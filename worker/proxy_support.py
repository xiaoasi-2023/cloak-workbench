"""Loopback HTTP relay for Chromium. Upstream credentials never enter process args."""
from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from http.client import HTTPSConnection
import ipaddress
import json
import select
import socket
import socketserver
import ssl
import threading
import time
from urllib.parse import unquote, urlsplit


class ProxyError(ValueError):
    """Messages are deliberately independent of configured credential values."""


@dataclass(frozen=True, repr=False)
class ProxyConfig:
    address: str
    scheme: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    upstream: ProxyConfig | None = None

    def __repr__(self):
        return f"ProxyConfig(protocol={self.scheme or 'direct'!r}, authenticated={bool(self.username or self.password)}, chained={self.upstream is not None})"


def parse_proxy(payload: dict) -> ProxyConfig:
    terminal = _parse_single_proxy(payload)
    upstream = _parse_single_proxy({
        "proxy": payload.get("upstreamProxy", ""),
        "proxyUsername": payload.get("upstreamProxyUsername", ""),
        "proxyPassword": payload.get("upstreamProxyPassword", ""),
        "clearProxyPassword": payload.get("clearUpstreamProxyPassword", False),
    })
    if upstream.address and not terminal.address:
        raise ProxyError("An upstream proxy requires a terminal proxy")
    return replace(terminal, upstream=upstream if upstream.address else None)


def _parse_single_proxy(payload: dict) -> ProxyConfig:
    value = payload.get("proxy", "")
    if not isinstance(value, str) or len(value) > 2048:
        raise ProxyError("Invalid proxy address")
    value = value.strip()
    username, password = payload.get("proxyUsername", ""), payload.get("proxyPassword", "")
    if not all(isinstance(x, str) and len(x.encode("utf-8")) <= 1024 and not any(ord(c) < 32 for c in x) for x in (username, password)):
        raise ProxyError("Invalid proxy username or password")
    if not value:
        return ProxyConfig("")
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ProxyError("Invalid proxy address")
    # Providers commonly export user:password@host:port or host:port. Port 443
    # is also used by plain HTTP proxies, so never infer TLS from the port.
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlsplit(value)
        port, host = parsed.port, parsed.hostname
    except ValueError:
        raise ProxyError("Invalid proxy host or port") from None
    if parsed.scheme not in ("http", "https", "socks5", "socks5h") or not host or not port:
        raise ProxyError("Proxy requires http://, https:// or socks5://host:port")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProxyError("Invalid proxy address")
    if parsed.username is not None:
        username, password = unquote(parsed.username), unquote(parsed.password or "")
        if not all(len(x.encode("utf-8")) <= 1024 and not any(ord(c) < 32 for c in x) for x in (username, password)):
            raise ProxyError("Invalid proxy username or password")
    if payload.get("clearProxyPassword") is True:
        password = ""
    scheme = "socks5" if parsed.scheme == "socks5h" else parsed.scheme
    if scheme == "socks5" and any(len(x.encode("utf-8")) > 255 for x in (username, password)):
        raise ProxyError("SOCKS5 credentials must each fit 255 UTF-8 bytes")
    if password and not username or ":" in username and parsed.scheme in ("http", "https"):
        raise ProxyError("Proxy password requires a username")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ProxyError("Invalid proxy host") from None
    authority = f"[{host}]" if ":" in host else host
    return ProxyConfig(f"{scheme}://{authority}:{port}", scheme, host, port, username, password)


def receive_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ProxyError("Proxy closed its connection during negotiation")
        result.extend(chunk)
    return bytes(result)


def receive_head(sock):
    result = bytearray()
    # Reading only the header leaves tunnel/application bytes in the socket.
    while not result.endswith(b"\r\n\r\n"):
        result.extend(receive_exact(sock, 1))
        if len(result) > 65536:
            raise ProxyError("Proxy HTTP headers exceed 64 KiB")
    return bytes(result)


def authority(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def destination(value, default_port=None):
    try:
        parsed = urlsplit("//" + value)
        host, port = parsed.hostname, parsed.port or default_port
    except ValueError:
        raise ProxyError("Invalid tunnel destination") from None
    if not host or not port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ProxyError("Invalid tunnel destination")
    return host, port


class ProxyRelay:
    """Owns a bounded local listener and its sockets. close() releases everything."""
    def __init__(self, config: ProxyConfig):
        if not config.address:
            raise ProxyError("Direct connections do not need a relay")
        self.config = config
        self._sockets = set()
        self._lock = threading.Lock()
        self._closed = False
        self._slots = threading.BoundedSemaphore(64)
        self._upstream_relay = None
        if config.upstream:
            if config.upstream.upstream or not config.upstream.address:
                raise ProxyError("Only one upstream proxy is supported")
            # Terminate the first hop in its own local relay. The terminal hop
            # receives a raw socket, so HTTPS-over-HTTPS never wraps an SSLSocket
            # in another SSL layer and bypasses the first hop's encryption.
            self._upstream_relay = ProxyRelay(config.upstream)
        relay = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not relay._slots.acquire(blocking=False):
                    return
                try:
                    relay._handle(self.request)
                finally:
                    relay._slots.release()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False
            request_queue_size = 64

        try:
            self.server = Server(("127.0.0.1", 0), Handler)
            self.port = self.server.server_address[1]
            self.address = f"http://127.0.0.1:{self.port}"
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
            self.thread.start()
        except Exception:
            if hasattr(self, "server"):
                self.server.server_close()
            if self._upstream_relay:
                self._upstream_relay.close()
            raise

    def _track(self, sock):
        with self._lock:
            if self._closed:
                sock.close()
                raise ProxyError("Proxy relay is closed")
            self._sockets.add(sock)
        return sock

    def _upstream(self, host, port, tunnel):
        config = self.config
        first_hop = self._upstream_relay
        route = ("127.0.0.1", first_hop.port) if first_hop else (config.host, config.port)
        upstream = self._track(socket.create_connection(route, timeout=8))
        try:
            if first_hop:
                target = authority(config.host, config.port)
                upstream.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
                self._check_connect_response(upstream)
            if config.scheme == "https":
                wrapped = ssl.create_default_context().wrap_socket(upstream, server_hostname=config.host)
                with self._lock:
                    self._sockets.discard(upstream)
                upstream = self._track(wrapped)
            if config.scheme == "socks5":
                authenticated = bool(config.username or config.password)
                upstream.sendall(b"\x05\x01" + (b"\x02" if authenticated else b"\x00"))
                reply = receive_exact(upstream, 2)
                if reply != (b"\x05\x02" if authenticated else b"\x05\x00"):
                    raise ProxyError("SOCKS5 proxy rejected the authentication method")
                if authenticated:
                    username, password = config.username.encode(), config.password.encode()
                    upstream.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
                    if receive_exact(upstream, 2) != b"\x01\x00":
                        raise ProxyError("SOCKS5 proxy authentication failed")
                try:
                    address = ipaddress.ip_address(host)
                    encoded = (b"\x01" if address.version == 4 else b"\x04") + address.packed
                except ValueError:
                    encoded_host = host.encode("idna")
                    if len(encoded_host) > 255:
                        raise ProxyError("SOCKS5 destination is too long")
                    encoded = b"\x03" + bytes([len(encoded_host)]) + encoded_host
                upstream.sendall(b"\x05\x01\x00" + encoded + port.to_bytes(2, "big"))
                reply = receive_exact(upstream, 4)
                if reply[:3] != b"\x05\x00\x00":
                    raise ProxyError("SOCKS5 proxy could not connect to the target")
                if reply[3] == 1:
                    receive_exact(upstream, 6)
                elif reply[3] == 4:
                    receive_exact(upstream, 18)
                elif reply[3] == 3:
                    receive_exact(upstream, receive_exact(upstream, 1)[0] + 2)
                else:
                    raise ProxyError("SOCKS5 proxy returned an invalid address")
            elif tunnel:
                target = authority(host, port)
                auth = self._auth_header()
                upstream.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n".encode("ascii") + auth + b"\r\n")
                self._check_connect_response(upstream)
            return upstream
        except Exception:
            self._untrack_close(upstream)
            raise

    @staticmethod
    def _check_connect_response(sock):
        head = receive_head(sock)
        parts = head.split(b"\r\n", 1)[0].split()
        if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or not parts[1].isdigit():
            raise ProxyError("HTTP proxy returned an invalid response")
        status = int(parts[1])
        if status == 407:
            raise ProxyError("HTTP proxy authentication failed")
        if status < 200 or status >= 300:
            raise ProxyError("HTTP proxy could not connect to the target")

    def _auth_header(self):
        if not (self.config.username or self.config.password):
            return b""
        encoded = base64.b64encode((self.config.username + ":" + self.config.password).encode("utf-8"))
        return b"Proxy-Authorization: Basic " + encoded + b"\r\n"

    def _handle(self, client):
        upstream = None
        replied = False
        try:
            self._track(client)
            client.settimeout(8)
            head = receive_head(client)
            lines = head[:-4].split(b"\r\n")
            request = lines[0].decode("ascii").split()
            if len(request) != 3 or request[2] not in ("HTTP/1.0", "HTTP/1.1"):
                raise ProxyError("Invalid HTTP proxy request")
            method, target, _ = request
            if method == "CONNECT":
                host, port = destination(target)
                upstream = self._upstream(host, port, True)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                replied = True
            else:
                parsed = urlsplit(target)
                if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
                    raise ProxyError("Proxy expects an absolute HTTP URL or CONNECT")
                try:
                    port = parsed.port or 80
                except ValueError:
                    raise ProxyError("Invalid target port") from None
                upstream = self._upstream(parsed.hostname, port, False)
                headers = []
                for line in lines[1:]:
                    if b":" not in line:
                        raise ProxyError("Invalid HTTP request header")
                    name = line.split(b":", 1)[0].strip().lower()
                    if name not in (b"proxy-authorization", b"proxy-connection", b"connection"):
                        headers.append(line)
                # One request per tunnel avoids leaking upstream auth into origin traffic.
                headers.append(b"Connection: close")
                if self.config.scheme != "socks5":
                    auth = self._auth_header().rstrip(b"\r\n")
                    if auth:
                        headers.append(auth)
                else:
                    target = parsed.path or "/"
                    if parsed.query:
                        target += "?" + parsed.query
                upstream.sendall(f"{method} {target} HTTP/1.1\r\n".encode("ascii") + b"\r\n".join(headers) + b"\r\n\r\n")
                replied = True
            client.settimeout(15)
            upstream.settimeout(15)
            self._bridge(client, upstream)
        except Exception:
            if not replied:
                try:
                    client.sendall(b"HTTP/1.1 502 Proxy connection failed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
        finally:
            self._untrack_close(client)
            if upstream:
                self._untrack_close(upstream)

    def _bridge(self, client, upstream):
        last_activity = time.monotonic()
        while not self._closed and time.monotonic() - last_activity < 120:
            pending = isinstance(upstream, ssl.SSLSocket) and upstream.pending()
            readable = [upstream] if pending else select.select([client, upstream], [], [], 1)[0]
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (upstream if source is client else client).sendall(data)
                last_activity = time.monotonic()

    def _untrack_close(self, sock):
        with self._lock:
            self._sockets.discard(sock)
        try:
            sock.close()
        except OSError:
            pass

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._sockets)
            self._sockets.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self._upstream_relay:
            self._upstream_relay.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# Fixed endpoints only. Never accept a user URL or follow redirects for this probe.
IP_ENDPOINTS = ("https://api.ipify.org?format=json", "https://api64.ipify.org?format=json")


def test_proxy(config: ProxyConfig) -> dict:
    started = time.monotonic()
    result = {"reachable": False, "egressIp": None, "latencyMs": None,
              "protocol": config.scheme or "direct", "mode": "proxy" if config.address else "direct",
              "error": None, "endpoint": None}
    relay = None
    try:
        if config.address:
            relay = ProxyRelay(config)
        # One 8 second attempt per fixed endpoint keeps the API below its 20 sec budget.
        for endpoint in IP_ENDPOINTS:
            connection = None
            try:
                target = urlsplit(endpoint)
                remaining = 16 - (time.monotonic() - started)
                if remaining <= 0:
                    break
                # Explicit CONNECT avoids system proxy/no_proxy settings silently bypassing
                # the configured route and displaying a direct IP as a successful proxy test.
                connection = HTTPSConnection("127.0.0.1" if relay else target.hostname,
                                             relay.port if relay else 443, timeout=min(8, remaining))
                if relay:
                    connection.set_tunnel(target.hostname, 443)
                connection.request("GET", (target.path or "/") + ("?" + target.query if target.query else ""),
                                   headers={"Accept": "application/json", "Connection": "close"})
                response = connection.getresponse()
                if response.status != 200:
                    raise ProxyError("IP service returned a non-success response")
                body = response.read(4097)
                if len(body) > 4096:
                    raise ProxyError("IP service response exceeds its size limit")
                value = json.loads(body)
                ip = str(ipaddress.ip_address(value["ip"]))
                result.update(reachable=True, egressIp=ip, endpoint=endpoint,
                              latencyMs=round((time.monotonic() - started) * 1000))
                break
            except Exception:
                result["error"] = "Unable to reach the HTTPS IP service through the configured route; check proxy, authentication, DNS and TLS"
            finally:
                if connection:
                    connection.close()
        if result["reachable"]:
            result["error"] = None
    except Exception:
        result["error"] = "Unable to initialize the proxy route"
    finally:
        if relay:
            relay.close()
    return result

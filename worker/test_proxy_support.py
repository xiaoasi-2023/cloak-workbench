"""Local wire-level proxy auth/tunnelling regressions. No external accounts or URLs."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import socketserver
import select
import ssl
import threading
import unittest
from unittest.mock import patch

from proxy_support import ProxyConfig, ProxyError, ProxyRelay, parse_proxy, receive_exact, receive_head, test_proxy
from tls_test_fixture import tls_fixture_directory


class LocalServer:
    def __init__(self, handler, http=False):
        cls = ThreadingHTTPServer if http else socketserver.ThreadingTCPServer
        self.server = cls(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ProxyTests(unittest.TestCase):
    def test_provider_auth_exports_keep_routing_username_and_explicit_protocol(self):
        username = "fixture-region-US-asn-AS64500-session-stable"
        for exported, protocol in ((username + ":fixture%40secret@gateway.invalid:443", "http"),
                                   ("http://" + username + ":fixture%40secret@gateway.invalid:443", "http"),
                                   ("https://" + username + ":fixture%40secret@gateway.invalid:443", "https"),
                                   ("socks5://" + username + ":fixture%40secret@gateway.invalid:443", "socks5")):
            with self.subTest(protocol=protocol, prefixed="://" in exported):
                config = parse_proxy({"proxy": exported, "upstreamProxy": "127.0.0.1:7897"})
                self.assertEqual(config.username, username)
                self.assertEqual(config.password, "fixture@secret")
                self.assertEqual(config.scheme, protocol)
                self.assertEqual(config.address, protocol + "://gateway.invalid:443")
                self.assertEqual(config.upstream.address, "http://127.0.0.1:7897")
                self.assertNotIn("fixture@secret", repr(config))

    def test_upstream_parse_credentials_clear_and_validation(self):
        config = parse_proxy({"proxy": "socks5h://terminal-user:terminal-secret@terminal.invalid:1080",
                              "upstreamProxy": "http://hop-user:hop-secret@127.0.0.1:7897"})
        self.assertEqual(config.scheme, "socks5")
        self.assertEqual(config.address, "socks5://terminal.invalid:1080")
        self.assertEqual(config.upstream.address, "http://127.0.0.1:7897")
        self.assertEqual(config.upstream.password, "hop-secret")
        self.assertNotIn("secret", repr(config))
        self.assertNotIn("secret", repr(config.upstream))
        cleared = parse_proxy({"proxy": "http://u:p@terminal.invalid:80",
                               "upstreamProxy": "https://hop:password@127.0.0.1:443",
                               "clearUpstreamProxyPassword": True})
        self.assertEqual(cleared.password, "p")
        self.assertEqual(cleared.upstream.password, "")
        for payload in ({"upstreamProxy": "http://127.0.0.1:7897"},
                        {"proxy": "http://terminal.invalid:80", "upstreamProxy": "ftp://bad.invalid:21"},
                        {"proxy": "http://terminal.invalid:80", "upstreamProxyPassword": "x\r\ny"}):
            with self.subTest(payload=payload), self.assertRaises(ProxyError):
                parse_proxy(payload)

    def test_two_hop_all_protocol_combinations_auth_dns_tls_and_shutdown(self):
        fixtures = tls_fixture_directory()
        tls_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_server.load_cert_chain(fixtures / "LOCAL_TEST_ONLY_cert.pem", fixtures / "LOCAL_TEST_ONLY_key.pem")
        verified = ssl.create_default_context(cafile=str(fixtures / "LOCAL_TEST_ONLY_cert.pem"))
        # An actual forwarding first hop maps the deliberately unresolvable
        # terminal hostname. Only the first hop may resolve the terminal name.
        for first_scheme in ("http", "https", "socks5"):
            for terminal_scheme in ("http", "https", "socks5"):
                seen = {"hop": [], "terminal": [], "dial": []}
                test = self

                def negotiate(sock, scheme, key, expected_user, expected_password):
                    if scheme == "socks5":
                        test.assertEqual(receive_exact(sock, 3), b"\x05\x01\x02")
                        sock.sendall(b"\x05\x02")
                        test.assertEqual(receive_exact(sock, 1), b"\x01")
                        user = receive_exact(sock, receive_exact(sock, 1)[0])
                        password = receive_exact(sock, receive_exact(sock, 1)[0])
                        test.assertEqual((user, password), (expected_user.encode(), expected_password.encode()))
                        sock.sendall(b"\x01\x00")
                        head = receive_exact(sock, 4)
                        test.assertEqual(head[:3], b"\x05\x01\x00")
                        if head[3] == 3:
                            host = receive_exact(sock, receive_exact(sock, 1)[0]).decode()
                        elif head[3] == 1:
                            host = socket.inet_ntop(socket.AF_INET, receive_exact(sock, 4))
                        else:
                            raise AssertionError("Unexpected address type")
                        port = int.from_bytes(receive_exact(sock, 2), "big")
                        seen[key].append((host, port, user, password))
                    else:
                        head = receive_head(sock)
                        target = head.split(b" ", 2)[1].decode()
                        host, port_text = target.rsplit(":", 1)
                        port = int(port_text)
                        auth = b"Basic " + base64.b64encode(f"{expected_user}:{expected_password}".encode())
                        test.assertIn(b"Proxy-Authorization: " + auth + b"\r\n", head)
                        seen[key].append((host, port, head))
                    return host, port

                def accept(sock, scheme):
                    sock.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x12\x34" if scheme == "socks5"
                                 else b"HTTP/1.1 200 OK\r\n\r\n")

                class Terminal(socketserver.BaseRequestHandler):
                    def handle(self):
                        sock = tls_server.wrap_socket(self.request, server_side=True) if terminal_scheme == "https" else self.request
                        try:
                            sock.settimeout(3)
                            test.assertEqual(negotiate(sock, terminal_scheme, "terminal", "terminal-user", "terminal-password"),
                                             ("destination.invalid", 443))
                            accept(sock, terminal_scheme)
                            sock.sendall(receive_exact(sock, 7))
                        finally:
                            sock.close()

                with self.subTest(first=first_scheme, terminal=terminal_scheme), LocalServer(Terminal) as terminal:
                    terminal_host = "127.0.0.1" if terminal_scheme == "https" else "never-resolve-terminal.invalid"

                    class FirstHop(socketserver.BaseRequestHandler):
                        def handle(self):
                            sock = tls_server.wrap_socket(self.request, server_side=True) if first_scheme == "https" else self.request
                            remote = None
                            try:
                                sock.settimeout(3)
                                test.assertEqual(negotiate(sock, first_scheme, "hop", "hop-user", "hop-password"),
                                                 (terminal_host, terminal.port))
                                remote = socket.create_connection(("127.0.0.1", terminal.port), timeout=3)
                                accept(sock, first_scheme)
                                while True:
                                    pending = isinstance(sock, ssl.SSLSocket) and sock.pending()
                                    readable = [sock] if pending else select.select([sock, remote], [], [], 3)[0]
                                    if not readable:
                                        return
                                    for source in readable:
                                        data = source.recv(65536)
                                        if not data:
                                            return
                                        (remote if source is sock else sock).sendall(data)
                            finally:
                                if remote:
                                    remote.close()
                                sock.close()

                    with LocalServer(FirstHop) as first:
                        config = parse_proxy({"proxy": f"{terminal_scheme}://terminal-user:terminal-password@{terminal_host}:{terminal.port}",
                                              "upstreamProxy": f"{first_scheme}://hop-user:hop-password@127.0.0.1:{first.port}"})
                        original_dial = socket.create_connection

                        def safe_dial(address, *args, **kwargs):
                            seen["dial"].append(address)
                            test.assertEqual(address[0], "127.0.0.1", "Terminal or target DNS was attempted locally")
                            return original_dial(address, *args, **kwargs)

                        with patch("proxy_support.ssl.create_default_context", return_value=verified), \
                                patch("proxy_support.socket.create_connection", side_effect=safe_dial):
                            with ProxyRelay(config) as relay:
                                first_relay = relay._upstream_relay
                                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                                    client.sendall(b"CONNECT destination.invalid:443 HTTP/1.1\r\n\r\n")
                                    self.assertIn(b"200", receive_head(client))
                                    client.sendall(b"payload")
                                    self.assertEqual(receive_exact(client, 7), b"payload")
                        self.assertEqual(len(seen["hop"]), 1)
                        self.assertEqual(len(seen["terminal"]), 1)
                        if first_scheme != "socks5":
                            self.assertNotIn(base64.b64encode(b"terminal-user:terminal-password"), seen["hop"][0][2])
                        if terminal_scheme != "socks5":
                            self.assertNotIn(base64.b64encode(b"hop-user:hop-password"), seen["terminal"][0][2])
                        for closed in (relay, first_relay):
                            self.assertFalse(closed.thread.is_alive())
                            self.assertFalse(closed._sockets)
                            with self.assertRaises(OSError):
                                socket.create_connection(("127.0.0.1", closed.port), timeout=.2)
        self.assertEqual(verified.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(verified.check_hostname)

    def test_two_hop_upstream_failure_never_falls_back_to_terminal(self):
        seen = []
        class Reject(socketserver.BaseRequestHandler):
            def handle(self):
                seen.append(receive_head(self.request))
                self.request.sendall(b"HTTP/1.1 407 secret-detail\r\n\r\n")
        with LocalServer(Reject) as first:
            config = parse_proxy({"proxy": "http://terminal-user:terminal-secret@should-not-dial.invalid:3128",
                                  "upstreamProxy": f"http://hop-user:hop-secret@127.0.0.1:{first.port}"})
            original_dial = socket.create_connection
            destinations = []
            def only_local(address, *args, **kwargs):
                destinations.append(address)
                self.assertEqual(address[0], "127.0.0.1")
                return original_dial(address, *args, **kwargs)
            with patch("proxy_support.socket.create_connection", side_effect=only_local):
                with ProxyRelay(config) as relay:
                    with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                        client.sendall(b"CONNECT destination.invalid:443 HTTP/1.1\r\n\r\n")
                        response = receive_head(client)
                        self.assertIn(b"502", response)
                        self.assertNotIn(b"secret", response)
            self.assertEqual(len(seen), 1)
            self.assertEqual(len(destinations), 3)  # client -> relay -> intermediary -> first hop

    def test_two_hop_constructor_failure_closes_owned_relay(self):
        created_threads = []
        original_start = threading.Thread.start
        def fail_second(thread):
            created_threads.append(thread)
            if len(created_threads) == 2:
                raise RuntimeError("Synthetic terminal relay startup failure")
            return original_start(thread)
        config = parse_proxy({"proxy": "http://terminal.invalid:3128", "upstreamProxy": "http://127.0.0.1:7897"})
        with patch("proxy_support.threading.Thread.start", new=fail_second):
            with self.assertRaises(RuntimeError):
                ProxyRelay(config)
        self.assertEqual(len(created_threads), 2)
        self.assertFalse(created_threads[0].is_alive())

    def test_https_upstream_verified_tls_connect_and_basic_auth(self):
        fixtures = tls_fixture_directory()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(fixtures / "LOCAL_TEST_ONLY_cert.pem", fixtures / "LOCAL_TEST_ONLY_key.pem")
        verified = ssl.create_default_context(cafile=str(fixtures / "LOCAL_TEST_ONLY_cert.pem"))
        seen = []
        class TLSConnect(socketserver.BaseRequestHandler):
            def handle(self):
                with context.wrap_socket(self.request, server_side=True) as sock:
                    seen.append(receive_head(sock))
                    sock.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                    sock.sendall(receive_exact(sock, 7))
        with LocalServer(TLSConnect) as upstream:
            with patch("proxy_support.ssl.create_default_context", return_value=verified):
                with ProxyRelay(parse_proxy({"proxy": f"https://tls-user:tls-pass@127.0.0.1:{upstream.port}"})) as relay:
                    with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                        client.sendall(b"CONNECT target.invalid:443 HTTP/1.1\r\n\r\n")
                        self.assertIn(b"200", receive_head(client))
                        client.sendall(b"payload")
                        self.assertEqual(receive_exact(client, 7), b"payload")
        self.assertIn(b"Proxy-Authorization: Basic dGxzLXVzZXI6dGxzLXBhc3M=", seen[0])
        self.assertEqual(verified.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(verified.check_hostname)

    def test_validation_and_credential_free_canonical_address(self):
        config = parse_proxy({"proxy": "socks5://user:p%40ss@[::1]:1080/"})
        self.assertEqual(config.address, "socks5://[::1]:1080")
        self.assertEqual(config.password, "p@ss")
        self.assertNotIn("p@ss", repr(config))
        for payload in ({"proxy": "http://host:0"}, {"proxy": "http://host:65536"},
                        {"proxy": "ftp://host:21"}, {"proxy": "http://host:80/path"},
                        {"proxy": "socks5://host:1080", "proxyUsername": "u", "proxyPassword": "x" * 256},
                        {"proxy": "http://host:80", "proxyUsername": "x\r\ny"},
                        {"proxy": "http://host:80", "proxyPassword": "without-user"}):
            with self.subTest(payload=payload), self.assertRaises(ProxyError):
                parse_proxy(payload)

    def test_http_auth_absolute_request_and_credential_header_is_replaced(self):
        seen = []
        class HTTPProxy(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                seen.append((self.path, self.headers.get("Proxy-Authorization"), self.headers.get("Connection")))
                self.send_response(200)
                self.send_header("Content-Length", "6")
                self.end_headers()
                self.wfile.write(b"routed")
        with LocalServer(HTTPProxy, http=True) as upstream:
            config = parse_proxy({"proxy": f"http://127.0.0.1:{upstream.port}", "proxyUsername": "fixture-user", "proxyPassword": "fixture-pass"})
            with ProxyRelay(config) as relay:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"GET http://example.invalid/path?q=1 HTTP/1.1\r\nHost: example.invalid\r\nProxy-Authorization: malicious-header\r\n\r\n")
                    self.assertIn(b"200", receive_head(client))
                    self.assertEqual(receive_exact(client, 6), b"routed")
            expected = "Basic " + base64.b64encode(b"fixture-user:fixture-pass").decode()
            self.assertEqual(seen, [("http://example.invalid/path?q=1", expected, "close")])
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", relay.port), timeout=.2)
            self.assertFalse(relay._sockets)

    def test_http_authenticated_connect_preserves_tunnel_bytes(self):
        seen = []
        class ConnectProxy(socketserver.BaseRequestHandler):
            def handle(self):
                head = receive_head(self.request)
                seen.append(head)
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.request.sendall(receive_exact(self.request, 7))
        with LocalServer(ConnectProxy) as upstream:
            with ProxyRelay(parse_proxy({"proxy": f"http://u:p@127.0.0.1:{upstream.port}"})) as relay:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"CONNECT target.invalid:443 HTTP/1.1\r\nHost: target.invalid\r\n\r\n")
                    self.assertIn(b"200", receive_head(client))
                    client.sendall(b"payload")
                    self.assertEqual(receive_exact(client, 7), b"payload")
        self.assertIn(b"CONNECT target.invalid:443", seen[0])
        self.assertIn(b"Proxy-Authorization: Basic dTpw", seen[0])

    def test_http_auth_rejection_is_sanitized(self):
        class Reject(socketserver.BaseRequestHandler):
            def handle(self):
                receive_head(self.request)
                self.request.sendall(b"HTTP/1.1 407 secret-upstream-detail\r\n\r\n")
        with LocalServer(Reject) as upstream:
            with ProxyRelay(parse_proxy({"proxy": f"http://u:super-secret@127.0.0.1:{upstream.port}"})) as relay:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"CONNECT target.invalid:443 HTTP/1.1\r\n\r\n")
                    response = receive_head(client)
                    self.assertIn(b"502", response)
                    self.assertNotIn(b"secret", response)

    def test_socks5_username_password_and_remote_hostname(self):
        seen = []
        class Socks(socketserver.BaseRequestHandler):
            def handle(self):
                sock = self.request
                seen.append(receive_exact(sock, 3))
                sock.sendall(b"\x05\x02")
                self.assert_version = receive_exact(sock, 1)
                username = receive_exact(sock, receive_exact(sock, 1)[0])
                password = receive_exact(sock, receive_exact(sock, 1)[0])
                seen.append((username, password))
                sock.sendall(b"\x01\x00")
                header = receive_exact(sock, 4)
                host = receive_exact(sock, receive_exact(sock, 1)[0])
                port = int.from_bytes(receive_exact(sock, 2), "big")
                seen.append((header, host, port))
                sock.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x12\x34")
                sock.sendall(receive_exact(sock, 7))
        with LocalServer(Socks) as upstream:
            with ProxyRelay(parse_proxy({"proxy": f"socks5://u:p@127.0.0.1:{upstream.port}"})) as relay:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"CONNECT never-resolve-locally.invalid:443 HTTP/1.1\r\n\r\n")
                    self.assertIn(b"200", receive_head(client))
                    client.sendall(b"payload")
                    self.assertEqual(receive_exact(client, 7), b"payload")
        self.assertEqual(seen, [b"\x05\x01\x02", (b"u", b"p"), (b"\x05\x01\x00\x03", b"never-resolve-locally.invalid", 443)])

    def test_socks5_unauthenticated_http_target_and_auth_rejection(self):
        for reject in (False, True):
            seen = []
            class Socks(socketserver.BaseRequestHandler):
                def handle(self):
                    sock = self.request
                    seen.append(receive_exact(sock, 3))
                    sock.sendall(b"\x05\xff" if reject else b"\x05\x00")
                    if reject:
                        return
                    receive_exact(sock, 4)
                    receive_exact(sock, receive_exact(sock, 1)[0] + 2)
                    sock.sendall(b"\x05\x00\x00\x03\x01x\x12\x34")
                    seen.append(receive_head(sock))
                    sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            with self.subTest(reject=reject), LocalServer(Socks) as upstream:
                with ProxyRelay(parse_proxy({"proxy": f"socks5://127.0.0.1:{upstream.port}"})) as relay:
                    with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                        client.sendall(b"GET http://target.invalid/a?b=2 HTTP/1.1\r\nHost: target.invalid\r\n\r\n")
                        response = receive_head(client)
                        self.assertIn(b"502" if reject else b"200", response)
                        if not reject:
                            self.assertEqual(receive_exact(client, 2), b"ok")
                            self.assertTrue(seen[1].startswith(b"GET /a?b=2 HTTP/1.1"))
                            self.assertNotIn(b"Proxy-Authorization", seen[1])
                self.assertEqual(seen[0], b"\x05\x01\x00")

    def test_malformed_client_request_and_shutdown_active_tunnel(self):
        class Waiting(socketserver.BaseRequestHandler):
            def handle(self):
                receive_head(self.request)
                self.request.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                self.request.recv(1)
        with LocalServer(Waiting) as upstream:
            relay = ProxyRelay(parse_proxy({"proxy": f"http://127.0.0.1:{upstream.port}"}))
            try:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"CONNECT bad:port HTTP/1.1\r\n\r\n")
                    self.assertIn(b"502", receive_head(client))
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(b"CONNECT target.invalid:443 HTTP/1.1\r\n\r\n")
                    receive_head(client)
                    relay.close()
                    self.assertEqual(client.recv(1), b"")
            finally:
                relay.close()

    def test_egress_probe_validates_ip_and_never_accepts_custom_urls(self):
        class Response:
            status = 200
            def read(self, limit): return b'{"ip":"203.0.113.44"}'
        class Connection:
            def request(self, method, path, headers): self.path = path
            def getresponse(self): return Response()
            def close(self): pass
        connection = Connection()
        with patch("proxy_support.HTTPSConnection", return_value=connection) as factory:
            result = test_proxy(ProxyConfig(""))
        self.assertTrue(result["reachable"])
        self.assertEqual(result["egressIp"], "203.0.113.44")
        self.assertEqual(result["mode"], "direct")
        self.assertEqual(factory.call_args.args[0], "api.ipify.org")
        self.assertEqual(connection.path, "/?format=json")
        with patch("proxy_support.HTTPSConnection", side_effect=OSError("credential must never leak")):
            result = test_proxy(ProxyConfig(""))
        self.assertFalse(result["reachable"])
        self.assertNotIn("credential", json.dumps(result))

    def test_proxy_egress_uses_explicit_connect_even_with_no_proxy_env(self):
        class Response:
            status = 200
            def read(self, limit): return b'{"ip":"203.0.113.45"}'
        class Connection:
            def set_tunnel(self, host, port): self.tunnel = (host, port)
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        connection = Connection()
        with patch.dict("os.environ", {"NO_PROXY": "*", "no_proxy": "*"}):
            with patch("proxy_support.HTTPSConnection", return_value=connection) as factory:
                result = test_proxy(parse_proxy({"proxy": "http://u:p@127.0.0.1:3128"}))
        self.assertTrue(result["reachable"])
        self.assertEqual(result["mode"], "proxy")
        self.assertEqual(connection.tunnel, ("api.ipify.org", 443))
        self.assertEqual(factory.call_args.args[0], "127.0.0.1")
        self.assertNotEqual(factory.call_args.args[1], 3128)  # Local ephemeral relay, never direct upstream.


if __name__ == "__main__":
    unittest.main(verbosity=2)

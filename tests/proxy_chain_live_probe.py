"""Probe fixed public IP services via two owned authenticated local proxy hops.

Uses synthetic credentials and no user proxy configuration. The terminal fixture
allows only three fixed public HTTPS hosts; the upstream allows only its gateway.
"""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
from proxy_support import parse_proxy
from proxy_diagnostics import diagnose_proxy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    observed = {"upstreamConnections": 0, "terminalTargets": []}
    allowed = {"api.ipify.org:443", "api64.ipify.org:443", "ipinfo.io:443", "my.ippure.com:443"}
    terminal_auth = "Basic " + base64.b64encode(b"fixture-terminal:fixture-terminal-secret").decode()
    upstream_auth = "Basic " + base64.b64encode(b"fixture-upstream:fixture-upstream-secret").decode()

    class Tunnel(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def pipe(self, target):
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            while True:
                ready, _, _ = select.select([self.connection, target], [], [], 12)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (target if source is self.connection else self.connection).sendall(data)

    class Terminal(Tunnel):
        def do_CONNECT(self):
            if self.headers.get("Proxy-Authorization") != terminal_auth:
                self.send_error(407)
                return
            if self.path not in allowed:
                self.send_error(502)
                return
            observed["terminalTargets"].append(self.path)
            try:
                with socket.create_connection((self.path.split(":")[0], 443), timeout=6) as target:
                    self.pipe(target)
            except OSError:
                self.close_connection = True

    terminal = ThreadingHTTPServer(("127.0.0.1", 0), Terminal)

    class Upstream(Tunnel):
        def do_CONNECT(self):
            if self.headers.get("Proxy-Authorization") != upstream_auth:
                self.send_error(407)
                return
            if self.path != "owned-gateway.invalid:3010":
                self.send_error(502)
                return
            observed["upstreamConnections"] += 1
            try:
                with socket.create_connection(("127.0.0.1", terminal.server_port), timeout=3) as target:
                    self.pipe(target)
            except OSError:
                self.close_connection = True

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    services = [terminal, upstream]
    threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": .05}, daemon=True) for s in services]
    for thread in threads:
        thread.start()
    try:
        result = diagnose_proxy(parse_proxy({"proxy": "http://owned-gateway.invalid:3010",
            "proxyUsername": "fixture-terminal", "proxyPassword": "fixture-terminal-secret",
            "upstreamProxy": f"http://127.0.0.1:{upstream.server_port}",
            "upstreamProxyUsername": "fixture-upstream", "upstreamProxyPassword": "fixture-upstream-secret"}))
        report = {"scope": "Synthetic two-hop route with live fixed public services; no commercial proxy credentials",
                  "doubleAuthentication": True, "remoteGatewayResolution": True,
                  "routeObserved": observed, "diagnostic": result}
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Do not print public IP/provider details unnecessarily to tool transcripts.
        print(json.dumps({"reachable": result["reachable"], "mode": result["mode"],
            "hasIsp": bool(result["isp"]), "hasAsn": bool(result["asn"]),
            "qualityComplete": result["qualityComplete"], "metadataError": result["metadataError"],
            "upstreamConnections": observed["upstreamConnections"], "terminalTargets": observed["terminalTargets"]}))
    finally:
        for service in services:
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=3)


if __name__ == "__main__":
    main()

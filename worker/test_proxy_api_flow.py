"""Authenticated API and real Chromium two-hop routing, isolated from user data."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from server import BrowserWorker, make_handler


@unittest.skipUnless(os.environ.get("CLOAK_WORKBENCH_REAL_TEST") == "1", "Opt-in real installed browser")
class CatalogBrowserFlow(unittest.TestCase):
    def test_catalog_binding_routes_real_browser_through_both_authenticated_hops(self):
        events = {"upstream": [], "terminal": []}
        expected_url = "http://catalog-browser-proof.invalid/owned-proof"
        upstream_auth = "Basic " + base64.b64encode(b"fixture-upstream:fixture-upstream-secret").decode()
        terminal_auth = "Basic " + base64.b64encode(b"fixture-terminal:fixture-terminal-secret").decode()

        class Terminal(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_CONNECT(self):
                self.send_error(502, "External traffic disabled in this fixture")

            def do_GET(self):
                if self.headers.get("Proxy-Authorization") != terminal_auth:
                    self.send_error(407)
                    return
                if self.path != expected_url:
                    self.send_error(502)
                    return
                events["terminal"].append(self.path)
                body = b'<!doctype html><title>Catalog chain proof</title><script>window.chainProof={route:"two-authenticated-hops",host:location.hostname};</script>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        terminal = ThreadingHTTPServer(("127.0.0.1", 0), Terminal)

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_CONNECT(self):
                if self.headers.get("Proxy-Authorization") != upstream_auth:
                    self.send_error(407)
                    return
                # This hostname cannot resolve locally. Only this upstream can map it.
                if self.path != "residential-gateway.invalid:3010":
                    self.send_error(502)
                    return
                events["upstream"].append(self.path)
                with socket.create_connection(("127.0.0.1", terminal.server_port), timeout=3) as target:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.flush()
                    self.close_connection = True
                    while True:
                        ready, _, _ = select.select([self.connection, target], [], [], 4)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            (target if source is self.connection else self.connection).sendall(data)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        binary = Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe"
        opener = build_opener(ProxyHandler({}))
        token = "isolated-fixture-api-token"
        with tempfile.TemporaryDirectory(prefix="cloak-catalog-api-") as directory:
            worker = BrowserWorker(Path(directory), binary)
            api = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, token))
            services = [terminal, upstream, api]
            threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": .05}, daemon=True) for s in services]
            for thread in threads:
                thread.start()

            def call(path, body=None):
                req = Request(f"http://127.0.0.1:{api.server_port}/{path}",
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                with opener.open(req, timeout=30) as response:
                    return json.load(response)

            try:
                payload = {"id": "fixture-chain", "name": "Isolated residential chain", "kind": "residential",
                           "proxy": "http://residential-gateway.invalid:3010", "proxyUsername": "fixture-terminal",
                           "proxyPassword": "fixture-terminal-secret", "upstreamProxy": f"http://127.0.0.1:{upstream.server_port}",
                           "upstreamProxyUsername": "fixture-upstream", "upstreamProxyPassword": "fixture-upstream-secret"}
                call("proxies", payload)
                inventory = call("proxies")["proxies"]
                self.assertEqual(len(inventory), 1)
                self.assertTrue(inventory[0]["proxyPasswordSet"])
                self.assertTrue(inventory[0]["upstreamProxyPasswordSet"])
                for secret in (payload["proxyPassword"], payload["upstreamProxyPassword"]):
                    self.assertNotIn(secret, json.dumps(inventory))
                call("profiles", {"id": "owned-chain-browser", "name": "Isolated chain browser", "proxyId": "fixture-chain",
                                  "startUrl": expected_url})
                self.assertEqual(call("proxies")["proxies"][0]["bindingCount"], 1)
                with self.assertRaises(HTTPError) as error:
                    call("proxies/fixture-chain/delete", {})
                self.assertEqual(error.exception.code, 409)
                call("profiles/owned-chain-browser/launch", {})
                deadline = time.monotonic() + 15
                proof = None
                while time.monotonic() < deadline:
                    try:
                        result = worker.command(worker.runtime["owned-chain-browser"], "Runtime.evaluate",
                                                {"expression": "window.chainProof", "returnByValue": True})
                        proof = result["result"].get("value")
                        if proof:
                            break
                    except Exception:
                        pass
                    time.sleep(.15)
                self.assertEqual(proof, {"route": "two-authenticated-hops", "host": "catalog-browser-proof.invalid"})
                self.assertIn(expected_url, events["terminal"])
                self.assertIn("residential-gateway.invalid:3010", events["upstream"])
                with self.assertRaises(HTTPError) as error:
                    call("proxies", {"id": "fixture-chain", "name": "Cannot change live route"})
                self.assertEqual(error.exception.code, 409)
                call("profiles/owned-chain-browser/close", {})
                # Switch back to direct, then deletion is safe and isolated.
                state = call("profiles", {"id": "owned-chain-browser", "proxyId": "", "proxy": ""})
                self.assertEqual(state["proxy"], "")
                call("proxies/fixture-chain/delete", {})
                self.assertEqual(call("proxies")["proxies"], [])
            finally:
                worker.shutdown()
                for service in services:
                    service.shutdown()
                    service.server_close()
                for thread in threads:
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

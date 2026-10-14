"""Meaningful isolation/auth/persistence checks; real-engine checks are opt-in."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from server import ApiError, BrowserWorker, make_handler, validate_profile, validate_url
from proxy_support import ProxyRelay


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path("missing-browser.exe"))

    def tearDown(self):
        self.worker.shutdown()
        self.temp.cleanup()

    def test_persistence_keeps_seed_on_partial_edit(self):
        created = self.worker.create({"id": "account-a", "name": "First"})
        changed = self.worker.create({"id": "account-a", "name": "Edited"})
        self.assertEqual(created["seed"], changed["seed"])
        restored = BrowserWorker(Path(self.temp.name), Path("missing-browser.exe"))
        self.assertEqual(restored.state("account-a")["name"], "Edited")
        self.assertEqual(restored.state("account-a")["seed"], created["seed"])

    def test_validation_rejects_path_traversal_and_invalid_settings(self):
        for payload in ({"id": "../elsewhere"}, {"proxy": "http://host:not-a-port"},
                        {"startUrl": "file:///C:/secret"}, {"timezone": "Imaginary/Zone"}, {"seed": True}):
            with self.subTest(payload=payload), self.assertRaises(ApiError):
                validate_profile(payload)

    def test_proxy_credentials_are_redacted_persistent_and_preserved_by_edits(self):
        state = self.worker.create({"id": "secret-test", "proxy": "http://test-user:test%40pass@127.0.0.1:3128"})
        self.assertEqual(state["proxy"], "http://127.0.0.1:3128")
        self.assertTrue(state["proxyPasswordSet"])
        self.assertNotIn("proxyPassword", state)
        self.assertNotIn("test@pass", json.dumps(state))
        self.worker.create({"id": "secret-test", "name": "Rename only"})
        self.assertEqual(self.worker.profile("secret-test")["proxyPassword"], "test@pass")
        restored = BrowserWorker(Path(self.temp.name), Path("missing-browser.exe"))
        self.assertTrue(restored.state("secret-test")["proxyPasswordSet"])
        self.worker.create({"id": "secret-test", "proxyPassword": "", "keepProxyPassword": True})
        self.assertTrue(self.worker.state("secret-test")["proxyPasswordSet"])
        self.worker.create({"id": "secret-test", "clearProxyPassword": True})
        self.assertFalse(self.worker.state("secret-test")["proxyPasswordSet"])

    def test_download_listing_is_scoped_and_labels_partial_files(self):
        self.worker.create({"id": "files-a"})
        directory = Path(self.worker.downloads("files-a")["directory"])
        (directory / "finished.txt").write_bytes(b"fixture")
        (directory / "active.crdownload").write_bytes(b"partial")
        files = self.worker.downloads("files-a")["files"]
        self.assertEqual({item["name"] for item in files}, {"finished.txt", "active.crdownload"})
        self.assertEqual({item["name"]: item["complete"] for item in files}, {"finished.txt": True, "active.crdownload": False})
        with self.assertRaises(ApiError):
            self.worker.downloads("../outside")

    def test_browser_launch_failure_releases_relay_lease_and_blocks_webrtc_udp(self):
        fake_binary = Path(self.temp.name) / "fake-browser.exe"
        fake_binary.write_bytes(b"test executable placeholder")
        self.worker.binary = fake_binary
        self.worker.create({"id": "failed-launch", "proxy": "http://127.0.0.1:3128", "proxyUsername": "u", "proxyPassword": "p"})
        relays = []
        def create_relay(config):
            relay = ProxyRelay(config)
            relays.append(relay)
            return relay
        with patch("server.ProxyRelay", side_effect=create_relay), patch("server.subprocess.Popen", side_effect=OSError("synthetic launch failure")) as launch:
            for _ in range(2):
                with self.assertRaises(OSError):
                    self.worker.launch("failed-launch")
                argv = launch.call_args.args[0]
                self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", argv)
                self.assertIn(f"--proxy-server={relays[-1].address}", argv)
                self.assertNotIn("--proxy-server=http://127.0.0.1:3128", argv)
                self.assertFalse(any("u:p@" in argument for argument in argv))
            self.worker.create({"id": "failed-direct-launch"})
            with self.assertRaises(OSError):
                self.worker.launch("failed-direct-launch")
            self.assertNotIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", launch.call_args.args[0])
        self.assertEqual(len(relays), 2)
        self.assertTrue(all(relay._closed and not relay.thread.is_alive() for relay in relays))
        self.assertFalse(self.worker.runtime)

    def test_unknown_profile_and_missing_browser_fail_without_launch(self):
        with self.assertRaises(ApiError) as error:
            self.worker.close("unknown")
        self.assertEqual(error.exception.status, 404)
        self.worker.create({"id": "account-a"})
        with self.assertRaises(ApiError) as error:
            self.worker.launch("account-a")
        self.assertEqual(error.exception.status, 503)
        self.assertFalse(self.worker.runtime)

    def test_http_auth_and_public_probe(self):
        token = "test-secret-not-for-production-12345"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.worker, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/profiles")
            self.assertEqual(error.exception.code, 401)
            with urlopen(base + "/probe") as response:
                self.assertIn(b"persistentMarker", response.read())
            request = Request(base + "/profiles", data=b'{"id":"account-a"}',
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["state"], "closed")
            request = Request(base + "/shutdown", data=b'{}', headers={"Authorization": "Bearer " + token})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["status"], "closed")
            request = Request(base + "/profiles", headers={"Authorization": "Bearer " + token, "Origin": "https://other.example"})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@unittest.skipUnless(os.environ.get("CLOAK_WORKBENCH_REAL_TEST") == "1", "Opt-in real installed browser test")
class RealBrowserTests(unittest.TestCase):
    def read_probe(self, worker, name):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                result = worker.command(worker.runtime[name], "Runtime.evaluate", {
                    "expression": "window.probeResult", "returnByValue": True})
                if "value" in result["result"]:
                    return result["result"]["value"]
            except ApiError:
                pass
            time.sleep(.15)
        self.fail("Probe page did not finish loading")

    def test_two_profiles_keep_independent_storage_and_stable_environment(self):
        binary = Path(os.environ.get("CLOAK_WORKBENCH_BROWSER", str(Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe")))
        with tempfile.TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), binary)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, "integration-secret-not-for-production"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                outputs = []
                for name, seed, locale, tz in (("a", 13579, "ja-JP", "Asia/Tokyo"), ("b", 24680, "en-US", "America/New_York")):
                    worker.create({"id": name, "seed": seed, "locale": locale, "timezone": tz,
                                   "startUrl": f"http://127.0.0.1:{server.server_port}/probe?profile={name}"})
                    state = worker.launch(name)
                    self.assertGreater(state["windowHandle"], 0)
                    result = self.read_probe(worker, name)
                    self.assertEqual(result["language"], locale)
                    self.assertEqual(result["timezone"], tz)
                    outputs.append(result)
                    worker.close(name)
                    worker.launch(name)
                    after = self.read_probe(worker, name)
                    self.assertEqual(after["persistentMarker"], result["persistentMarker"])
                    self.assertEqual(after["canvasSHA256"], result["canvasSHA256"])
                    worker.close(name)
                self.assertNotEqual(outputs[0]["persistentMarker"], outputs[1]["persistentMarker"])
                print("REAL_PROBE " + json.dumps([{k: v for k, v in item.items() if k != "userAgent"} for item in outputs]))
            finally:
                worker.shutdown()
                server.shutdown()
                server.server_close()
                thread.join()


@unittest.skipUnless(os.environ.get("CLOAK_WORKBENCH_REAL_TEST") == "1", "Opt-in real installed browser test")
class RealBrowserProxyTests(unittest.TestCase):
    def test_http_proxy_routes_synthetic_host_without_external_access(self):
        """The .invalid hostname avoids Chromium's automatic localhost bypass."""
        observed = []
        expected_url = "http://workbench-proxy-probe.invalid/owned-proxy-test"

        class RecordingProxy(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_CONNECT(self):
                # Block all HTTPS background traffic. Never create upstream sockets.
                self.send_error(502, "External access disabled for local test")

            def do_GET(self):
                if self.path != expected_url:
                    self.send_error(502, "Only the owned synthetic target is available")
                    return
                if self.headers.get("Proxy-Authorization") != "Basic dGVzdC11c2VyOnRlc3QtcGFzcw==":
                    self.send_error(407, "Proxy credentials required")
                    return
                observed.append(self.path)
                body = b'<!doctype html><title>Local proxy proof</title><script>window.proxyProof={via:"recording-local-proxy",host:location.hostname,path:location.pathname};</script><p>Local HTTP proxy reached.</p>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except ConnectionError:
                    pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), RecordingProxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        binary = Path(os.environ.get("CLOAK_WORKBENCH_BROWSER", str(Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe")))
        try:
            with tempfile.TemporaryDirectory() as directory:
                worker = BrowserWorker(Path(directory), binary)
                try:
                    worker.create({"id": "owned-proxy-test", "name": "Temporary local proxy proof", "seed": 31415,
                                   "proxy": f"http://127.0.0.1:{proxy.server_port}", "proxyUsername": "test-user",
                                   "proxyPassword": "test-pass", "startUrl": expected_url})
                    state = worker.launch("owned-proxy-test")
                    proof = None
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        try:
                            result = worker.command(worker.runtime["owned-proxy-test"], "Runtime.evaluate", {
                                "expression": "window.proxyProof", "returnByValue": True})
                            proof = result["result"].get("value")
                            if proof:
                                break
                        except ApiError:
                            pass
                        time.sleep(.15)
                    self.assertIn(expected_url, observed)
                    self.assertEqual(proof, {"via": "recording-local-proxy", "host": "workbench-proxy-probe.invalid", "path": "/owned-proxy-test"})
                    rtc = worker.command(worker.runtime["owned-proxy-test"], "Runtime.evaluate", {
                        "awaitPromise": True, "returnByValue": True, "expression": """(async()=>{
                          const pc=new RTCPeerConnection({iceServers:[]});pc.createDataChannel('owned-local-regression');
                          const candidates=[];let completed=false;
                          const done=new Promise(resolve=>{pc.onicecandidate=e=>{if(e.candidate)candidates.push(e.candidate.candidate);else{completed=true;resolve()}};setTimeout(resolve,3000)});
                          await pc.setLocalDescription(await pc.createOffer());await done;pc.close();
                          return {completed,candidates};})()"""})["result"]["value"]
                    self.assertTrue(rtc["completed"], "Local ICE gathering did not complete")
                    self.assertEqual(rtc["candidates"], [], "Proxy-enabled environment exposed a direct local ICE candidate")
                    print("REAL_PROXY " + json.dumps({"configuredProxy": f"http://127.0.0.1:{proxy.server_port}",
                          "recordedTarget": observed[0], "pageProof": proof, "browserState": state["state"],
                          "externalAccess": "blocked", "authentication": "HTTP Basic verified", "protocol": "HTTP",
                          "webRtcLocalIce": {"completed": rtc["completed"], "directCandidateCount": len(rtc["candidates"]), "publicStun": "not tested"}}))
                finally:
                    worker.shutdown()
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)

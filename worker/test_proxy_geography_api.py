"""Profile/editor diagnostics expose geography and preserve route credentials."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, ProxyHandler, build_opener

from server import BrowserWorker, make_handler, validate_profile, ApiError


class GeographyApiTests(unittest.TestCase):
    def test_profile_and_editor_share_full_diagnostics_and_save_catalog_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), Path("missing.exe"))
            worker.catalog.upsert({"id": "geo-route", "name": "Geo fixture", "kind": "residential",
                "proxy": "http://terminal.invalid:443", "proxyUsername": "fixture", "proxyPassword": "fixture-secret",
                "upstreamProxy": "http://127.0.0.1:7897"})
            worker.create({"id": "geo-profile", "proxyId": "geo-route", "locale": "ja-JP", "timezone": "Asia/Tokyo"})
            before = json.loads(worker.profile_file.read_text(encoding="utf-8"))
            route_before = dict(worker.catalog.entry("geo-route"))
            result = {"reachable": True, "country": "US", "countryCode": "US", "city": "Dallas",
                      "region": "Texas", "timezone": "America/Chicago", "egressIp": "203.0.113.1"}
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, "fixture-token"))
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
            thread.start()
            opener = build_opener(ProxyHandler({}))
            def call(path, payload):
                request = Request(f"http://127.0.0.1:{server.server_port}/{path}", data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer fixture-token", "Content-Type": "application/json"})
                with opener.open(request, timeout=5) as response:
                    return json.load(response)
            try:
                with patch("proxy_catalog.diagnose_proxy", return_value=result) as diagnose, patch("server.diagnose_proxy", return_value=result) as profile_diagnose:
                    self.assertEqual(call("profiles/geo-profile/proxy-test", {}), dict(result, source='profile_proxy_diagnostics'))
                    self.assertEqual(call("proxy/test", {"proxyId": "geo-route"}), result)
                self.assertEqual(diagnose.call_count, 1)
                self.assertEqual(profile_diagnose.call_count, 1)
                self.assertTrue(all(c.args[0].password == "fixture-secret" and c.args[0].upstream.host == "127.0.0.1" for c in diagnose.call_args_list + profile_diagnose.call_args_list))
                self.assertEqual(worker.accounts.details('geo-profile')['networkCheck'], dict(result, source='profile_proxy_diagnostics'))
                self.assertEqual(worker.catalog.entry("geo-route")["check"], result)
                self.assertEqual({k:v for k,v in worker.catalog.entry("geo-route").items() if k != "check"}, {k:v for k,v in route_before.items() if k != "check"})
                self.assertEqual(json.loads(worker.profile_file.read_text(encoding="utf-8")), before)
                state = call("profiles", {"id": "geo-profile", "locale": "en-US", "timezone": "America/Chicago", "regionMode": "proxy"})
                self.assertEqual((state["locale"], state["timezone"], state["regionMode"]), ("en-US", "America/Chicago", "proxy"))
                with patch("server.diagnose_proxy", return_value=result) as direct:
                    self.assertEqual(call("proxy/test", {}), result)
                self.assertFalse(direct.call_args.args[0].address)
            finally:
                worker.shutdown(); server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_invalid_region_mode_is_rejected(self):
        with self.assertRaises(ApiError):
            validate_profile({"regionMode": "invalid"})


if __name__ == "__main__":
    unittest.main()

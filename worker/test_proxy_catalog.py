"""Inventory persistence, binding safeguards and diagnostic route integrity."""
import json
from pathlib import Path
import socketserver
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import ApiError, BrowserWorker
from proxy_diagnostics import diagnose_proxy, request_json
from proxy_support import parse_proxy, receive_head
from tls_test_fixture import tls_fixture_directory


class ProxyCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path("missing.exe"))
        self.catalog = self.worker.catalog

    def tearDown(self):
        self.worker.shutdown()
        self.temp.cleanup()

    def proxy(self, pid="route"):
        return self.catalog.upsert({"id": pid, "name": "Fixture", "kind": "residential",
                                    "proxy": "http://user:fixture-secret@invalid.example:8080",
                                    "upstreamProxy": "http://up:upstream-secret@127.0.0.1:7897"})

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.catalog.snapshot(job_id)
            if value["state"] not in ("running", "cancelling"):
                return value
            time.sleep(.01)
        self.fail("Diagnostic job timed out")

    def test_secrets_preserved_redacted_and_explicitly_cleared(self):
        public = self.proxy()
        self.assertTrue(public["proxyPasswordSet"])
        self.assertTrue(public["upstreamProxyPasswordSet"])
        self.assertNotIn("fixture-secret", json.dumps(public))
        self.assertNotIn("upstream-secret", json.dumps(public))
        self.catalog.upsert({"id": "route", "name": "Renamed"})
        restored = BrowserWorker(Path(self.temp.name), Path("missing.exe"))
        self.assertEqual(restored.catalog.entry("route")["proxyPassword"], "fixture-secret")
        self.catalog.upsert({"id": "route", "clearUpstreamProxyPassword": True})
        self.assertFalse(self.catalog.public(self.catalog.entry("route"))["upstreamProxyPasswordSet"])
        with self.assertRaises(ApiError):
            self.catalog.upsert({"id": "bad", "name": "Bad", "kind": "residential", "proxy": "http://host:80"})

    def test_binding_clears_inline_route_and_protects_delete_and_running_edit(self):
        self.proxy()
        self.worker.create({"id": "account", "proxy": "http://old:secret@host:80"})
        state = self.worker.create({"id": "account", "proxyId": "route"})
        self.assertEqual(self.worker.profile("account")["proxyPassword"], "")
        self.assertEqual(state["proxy"], "http://invalid.example:8080")
        self.assertEqual(state["proxyName"], "Fixture")
        self.assertEqual(state["proxyMode"], "chained")
        self.assertNotIn("fixture-secret", json.dumps(state))
        self.assertEqual(self.catalog.public(self.catalog.entry("route"))["bindingCount"], 1)
        with self.assertRaises(ApiError):
            self.catalog.delete("route")
        with patch.object(self.worker, "is_running", return_value=True), self.assertRaises(ApiError):
            self.catalog.upsert({"id": "route", "name": "Running rename"})
        self.worker.create({"id": "account", "proxyId": ""})
        self.assertEqual(self.worker.state("account")["proxy"], "")
        self.catalog.delete("route")

    def test_profile_draft_test_resolves_catalog_credentials(self):
        self.proxy()
        with patch("proxy_catalog.diagnose_proxy", return_value={"reachable": True}) as probe:
            self.worker.proxy_test({"proxyId": "route", "proxy": "http://wrong:80"})
        config = probe.call_args.args[0]
        self.assertEqual(config.host, "invalid.example")
        self.assertEqual(config.upstream.host, "127.0.0.1")

    def test_invalid_ids_and_thread_start_failure_leave_no_reservation(self):
        for pid in ([], {}, 12):
            with self.subTest(pid=pid), self.assertRaises(ApiError) as exc:
                self.catalog.upsert({"id": pid, "name": "Invalid"})
            self.assertEqual(exc.exception.status, 400)
        self.proxy()
        with patch("proxy_catalog.threading.Thread.start", side_effect=RuntimeError("failed")), self.assertRaises(ApiError):
            self.catalog.start(["route"])
        self.assertFalse(self.catalog.inflight)
        self.assertFalse(self.catalog.jobs)

    def test_executor_failure_reports_failed_and_shutdown_prevents_stale_save(self):
        self.proxy()
        with patch("proxy_catalog.ThreadPoolExecutor", side_effect=RuntimeError("failed")):
            final = self.wait(self.catalog.start(["route"])["id"])
        self.assertEqual(final["state"], "failed")
        self.assertFalse(self.catalog.inflight)
        entered, release = threading.Event(), threading.Event()
        def probe(config):
            entered.set()
            release.wait(2)
            return {"reachable": True}
        with patch("proxy_catalog.diagnose_proxy", side_effect=probe):
            job = self.catalog.start(["route"])
            self.assertTrue(entered.wait(1))
            self.catalog.close()
            release.set()
            final = self.wait(job["id"])
        self.assertEqual(final["state"], "cancelled")
        self.assertFalse(final["results"][0]["applied"])
        self.assertIsNone(self.catalog.entry("route")["check"])

    def test_revision_guard_check_deletion_guard_and_no_global_lock_during_network(self):
        self.proxy()
        entered, release = threading.Event(), threading.Event()
        def probe(config):
            entered.set()
            release.wait(3)
            return {"reachable": True, "egressIp": "203.0.113.1"}
        with patch("proxy_catalog.diagnose_proxy", side_effect=probe):
            job = self.catalog.start(["route"])
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(ApiError):
                    self.catalog.delete("route")
                with self.assertRaises(ApiError):
                    self.catalog.start(["route"])
                self.catalog.upsert({"id": "route", "name": "Changed while checking"})
                self.worker.create({"id": "parallel", "name": "Not blocked"})
            finally:
                release.set()
            final = self.wait(job["id"])
        self.assertFalse(final["results"][0]["applied"])
        self.assertIsNone(self.catalog.entry("route")["check"])

    def test_batch_concurrency_and_cancel_pending(self):
        for n in range(8):
            self.proxy("p" + str(n))
        lock, entered, release = threading.Lock(), threading.Event(), threading.Event()
        active, maximum = 0, 0
        def probe(config):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 3:
                    entered.set()
            release.wait(3)
            with lock:
                active -= 1
            return {"reachable": True}
        with patch("proxy_catalog.diagnose_proxy", side_effect=probe):
            job = self.catalog.start(["p" + str(n) for n in range(8)])
            self.assertTrue(entered.wait(1))
            snapshot = self.catalog.snapshot(job["id"])
            self.assertEqual(list(snapshot["items"].values()).count("running"), 3)
            self.assertEqual(list(snapshot["items"].values()).count("queued"), 5)
            self.assertNotIn("fixture-secret", json.dumps(snapshot))
            self.catalog.cancel(job["id"])
            release.set()
            final = self.wait(job["id"])
        self.assertLessEqual(maximum, 3)
        self.assertEqual(final["state"], "cancelled")
        self.assertEqual(final["completed"], 8)
        self.assertTrue(any(row["check"].get("cancelled") for row in final["results"]))
        self.assertEqual(list(final["items"].values()).count("completed"), 3)
        self.assertEqual(list(final["items"].values()).count("cancelled"), 5)


class DiagnosticTests(unittest.TestCase):
    def test_real_verified_https_request_size_redirect_and_deadline(self):
        fixtures = tls_fixture_directory()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(fixtures / "LOCAL_TEST_ONLY_cert.pem", fixtures / "LOCAL_TEST_ONLY_key.pem")
        verified = ssl.create_default_context(cafile=str(fixtures / "LOCAL_TEST_ONLY_cert.pem"))
        seen = []
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    head = receive_head(self.request)
                    seen.append(head)
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    with context.wrap_socket(self.request, server_side=True) as stream:
                        path = receive_head(stream).split(b" ")[1]
                        if path == b"/redirect":
                            stream.sendall(b"HTTP/1.1 302 Redirect\r\nLocation: http://untrusted.invalid\r\nContent-Length: 0\r\n\r\n")
                        elif path == b"/oversize":
                            stream.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 65537\r\n\r\n" + b"x" * 65537)
                        elif path == b"/slow":
                            stream.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 20000\r\n\r\n")
                            for _ in range(20):
                                stream.sendall(b"x")
                                time.sleep(.04)
                        else:
                            body = b'{"ip":"203.0.113.1"}'
                            stream.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                except OSError:
                    pass
        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
            thread.start()
            relay = type("Relay", (), {"port": server.server_address[1]})()
            # This test retains certificate verification and trusts only its local fixture certificate.
            with patch("http.client._create_https_context", return_value=verified):
                self.assertEqual(request_json(relay, "https://127.0.0.1/ok")["ip"], "203.0.113.1")
                for path in ("/redirect", "/oversize"):
                    with self.assertRaises(ValueError):
                        request_json(relay, "https://127.0.0.1" + path)
                started = time.monotonic()
                with self.assertRaises((TimeoutError, OSError, ValueError)):
                    request_json(relay, "https://127.0.0.1/slow", timeout=.2)
                self.assertLess(time.monotonic() - started, 1)
            server.shutdown()
            thread.join(1)
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(h.startswith(b"CONNECT 127.0.0.1:443 HTTP/") for h in seen))

    def probe(self, responses):
        with patch("proxy_diagnostics.ProxyRelay") as relay, patch("proxy_diagnostics.request_json", side_effect=responses) as request:
            result = diagnose_proxy(parse_proxy({"proxy": "http://host:80", "upstreamProxy": "http://up:80"}))
            calls = request.call_args_list
            self.assertTrue(all(c.args[0] is relay.return_value.__enter__.return_value for c in calls))
            self.assertTrue(all(c.args[1].startswith("https://") for c in calls))
            return result

    def test_complete_same_ip_information_has_explainable_quality(self):
        result = self.probe([{"ip": "203.0.113.1", "isResidential": True, "fraudScore": "10"},
                             {"ip": "203.0.113.1", "country": "US", "region": "Test", "org": "AS123 Fixture ISP"}])
        self.assertTrue(result["reachable"])
        self.assertEqual(result["isp"], "Fixture ISP")
        self.assertEqual(result["asn"], "AS123")
        self.assertEqual(result["qualityScore"], 96)
        self.assertTrue(result["qualityComplete"])
        self.assertFalse(result["rotatingExit"])
        self.assertIsNone(result["metadataError"])

    def test_geography_uses_the_same_exit_snapshot_and_validates_timezone(self):
        primary = {"ip": "203.0.113.1", "isResidential": True, "fraudScore": 10,
                   "countryCode": "US", "region": "Texas", "city": "Dallas", "timezone": "America/Chicago",
                   "asn": 123, "asOrganization": "Fixture ISP"}
        wrong = {"ip": "203.0.113.2", "country": "JP", "city": "Tokyo", "timezone": "Asia/Tokyo"}
        result = self.probe([primary, wrong])
        self.assertEqual((result["countryCode"], result["city"], result["timezone"]), ("US", "Dallas", "America/Chicago"))
        primary["timezone"] = "Not/ARealZone"
        result = self.probe([primary, wrong, wrong])
        self.assertIsNone(result["timezone"])
        self.assertEqual(result["city"], "Dallas")

    def test_direct_diagnostics_do_not_use_proxy_or_system_proxy(self):
        network = {"ip": "203.0.113.1", "country": "GB", "city": "London", "region": "England",
                   "timezone": "Europe/London", "org": "AS123 Fixture ISP"}
        with patch("proxy_diagnostics.ProxyRelay") as relay, patch("proxy_diagnostics.request_json", side_effect=[OSError(), network]) as requests:
            result = diagnose_proxy(parse_proxy({}))
        self.assertTrue(result["reachable"])
        self.assertEqual(result["mode"], "direct")
        self.assertEqual(result["timezone"], "Europe/London")
        relay.assert_not_called()
        self.assertTrue(all(call.args[0] is None for call in requests.call_args_list))

    def test_unavailable_risk_service_does_not_fake_score_or_break_connectivity(self):
        result = self.probe([ValueError("contains private credentials"),
                             {"ip": "203.0.113.1", "country": "US", "region": "Test", "org": "AS123 Fixture ISP"}])
        self.assertTrue(result["reachable"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["qualityScore"])
        self.assertFalse(result["qualityComplete"])
        self.assertIsNone(result["isResidential"])
        self.assertEqual(result["snapshotSource"], "ipinfo")
        self.assertNotIn("private credentials", json.dumps(result))

    def test_rotating_exits_keep_primary_snapshot_and_never_mix_network_details(self):
        primary = {"ip": "203.0.113.1", "isResidential": True, "fraudScore": 5,
                   "countryCode": "US", "country": "United States", "region": "Primary Region",
                   "asOrganization": "Primary ISP", "asn": 123}
        result = self.probe([primary, {"ip": "203.0.113.2", "country": "JP", "region": "Wrong Region", "org": "AS999 Wrong ISP"}])
        self.assertEqual(result["egressIp"], "203.0.113.1")
        self.assertEqual(result["snapshotSource"], "ippure")
        self.assertEqual(result["country"], "US")
        self.assertEqual(result["region"], "Primary Region")
        self.assertEqual(result["isp"], "Primary ISP")
        self.assertEqual(result["asn"], "AS123")
        self.assertEqual(result["qualityScore"], 98)
        self.assertTrue(result["rotatingExit"])
        self.assertFalse(result["sources"]["network"]["matchesEgress"])
        self.assertEqual(len(result["observations"]), 2)
        self.assertIn("单次出口快照", result["observationNote"])
        self.assertIsNone(result["metadataError"])

    def test_primary_remains_complete_when_comparative_network_service_fails(self):
        result = self.probe([{"ip": "203.0.113.1", "isResidential": False, "fraudScore": 80,
                              "countryCode": "US", "region": "Test", "asOrganization": "Primary ISP", "asn": "AS123"},
                             ValueError("network unavailable")])
        self.assertTrue(result["reachable"])
        self.assertTrue(result["qualityComplete"])
        self.assertEqual(result["qualityScore"], 33)
        self.assertIsNone(result["metadataError"])
        self.assertFalse(result["rotatingExit"])

    def test_invalid_or_absent_primary_ip_withholds_its_risk_metadata(self):
        for ip in (None, "not-an-ip", "127.0.0.1/path?credential", True):
            result = self.probe([{"ip": ip, "isResidential": True, "fraudScore": 0, "asOrganization": "Wrong ISP"},
                                 {"ip": "203.0.113.2", "country": "JP", "region": "Fallback Region", "org": "AS999 Fallback ISP"}])
            self.assertTrue(result["reachable"])
            self.assertEqual(result["egressIp"], "203.0.113.2")
            self.assertEqual(result["isp"], "Fallback ISP")
            self.assertIsNone(result["qualityScore"])
            self.assertIsNone(result["riskScore"])
            self.assertIsNone(result["isResidential"])

    def test_lookup_only_fills_exact_canonical_ip_and_is_not_exit_observation(self):
        primary = {"ip": "203.0.113.1", "isResidential": True, "fraudScore": 0}
        wrong = {"ip": "203.0.113.2", "country": "JP", "region": "Wrong", "org": "AS999 Wrong ISP"}
        matching = {"ip": "203.0.113.1", "country": "US", "region": "Exact", "org": "AS123 Exact ISP"}
        for lookup in (wrong, matching):
            result = self.probe([primary, wrong, lookup])
            self.assertEqual(result["egressIp"], "203.0.113.1")
            self.assertEqual(result["isp"], "Exact ISP" if lookup is matching else None)
            self.assertEqual(result["qualityScore"], 100)
            self.assertEqual(result["sources"]["networkLookup"]["url"], "https://ipinfo.io/203.0.113.1/json")
            self.assertFalse(result["observations"][-1]["isExitObservation"])
            self.assertTrue(result["rotatingExit"])

    def test_ipify_fallback_has_unknown_metadata_and_request_deadlines_share_budget(self):
        with patch("proxy_diagnostics.ProxyRelay"), patch("proxy_diagnostics.time.monotonic") as clock:
            current = [0.0]
            clock.side_effect = lambda: current[0]
            timeouts = []
            def request(relay, endpoint, timeout):
                timeouts.append(timeout)
                current[0] += timeout
                if len(timeouts) < 3:
                    raise TimeoutError("private")
                return {"ip": "203.0.113.1"}
            with patch("proxy_diagnostics.request_json", side_effect=request):
                result = diagnose_proxy(parse_proxy({"proxy": "http://host:80"}))
        self.assertTrue(result["reachable"])
        self.assertEqual(result["snapshotSource"], "ipify")
        self.assertIsNone(result["qualityScore"])
        self.assertEqual(timeouts, [8, 8, 8, 8])
        self.assertLessEqual(current[0], 32)

    def test_all_failed_services_never_exceed_four_attempts_and_budget(self):
        with patch("proxy_diagnostics.ProxyRelay"), patch("proxy_diagnostics.time.monotonic") as clock:
            current = [0.0]
            clock.side_effect = lambda: current[0]
            def request(relay, endpoint, timeout):
                current[0] += timeout
                raise TimeoutError("private")
            with patch("proxy_diagnostics.request_json", side_effect=request) as call:
                result = diagnose_proxy(parse_proxy({"proxy": "http://host:80"}))
        self.assertFalse(result["reachable"])
        self.assertEqual(call.call_count, 4)
        self.assertEqual(current[0], 32)

    def test_org_does_not_imply_residential_and_unknown_risk_stays_unknown(self):
        result = self.probe([{"ip": "203.0.113.1", "fraudScore": "unknown"},
                             {"ip": "203.0.113.1", "org": "AS123 Telecom", "country": "US", "region": "Test"}])
        self.assertIsNone(result["isResidential"])
        self.assertEqual(result["riskStatus"], "unknown")
        self.assertIsNone(result["qualityScore"])


if __name__ == "__main__":
    unittest.main()

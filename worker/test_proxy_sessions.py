"""Offline session policy tests. No real profile, browser or provider is touched."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from proxy_catalog import ProxyCatalog
from proxy_support import parse_proxy


class Error(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Worker:
    def __init__(self, path):
        self.data_dir = path
        self.lock = threading.RLock()
        self.profiles = {pid: {"id": pid, "proxyId": "route"} for pid in ("one", "two")}
        self.runtime = {}
        self.account_checks = {}
        self.active = set()

    def is_running(self, pid):
        return pid in self.active

    def profile(self, pid):
        return self.profiles[pid]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = Worker(Path(self.temp.name))
        self.catalog = ProxyCatalog(self.worker, Error)
        self.payload = dict(id="route", name="Fixture", kind="residential",
                            proxy="http://original-region-us:terminal-secret@gateway.invalid:443",
                            upstreamProxy="http://up:upstream-secret@127.0.0.1:7897",
                            sessionMode="per_profile", sessionTemplate="{user}-session-{session}",
                            sessionLifetimeMinutes=0)
        self.catalog.upsert(self.payload)

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def resolve(self, pid="one", refresh=True):
        return self.catalog.resolve_for_profile(self.worker.profile(pid), refresh=refresh)

    def test_default_fixed_preserves_original_username_and_does_not_create_records(self):
        old = copy.deepcopy(self.payload)
        for field in ("sessionMode", "sessionTemplate", "sessionLifetimeMinutes"):
            old.pop(field)
        self.catalog.upsert(dict(old, sessionMode="fixed", sessionTemplate="", sessionLifetimeMinutes=0))
        self.assertEqual(self.resolve()["proxyUsername"], "original-region-us")
        self.assertFalse(self.catalog.sessions.records)
        self.assertEqual(self.catalog.session_info(self.worker.profile("one"))["mode"], "fixed")

    def test_details_do_not_allocate_or_rotate_sessions(self):
        self.resolve(refresh=False)
        self.assertFalse(self.catalog.sessions.path.exists())
        self.assertIsNone(self.catalog.session_info(self.worker.profile("one"))["session"])

    def test_independent_stable_tokens_survive_worker_restart(self):
        one, two = self.resolve(), self.resolve("two")
        self.assertNotEqual(one["proxyUsername"], two["proxyUsername"])
        self.assertEqual(one, self.resolve())
        restored = ProxyCatalog(Worker(Path(self.temp.name)), Error)
        self.assertEqual(one["proxyUsername"], restored.resolve_for_profile(self.worker.profile("one"))["proxyUsername"])
        self.assertNotIn("terminal-secret", restored.sessions.path.read_text(encoding="utf-8"))
        self.assertNotIn("upstream-secret", restored.sessions.path.read_text(encoding="utf-8"))

    def test_chain_and_password_are_identical_only_terminal_username_changes(self):
        config = parse_proxy(self.resolve())
        original = parse_proxy(self.catalog.entry("route"))
        self.assertEqual(config.upstream, original.upstream)
        self.assertEqual(config.address, original.address)
        self.assertEqual(config.password, original.password)
        self.assertTrue(config.username.startswith("original-region-us-session-"))
        with self.assertRaises(Error):
            self.catalog.upsert(dict(self.payload, upstreamProxy=""))

    def test_template_is_explicit_and_rejects_unknown_fields_formats_or_missing_user(self):
        for template in ("", "{user}", "{session}", "{user}-{session}-{password}",
                         "{user.__class__}-{session}", "{user!r}-{session}", "{user}-{session:20}", "{user}-{session"):
            with self.subTest(template=template), self.assertRaises(Error):
                self.catalog.upsert(dict(self.payload, sessionTemplate=template))
        with self.assertRaises(Error):
            self.catalog.upsert(dict(self.payload, proxy="http://gateway.invalid:443", proxyUsername=""))

    def test_bad_lifetime_and_standard_session_mode_are_rejected(self):
        for value in (-1, 43201, True, "60", 1.5):
            with self.subTest(value=value), self.assertRaises(Error):
                self.catalog.upsert(dict(self.payload, sessionLifetimeMinutes=value))
        with self.assertRaises(Error):
            self.catalog.upsert(dict(self.payload, kind="standard"))

    def test_expiry_only_refreshes_closed_environment_and_never_on_read(self):
        self.catalog.upsert(dict(self.payload, sessionLifetimeMinutes=1))
        with patch("proxy_sessions.time.time", return_value=1000):
            initial = self.resolve()
        with patch("proxy_sessions.time.time", return_value=1061):
            self.assertEqual(initial["proxyUsername"], self.resolve(refresh=False)["proxyUsername"])
            self.assertTrue(self.catalog.session_info(self.worker.profile("one"))["expired"])
            self.worker.active.add("one")
            self.worker.runtime["one"] = {"proxyConfig": initial}
            self.assertEqual(initial, self.resolve())
            self.worker.active.clear()
            changed = self.resolve()
            self.assertNotEqual(initial["proxyUsername"], changed["proxyUsername"])
            self.assertEqual(self.catalog.session_info(self.worker.profile("one"))["rotationReason"], "expired")

    def test_manual_rotate_rejects_running_preserves_other_accounts_and_clears_checks(self):
        before, other = self.resolve(), self.resolve("two")
        self.worker.account_checks["one"] = {"status": "logged_in"}
        self.worker.active.add("one")
        with self.assertRaises(Error) as ex:
            self.catalog.rotate_profile_session("one")
        self.assertEqual(ex.exception.status, 409)
        self.worker.active.clear()
        meta = self.catalog.rotate_profile_session("one")
        self.assertEqual(meta["rotationReason"], "manual")
        self.assertNotEqual(before["proxyUsername"], self.resolve()["proxyUsername"])
        self.assertEqual(other, self.resolve("two"))
        self.assertNotIn("one", self.worker.account_checks)
        self.assertNotIn("terminal-secret", json.dumps(meta))
        self.assertNotIn("original-region-us", json.dumps(meta))

    def test_rebinding_and_policy_change_invalidate_only_corresponding_records_and_checks(self):
        before = self.resolve()
        self.catalog.entry("route")["check"] = {"reachable": True}
        self.worker.account_checks["one"] = {"status": "logged_in"}
        self.catalog.upsert(dict(self.payload, sessionTemplate="{user}-sid-{session}"))
        self.assertIsNone(self.catalog.entry("route")["check"])
        self.assertNotIn("one", self.worker.account_checks)
        self.assertIsNone(self.catalog.session_info(self.worker.profile("one"))["session"])
        self.assertNotEqual(before["proxyUsername"], self.resolve()["proxyUsername"])
        self.catalog.upsert(dict(self.payload, id="second"))
        self.worker.profiles["one"]["proxyId"] = "second"
        self.assertIsNone(self.catalog.session_info(self.worker.profile("one"))["session"])
        self.assertNotEqual(before["proxyUsername"], self.resolve()["proxyUsername"])

    def test_inventory_checks_use_temporary_session_do_not_rotate_profile(self):
        before = self.resolve()
        token = self.catalog.session_info(self.worker.profile("one"))["session"]
        with patch("proxy_catalog.diagnose_proxy", return_value={"reachable": True, "egressIp": "203.0.113.1"}) as probe:
            result = self.catalog.draft_test({"id": "route"})
        actual = probe.call_args.args[0]
        self.assertNotIn(token, actual.username)
        self.assertEqual(actual.upstream, parse_proxy(before).upstream)
        self.assertEqual(result["sessionScope"], "temporary")
        self.assertEqual(before["proxyUsername"], self.resolve()["proxyUsername"])
        self.assertEqual(result, self.catalog.entry("route")["check"])

    def test_session_save_failure_rolls_back_without_rotating(self):
        before = self.resolve()
        with patch.object(self.catalog.sessions, "save", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.catalog.rotate_profile_session("one")
        self.assertEqual(before, self.resolve())


if __name__ == "__main__":
    unittest.main()

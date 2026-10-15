"""Isolated inventory API flows; no real profiles, credentials or network probes."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import BrowserWorker, ApiError, make_handler


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.directory.name), Path('missing.exe'))

    def tearDown(self):
        self.worker.accounts.close()
        self.directory.cleanup()

    def wait(self, jid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.worker.accounts.snapshot(jid)
            if job['state'] not in ('running', 'cancelling'):
                return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_preview_validation_commit_and_duplicate_detection(self):
        payload = {'rows': [
            {'name': 'Primary', 'accountEmail': 'one@example.invalid'},
            {'name': 'duplicate', 'accountEmail': 'ONE@example.invalid'},
            {'accountEmail': 'invalid'}, {'name': 'credential', 'password': 'fixture'}], 'preview': True}
        result = self.worker.accounts.import_rows(payload)
        self.assertEqual([r['status'] for r in result['results']], ['ready', 'duplicate', 'invalid', 'invalid'])
        self.assertEqual(self.worker.profiles, {})
        result = self.worker.accounts.import_rows(dict(payload, preview=False))
        self.assertEqual(result['created'], 1)
        self.assertEqual(len(self.worker.profiles), 1)
        self.assertEqual(self.worker.accounts.import_rows(dict(payload, preview=False))['created'], 0)
        restored = BrowserWorker(Path(self.directory.name), Path('missing.exe'))
        self.assertEqual(len(restored.profiles), 1)

    def test_csv_json_and_independent_seeds(self):
        csv = 'name,accountEmail,accountGroup,accountNotes\n"Account, One",one@example.invalid,test,"first\nsecond"'
        result = self.worker.accounts.import_rows({'text': csv, 'preview': False})
        self.assertEqual(result['created'], 1)
        self.assertEqual(next(iter(self.worker.profiles.values()))['accountNotes'], 'first\nsecond')
        self.assertEqual(self.worker.accounts.import_rows({'text': json.dumps({'accounts': [{'name': 'JSON'}]}), 'preview': False})['created'], 1)
        self.worker.accounts.import_rows({'count': 20, 'prefix': 'Batch', 'preview': False})
        self.assertEqual(len({p['seed'] for p in self.worker.profiles.values()}), 22)

    def test_import_uses_fixed_known_region_and_never_temporary_residential_country(self):
        self.worker.catalog.upsert({'id': 'fixed', 'name': 'fixture', 'proxy': 'http://fixture.invalid:80'})
        self.worker.catalog.entries['fixed']['check'] = {'reachable': True, 'countryCode': 'US', 'timezone': 'America/Chicago'}
        result = self.worker.accounts.import_rows({'count': 1, 'proxyId': 'fixed', 'preview': False})
        self.assertEqual(result['results'][0]['regionStatus'], 'matched_proxy')
        profile = next(iter(self.worker.profiles.values()))
        self.assertEqual((profile['locale'], profile['timezone'], profile['regionMode']), ('en-US', 'America/Chicago', 'proxy'))
        self.worker.catalog.entries['fixed']['check']['timezone'] = None
        result = self.worker.accounts.import_rows({'count': 1, 'proxyId': 'fixed', 'preview': True})
        self.assertEqual(result['results'][0]['regionStatus'], 'missing_timezone')
        self.assertEqual(result['results'][0]['regionMode'], 'manual')
        self.worker.catalog.entries['fixed'].update(kind='residential', sessionMode='per_profile')
        result = self.worker.accounts.import_rows({'count': 1, 'proxyId': 'fixed', 'preview': True})
        self.assertEqual(result['results'][0]['regionStatus'], 'not_checked')
        self.assertEqual(result['results'][0]['regionMode'], 'manual')
        self.assertFalse(self.worker.catalog.sessions.records)
        invalid = self.worker.accounts.import_rows({'rows': [{'proxyId': []}]})
        self.assertEqual(invalid['results'][0]['status'], 'invalid')

    def test_limits_and_no_accidental_batch_clear(self):
        for payload in ({'rows': []}, {'count': True}, {'count': 201}, {'count': 1, 'rows': []}, {'text': 'x' * 60001}, {'rows': [{'name': 'one'}], 'preview': 'false'}):
            with self.subTest(payload=str(payload)[:100]), self.assertRaises(ApiError):
                self.worker.accounts.import_rows(payload)
        for action in ('proxy', 'group'):
            with self.assertRaises(ApiError):
                self.worker.accounts.batch({'ids': ['one'], 'action': action})

    def test_import_save_failure_rolls_back_all_rows(self):
        with patch.object(self.worker, 'save', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.worker.accounts.import_rows({'count': 3, 'preview': False})
        self.assertEqual(self.worker.profiles, {})

    def test_batch_partial_failures_and_running_metadata(self):
        self.worker.create({'id': 'one'}); self.worker.create({'id': 'two'})
        self.worker.runtime['one'] = {'process': SimpleNamespace(poll=lambda: None, pid=0), 'cdpPort': 0}
        self.worker.target = Mock(return_value={'url': 'https://dola.com', 'title': 'fixture'})
        result = self.worker.accounts.batch({'ids': ['one', 'two', 'missing'], 'action': 'group', 'accountGroup': 'group'})
        self.assertEqual((result['succeeded'], result['failed']), (2, 1))
        result = self.worker.accounts.batch({'ids': ['one', 'two'], 'action': 'proxy', 'proxyId': ''})
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))
        self.assertEqual(self.worker.profile('one')['accountGroup'], 'group')
        self.worker.runtime.clear()

    def test_details_redaction_persistence_and_unknown_quota(self):
        self.worker.create({'id': 'one', 'proxy': 'http://fixture.invalid:80', 'proxyUsername': 'fixture-user', 'proxyPassword': 'fixture-secret'})
        self.worker.check_account('one')
        self.worker.accounts.persist('one', 'networkCheck', {'reachable': False, 'checkedAt': 'fixture', 'countryCode': None})
        restored = BrowserWorker(Path(self.directory.name), Path('missing.exe'))
        details = restored.accounts.details('one')
        self.assertEqual(details['accountHealth'], 'network_error')
        self.assertEqual(details['accountCheck']['reason'], 'browser_closed')
        self.assertEqual(details['quota']['remaining'], None)
        self.assertNotIn('fixture-secret', json.dumps(details))
        self.assertIsNotNone(details['createdAt'])

    def logged_in_runtime(self, pid='one'):
        runtime = {'process': SimpleNamespace(poll=lambda: None)}
        self.worker.runtime[pid] = runtime
        self.worker.command = Mock(return_value={'result': {'value': {
            'url': 'https://dola.com/chat/', 'ready': 'complete', 'logout': True}}})
        self.worker.session_backups.after_login = Mock()
        return runtime

    def test_login_evidence_is_current_only_for_the_observed_running_instance(self):
        self.worker.create({'id': 'one'})
        runtime = self.logged_in_runtime()
        observed = self.worker.check_account('one')
        current = self.worker.accounts.summary('one')
        self.assertEqual(current['accountHealth'], 'logged_in')
        self.assertTrue(current['accountCheck']['isCurrent'])
        self.assertEqual(current['lastAccountCheck']['evidenceScope'], 'current')
        runtime['process'].poll = lambda: 0
        closed = self.worker.accounts.summary('one')
        self.assertEqual(closed['accountHealth'], 'unknown')
        self.assertEqual(closed['accountCheck']['reason'], 'browser_closed')
        self.assertIsNone(closed['accountCheck']['checkedAt'])
        self.assertEqual(closed['lastAccountCheck']['checkedAt'], observed['checkedAt'])
        self.assertEqual(closed['lastAccountCheck']['evidenceScope'], 'history')
        self.logged_in_runtime()
        reopened = self.worker.accounts.summary('one')
        self.assertEqual(reopened['accountCheck']['reason'], 'session_not_checked')
        self.assertFalse(reopened['accountCheck']['isCurrent'])
        self.assertEqual(reopened['lastAccountCheck']['status'], 'logged_in')
        self.worker.check_account('one')
        self.assertTrue(self.worker.accounts.summary('one')['accountCheck']['isCurrent'])
        self.worker.runtime.clear()

    def test_worker_restart_and_closed_check_preserve_history_without_current_login(self):
        self.worker.create({'id': 'one'})
        self.logged_in_runtime()
        observed = self.worker.check_account('one')
        restored = BrowserWorker(Path(self.directory.name), Path('missing.exe'))
        history = restored.accounts.summary('one')
        self.assertEqual(history['accountHealth'], 'unknown')
        self.assertFalse(history['accountCheck']['isCurrent'])
        self.assertEqual(history['lastAccountCheck']['checkedAt'], observed['checkedAt'])
        restored.check_account('one')
        after_closed_check = BrowserWorker(Path(self.directory.name), Path('missing.exe')).accounts.summary('one')
        self.assertEqual(after_closed_check['lastAccountCheck']['status'], 'logged_in')
        self.assertEqual(after_closed_check['accountCheck']['status'], 'unknown')
        self.worker.runtime.clear()

    def test_old_observations_upgrade_and_changed_routes_keep_labeled_history(self):
        self.worker.create({'id': 'one'})
        saved = {'status': 'logged_in', 'reason': 'visible_logout', 'checkedAt': '2026-10-01T00:00:00Z'}
        self.worker.accounts.record_check('one', 'account', saved)
        restored = BrowserWorker(Path(self.directory.name), Path('missing.exe'))
        self.assertEqual(restored.accounts.summary('one')['lastAccountCheck']['status'], 'logged_in')
        restored.check_account('one')
        self.assertEqual(restored.accounts.summary('one')['lastAccountCheck']['checkedAt'], saved['checkedAt'])
        restored.create({'id': 'one', 'proxy': 'http://new.invalid:80'})
        summary = restored.accounts.summary('one')
        self.assertEqual(summary['accountCheck']['reason'], 'proxy_changed')
        self.assertEqual(summary['lastAccountCheck']['status'], 'logged_in')
        self.assertFalse(summary['lastAccountCheck']['routeMatches'])

    def test_logged_in_site_never_verifies_bound_google_identity(self):
        self.worker.create({'id': 'one'})
        self.logged_in_runtime()
        self.worker.check_account('one')
        manual = self.worker.accounts.summary('one')
        self.assertEqual(manual['identityCheck'], {'status': 'unverified', 'reason': 'site_identity_not_observed'})
        self.worker.profiles['one']['loginAccountId'] = 'fixture-binding'
        bound = self.worker.accounts.summary('one')
        self.assertEqual(bound['identityCheck'], {'status': 'unverified', 'reason': 'bound_account_not_verified'})
        self.assertEqual(bound['accountCheck']['identityStatus'], 'unverified')
        self.worker.runtime.clear()

    def test_current_signed_out_replaces_positive_history(self):
        self.worker.create({'id': 'one'})
        self.logged_in_runtime()
        self.worker.check_account('one')
        self.worker.command.return_value = {'result': {'value': {
            'url': 'https://dola.com/signin', 'ready': 'complete', 'login': True}}}
        self.worker.check_account('one')
        summary = self.worker.accounts.summary('one')
        self.assertEqual(summary['accountHealth'], 'login_required')
        self.assertEqual(summary['lastAccountCheck']['status'], 'login_required')
        self.assertEqual(summary['identityCheck']['status'], 'unknown')
        self.worker.runtime.clear()

    def test_network_job_uses_profile_effective_proxy_and_persists(self):
        self.worker.create({'id': 'one', 'proxy': 'http://fixture.invalid:80'})
        with patch('account_management.diagnose_proxy', return_value={'reachable': True, 'countryCode': 'US', 'checkedAt': 'fixture'}) as probe:
            job = self.worker.accounts.start({'ids': ['one'], 'kind': 'network'})
            result = self.wait(job['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['results'][0]['applied'])
        self.assertEqual(probe.call_args.args[0].address, 'http://fixture.invalid:80')
        self.assertEqual(self.worker.accounts.details('one')['networkCheck']['countryCode'], 'US')

    def test_cancel_bound_concurrency_and_prevents_persistence(self):
        for i in range(8): self.worker.create({'id': f'p{i}'})
        release, entered = threading.Event(), threading.Event()
        count, active, maximum = [0], [0], [0]
        gate = threading.Lock()
        def probe(_):
            with gate:
                count[0] += 1; active[0] += 1; maximum[0] = max(maximum[0], active[0]); entered.set()
            release.wait(4)
            with gate: active[0] -= 1
            return {'reachable': True, 'countryCode': 'US'}
        with patch('account_management.diagnose_proxy', side_effect=probe):
            job = self.worker.accounts.start({'ids': list(self.worker.profiles), 'kind': 'network'})
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ApiError): self.worker.accounts.start({'ids': ['p0'], 'kind': 'login'})
            self.worker.accounts.cancel(job['id']); release.set()
            result = self.wait(job['id'])
        self.assertEqual(result['state'], 'cancelled')
        self.assertLessEqual(maximum[0], 3)
        self.assertLessEqual(count[0], 3)
        self.assertFalse(any(row['applied'] for row in result['results']))
        self.assertFalse(self.worker.accounts.observations)

    def test_late_network_result_rejects_changed_proxy(self):
        self.worker.create({'id': 'one', 'proxy': 'http://old.invalid:80'})
        entered, release = threading.Event(), threading.Event()
        def probe(_):
            entered.set(); release.wait(4)
            return {'reachable': True, 'countryCode': 'US'}
        with patch('account_management.diagnose_proxy', side_effect=probe):
            job = self.worker.accounts.start({'ids': ['one'], 'kind': 'network'})
            self.assertTrue(entered.wait(2))
            self.worker.create({'id': 'one', 'proxy': 'http://new.invalid:80'})
            release.set(); result = self.wait(job['id'])
        self.assertFalse(result['results'][0]['applied'])
        self.assertIsNone(self.worker.accounts.details('one')['networkCheck'])

    def test_single_network_check_rejects_route_aba_and_restart_stale_evidence(self):
        self.worker.create({'id': 'one', 'proxy': 'http://old.invalid:80'})
        self.worker.accounts.record_check('one', 'network', {'reachable': True, 'countryCode': 'GB'})
        entered, release = threading.Event(), threading.Event()
        def probe(_):
            entered.set(); release.wait(4)
            return {'reachable': True, 'countryCode': 'US'}
        with patch('server.diagnose_proxy', side_effect=probe):
            thread = threading.Thread(target=lambda: self.worker.proxy_test(profile_id='one'))
            thread.start(); self.assertTrue(entered.wait(2))
            self.worker.create({'id': 'one', 'proxy': 'http://new.invalid:80'})
            self.worker.create({'id': 'one', 'proxy': 'http://old.invalid:80'})
            release.set(); thread.join(3)
        self.assertIsNone(self.worker.accounts.details('one')['networkCheck'])
        # Even if clearing observations cannot be written, route identity prevents
        # saved old evidence from appearing for the new persisted environment.
        self.worker.accounts.record_check('one', 'network', {'reachable': True, 'countryCode': 'GB'})
        self.worker.profiles['one']['proxy'] = 'http://other.invalid:80'; self.worker.save()
        restored = BrowserWorker(Path(self.directory.name), Path('missing.exe'))
        self.assertIsNone(restored.accounts.details('one')['networkCheck'])

    def test_residential_profile_checks_use_own_session_and_leave_inventory_snapshot(self):
        self.worker.catalog.upsert({'id': 'res', 'name': 'fixture', 'kind': 'residential', 'proxy': 'http://terminal.invalid:443',
                                    'proxyUsername': 'fixture', 'proxyPassword': 'fixture-secret', 'upstreamProxy': 'http://upstream.invalid:80',
                                    'sessionMode': 'per_profile', 'sessionTemplate': '{user}-session-{session}'})
        self.worker.create({'id': 'one', 'proxyId': 'res'})
        self.assertFalse(self.worker.catalog.sessions.records)
        with patch('server.diagnose_proxy', return_value={'reachable': True, 'countryCode': 'US'}) as probe:
            self.worker.proxy_test(profile_id='one')
        session = self.worker.catalog.session_info(self.worker.profile('one'))['session']
        self.assertEqual(probe.call_args.args[0].username, 'fixture-session-' + session)
        self.assertEqual(probe.call_args.args[0].upstream.host, 'upstream.invalid')
        self.assertIsNone(self.worker.catalog.entry('res')['check'])
        self.assertNotIn('fixture-secret', json.dumps(self.worker.accounts.details('one')))

    def test_late_login_result_does_not_overwrite_changed_session(self):
        self.worker.create({'id': 'one'})
        rt = {'process': SimpleNamespace(poll=lambda: None)}
        self.worker.runtime['one'] = rt
        entered, release = threading.Event(), threading.Event()
        def observation(*_):
            entered.set(); release.wait(4)
            return {'result': {'value': {'url': 'https://dola.com/', 'ready': 'complete', 'logout': True}}}
        self.worker.command = Mock(side_effect=observation)
        self.worker.target = Mock(return_value={})
        job = self.worker.accounts.start({'ids': ['one'], 'kind': 'login'})
        self.assertTrue(entered.wait(2))
        self.worker.accounts.invalidate_proxy('one')
        release.set(); result = self.wait(job['id'])
        self.assertFalse(result['results'][0]['applied'])
        self.assertEqual(self.worker.account_checks['one']['reason'], 'proxy_changed')
        self.worker.runtime.clear()

    def test_authenticated_http_import_batch_details_and_jobs(self):
        token = 'fixture-token-with-24-plus-characters'
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.worker, token))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def call(path, payload=None, authorized=True):
            request = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                              headers={'Authorization': f'Bearer {token}' if authorized else '', 'Content-Type': 'application/json'})
            with urlopen(request, timeout=3) as response: return json.load(response)
        try:
            with self.assertRaises(HTTPError) as denied: call('/accounts/import', {'count': 1}, authorized=False)
            self.assertEqual(denied.exception.code, 401)
            preview = call('/accounts/import', {'rows': [{'id': 'one', 'name': 'fixture'}], 'preview': True})
            self.assertEqual(preview['created'], 0)
            self.assertEqual(call('/accounts/import', {'rows': [{'id': 'one', 'name': 'fixture'}], 'preview': False})['created'], 1)
            self.assertEqual(call('/accounts/batch', {'ids': ['one'], 'action': 'group', 'accountGroup': 'fixture'})['succeeded'], 1)
            self.assertEqual(call('/profiles/one/details')['accountGroup'], 'fixture')
            job = call('/accounts/check', {'ids': ['one'], 'kind': 'login'})
            self.wait(job['id'])
            self.assertEqual(call('/account-jobs/' + job['id'])['completed'], 1)
            self.assertIn(call('/account-jobs/' + job['id'] + '/cancel', {})['state'], ('completed', 'cancelled'))
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()

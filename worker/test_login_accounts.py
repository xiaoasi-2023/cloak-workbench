"""Account inventory, plaintext persistence and isolated authenticated HTTP flows."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from server import BrowserWorker, ApiError, make_handler


class LoginAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.registry = self.worker.login_accounts
        self.worker.create({'id': 'one', 'name': 'Original'})
        self.worker.create({'id': 'two', 'name': 'Other'})
        self.record = self.registry.upsert({'email': 'test@example.invalid', 'password': 'fixture-only-password', 'name': 'Test'})
        self.aid = self.record['id']

    def tearDown(self):
        self.worker.google_login.close()
        self.worker.accounts.close()
        self.worker.runtime.clear()
        self.temp.cleanup()

    def running(self, pid='one'):
        runtime = {'process': SimpleNamespace(poll=lambda: None, pid=123), 'cdpPort': 1}
        self.worker.runtime[pid] = runtime
        return runtime

    def assertError(self, status, fn):
        with self.assertRaises(ApiError) as raised:
            fn()
        self.assertEqual(raised.exception.status, status)

    def test_inventory_shows_plaintext_password_but_profile_summary_omits_it(self):
        self.assertEqual(len(self.worker.profiles), 2)
        self.assertEqual(len(self.registry.list()['accounts']), 1)
        self.assertTrue(self.record['passwordSet'])
        self.assertNotIn('password', self.record)
        self.assertNotIn('protectedPassword', self.record)
        saved = json.loads(self.registry.path.read_text(encoding='utf-8'))[0]
        self.assertEqual(saved['password'], 'fixture-only-password')
        self.assertNotIn('protectedPassword', saved)
        self.assertEqual(self.registry.credentials(self.aid)['password'], 'fixture-only-password')
        self.assertEqual(self.registry.list()['accounts'][0]['password'], 'fixture-only-password')
        self.registry.bind('one', {'accountId': self.aid})
        self.assertNotIn('password', self.worker.state('one')['loginAccount'])

    def test_duplicate_email_and_invalid_input_are_rejected(self):
        self.assertError(409, lambda: self.registry.upsert({'email': 'TEST@example.invalid', 'password': 'other'}))
        for payload in ({'email': 'bad', 'password': 'p'}, {'email': 'other@example.invalid'},
                        {'email': 'other@example.invalid', 'password': 123}, {'email': 'other@example.invalid', 'password': 'p', 'cookies': []}):
            self.assertError(400, lambda: self.registry.upsert(payload))

    def test_empty_edit_preserves_password_and_update_replaces_it(self):
        self.registry.upsert({'id': self.aid, 'name': 'Changed', 'password': ''})
        self.assertEqual(self.registry.credentials(self.aid)['password'], 'fixture-only-password')
        self.registry.upsert({'id': self.aid, 'password': 'replacement'})
        self.assertEqual(self.registry.credentials(self.aid)['password'], 'replacement')

    def test_binding_persists_and_does_not_change_environment(self):
        before = dict(self.worker.profile('one'))
        self.registry.bind('one', {'accountId': self.aid})
        self.assertEqual(self.worker.profile('one'), dict(before, loginAccountId=self.aid))
        reload = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.assertEqual(reload.profile('one')['loginAccountId'], self.aid)
        self.assertEqual(reload.login_accounts.credentials(self.aid)['password'], 'fixture-only-password')
        self.assertEqual(reload.state('one')['loginAccount']['email'], 'test@example.invalid')
        reload.accounts.close()

    def test_only_one_environment_can_bind_same_account(self):
        self.registry.bind('one', {'accountId': self.aid})
        self.assertError(409, lambda: self.registry.bind('two', {'accountId': self.aid}))
        self.assertError(409, lambda: self.worker.create({'id': 'three', 'loginAccountId': self.aid}))
        self.assertNotIn('three', self.worker.profiles)
        self.assertError(404, lambda: self.registry.bind('two', {'accountId': 'missing'}))

    def test_closed_profile_required_for_changed_binding(self):
        self.running()
        self.assertError(409, lambda: self.registry.bind('one', {'accountId': self.aid}))
        self.assertEqual(self.worker.profile('one')['loginAccountId'], '')

    def test_identity_change_and_delete_require_unbinding(self):
        self.registry.bind('one', {'accountId': self.aid})
        self.assertError(409, lambda: self.registry.upsert({'id': self.aid, 'email': 'new@example.invalid', 'password': 'new'}))
        self.assertError(409, lambda: self.registry.delete(self.aid))
        self.registry.bind('one', {'accountId': ''})
        self.registry.delete(self.aid)
        self.assertEqual(self.registry.list()['accounts'], [])
        self.assertEqual(self.worker.profile('one')['loginAccountId'], '')

    def test_disk_failures_leave_original_data(self):
        before = self.registry.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('hidden')):
            self.assertError(500, lambda: self.registry.upsert({'id': self.aid, 'name': 'unsaved'}))
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.registry.entry(self.aid)['name'], 'Test')
        with patch.object(self.worker, 'save', side_effect=OSError('hidden')):
            self.assertError(500, lambda: self.registry.bind('one', {'accountId': self.aid}))
        self.assertEqual(self.worker.profile('one')['loginAccountId'], '')

    def test_missing_or_invalid_plaintext_password_is_rejected(self):
        for value in (None, '', 123):
            self.registry.entries[self.aid]['password'] = value
            self.assertError(409, lambda: self.registry.credentials(self.aid))
        del self.registry.entries[self.aid]['password']
        self.assertError(409, lambda: self.registry.credentials(self.aid))

    def test_bound_login_uses_saved_account_secret_without_exposing_it(self):
        self.registry.bind('one', {'accountId': self.aid})
        self.running()
        captured = []
        finished = threading.Event()
        def run(pid, job, runtime, generation, email, password, resume):
            captured.append((email, password))
            self.worker.google_login._update(pid, job, 'needs_attention', 'verification_required')
            finished.set()
        with patch.object(self.worker.google_login, '_run', side_effect=run):
            result = self.worker.google_login.start('one', {'email': 'test@example.invalid', 'useSaved': True})
            self.assertTrue(finished.wait(2))
        self.assertEqual(captured, [('test@example.invalid', 'fixture-only-password')])
        self.assertNotIn('fixture-only-password', json.dumps(result))
        self.assertNotIn('test@example.invalid', json.dumps(result))
        info = self.worker.google_login.credential_info('one')
        self.assertEqual(info['source'], 'account')
        self.assertNotIn('password', info)
        self.assertError(400, lambda: self.worker.google_login.start('one', {'email': 'wrong@example.invalid', 'useSaved': True}))
        self.assertError(409, lambda: self.worker.google_login.forget('one'))

    def test_active_login_blocks_account_edits(self):
        self.worker.google_login.jobs['one'] = {'state': 'running', 'loginAccountId': self.aid, 'cancel': threading.Event()}
        self.assertError(409, lambda: self.registry.upsert({'id': self.aid, 'password': 'changed'}))
        self.assertError(409, lambda: self.registry.delete(self.aid))

    def test_bound_existing_session_does_not_claim_account_switch(self):
        self.registry.bind('one', {'accountId': self.aid})
        runtime = self.running()
        page = {'id': 'dola', 'url': 'https://www.dola.com/chat/'}
        job = {'id': 'fixture', 'state': 'running', 'cancel': threading.Event(), 'loginAccountId': self.aid}
        self.worker.google_login.jobs['one'] = job
        with patch.object(self.worker, 'target', return_value=page), patch.object(self.worker.google_login, '_pages', return_value=[page]), patch.object(self.worker.google_login, '_confirm', return_value=True):
            self.worker.google_login._run('one', job, runtime, 0, 'test@example.invalid', 'fixture-only-password')
        self.assertEqual((job['state'], job['stage']), ('needs_attention', 'existing_session_unverified'))

    def test_explicit_legacy_migration_preserves_original(self):
        self.worker.google_login.vault.save('one', 'legacy@example.invalid', 'legacy-secret')
        before = self.worker.google_login.vault.path.read_bytes()
        result = self.registry.migrate()
        self.assertEqual(result, {'imported': 1, 'skipped': 0})
        self.assertTrue(self.worker.profile('one')['loginAccountId'])
        self.assertEqual(self.worker.google_login.vault.path.read_bytes(), before)
        self.assertEqual(self.registry.migrate()['imported'], 0)

    def test_http_flow_auth_preview_binding_and_public_state(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.worker, 'fixture-token'))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        def request(path, body=None, **headers):
            req = Request(f'http://127.0.0.1:{server.server_port}/{path}', data=json.dumps(body).encode() if body is not None else None,
                          headers={'Authorization': 'Bearer fixture-token', 'Content-Type': 'application/json', **headers})
            with urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            inventory = request('login-accounts')['accounts']
            self.assertEqual(len(inventory), 1)
            self.assertEqual(inventory[0]['password'], 'fixture-only-password')
            created = request('login-accounts', {'email': 'http@example.invalid', 'password': 'http-secret'})
            state = request('profiles', {'id': 'http', 'name': 'HTTP browser', 'loginAccountId': created['id']})
            self.assertEqual(state['loginAccount']['id'], created['id'])
            self.assertNotIn('http-secret', json.dumps(request('profiles')))
            for headers, status in (({'Authorization': ''}, 401), ({'Origin': 'https://evil.invalid'}, 403)):
                with self.assertRaises(HTTPError) as raised:
                    request('login-accounts', **headers)
                self.assertEqual(raised.exception.code, status)
            request('profiles/http/login-account', {'accountId': ''})
            request('login-accounts/' + created['id'] + '/delete', {})
            self.assertEqual(len(request('login-accounts')['accounts']), 1)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()

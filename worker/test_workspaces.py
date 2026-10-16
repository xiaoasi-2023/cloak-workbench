"""Tenant isolation, batch effects, local account integration and authenticated HTTP."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from workspace_fixture import DirectoryFixture, fixture_key, raw_user
from workspace_api import DirectoryClient, DirectoryError, TOKEN_URI, validate_key
from server import BrowserWorker, ApiError, make_handler


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.registry = self.worker.workspaces
        self.fixture = DirectoryFixture()
        self.registry.client_factory = self.fixture.connect
        self.alpha = self.connection('Alpha', 'admin@alpha.invalid')['id']
        self.beta = self.connection('Beta', 'admin@beta.invalid')['id']

    def tearDown(self):
        self.worker.shutdown()
        self.temp.cleanup()

    def connection(self, name, email, **extra):
        return self.registry.upsert(dict(name=name, adminEmail=email, keyJson=fixture_key(), **extra))

    def finish(self, snapshot):
        for _ in range(250):
            state = self.registry.snapshot(snapshot['id'])
            if state['state'] not in ('queued', 'running', 'cancelling'):
                return state
            time.sleep(.01)
        self.fail('Workspace job timed out')

    def ready(self, wid=None, sync=False):
        wid = wid or self.alpha
        self.assertEqual(self.finish(self.registry.start(wid, 'check'))['state'], 'completed')
        if sync:
            self.assertEqual(self.finish(self.registry.start(wid, 'sync'))['state'], 'completed')

    def preview(self, wid=None, **extra):
        return self.registry.preview(wid or self.alpha, dict(operation='create', count=2, prefix='employee', domain='alpha.invalid', password='fixture-password', **extra))

    def execute(self, draft, wid=None):
        return self.finish(self.registry.start(wid or self.alpha, 'execute', {'draftId': draft['draftId']}))

    def assertError(self, status, fn):
        with self.assertRaises(ApiError) as raised:
            fn()
        self.assertEqual(raised.exception.status, status)

    def test_key_validation_pins_endpoint_and_sanitizes_errors(self):
        self.assertEqual(validate_key(fixture_key())['token_uri'], TOKEN_URI)
        for data in ('bad', '{}', json.dumps({'type': 'api_key', 'private_key': 'never-expose-this'})):
            with self.assertRaises(DirectoryError) as raised:
                validate_key(data)
            self.assertNotIn('never-expose-this', str(raised.exception))
        output = json.dumps(self.registry.list())
        self.assertNotIn('BEGIN PRIVATE KEY', output)
        self.assertNotIn('malicious.invalid', output)

    def test_connection_customer_pin_verified_domains_and_reload(self):
        self.ready()
        record = self.registry.entry(self.alpha)
        self.assertEqual(record['customerId'], 'customer-alpha')
        self.assertEqual(record['domains'], ['alpha.invalid'])
        reloaded = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.assertEqual(reloaded.workspaces.list()['workspaces'][0]['status'], 'connected')
        reloaded.shutdown()
        self.fixture.customer_override = 'customer-beta'
        result = self.finish(self.registry.start(self.alpha, 'check'))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(self.registry.entry(self.alpha)['customerId'], 'customer-alpha')

    def test_disabled_and_unchecked_connections_cannot_mutate(self):
        self.assertError(409, lambda: self.preview())
        record = self.registry.upsert({'id': self.alpha, 'name': 'Alpha', 'adminEmail': 'admin@alpha.invalid', 'enabled': False})
        self.assertFalse(record['enabled'])
        self.assertError(409, lambda: self.registry.start(self.alpha, 'check'))

    def test_unchanged_and_name_only_saves_preserve_verified_connection(self):
        self.ready()
        before = self.registry.public(self.registry.entry(self.alpha))
        payload = dict(id=self.alpha, name='Alpha', adminEmail='admin@alpha.invalid', expectedRevision=before['revision'])
        unchanged = self.registry.upsert(payload)
        self.assertEqual(unchanged['revision'], before['revision'])
        renamed = self.registry.upsert(dict(payload, name='Renamed'))
        self.assertEqual(renamed['status'], 'connected')
        self.assertEqual(renamed['checkedAt'], before['checkedAt'])
        self.assertEqual(renamed['customerId'], before['customerId'])

    def test_stale_editor_cannot_overwrite_new_network_route(self):
        before = self.registry.public(self.registry.entry(self.alpha))
        proxy = self.worker.catalog.upsert({'name': 'Fixture route', 'proxy': 'http://127.0.0.1:7897'})
        current = self.registry.upsert(dict(id=self.alpha, name='Alpha', adminEmail='admin@alpha.invalid', proxyId=proxy['id']))
        self.assertError(409, lambda: self.registry.upsert(dict(id=self.alpha, name='Alpha', adminEmail='admin@alpha.invalid',
                                                              proxyId='', expectedRevision=before['revision'])))
        self.assertEqual(self.registry.entry(self.alpha)['proxyId'], proxy['id'])
        omitted = self.registry.upsert(dict(id=self.alpha, name='Renamed', adminEmail='admin@alpha.invalid', expectedRevision=current['revision']))
        self.assertEqual(omitted['proxyId'], proxy['id'])
        self.assertEqual(omitted['status'], 'unchecked')

    def test_public_connection_reports_live_job_instead_of_queued_disk_snapshot(self):
        with self.worker.lock:
            job = self.registry.start(self.alpha, 'check')
            self.registry.jobs[job['id']]['phase'] = 'fixture-live-phase'
            self.registry.jobs[job['id']]['state'] = 'running'
            public = self.registry.public(self.registry.entry(self.alpha))
            self.assertEqual(public['lastJob']['state'], 'running')
            self.assertEqual(public['lastJob']['phase'], 'fixture-live-phase')
            self.assertNotIn('cancel', public['lastJob'])
            self.assertEqual(self.registry.entry(self.alpha)['lastJob']['state'], 'queued')
        self.finish(job)

    def test_same_customer_cannot_be_accidentally_added_as_another_enterprise(self):
        self.ready()
        duplicate = self.connection('Duplicate', 'other-admin@alpha.invalid')['id']
        result = self.finish(self.registry.start(duplicate, 'check'))
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(self.registry.entry(duplicate).get('customerId'))

    def test_sync_imports_passwordless_accounts_and_preserves_known_passwords(self):
        local = self.worker.login_accounts.upsert({'email': 'existing@alpha.invalid', 'password': 'known-fixture'})
        self.ready(sync=True)
        stored = self.worker.login_accounts.entry(local['id'])
        self.assertEqual(stored['password'], 'known-fixture')
        self.assertEqual(stored['workspaceId'], self.alpha)
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], 'known-fixture')
        self.ready(self.beta, sync=True)
        other = next(a for a in self.worker.login_accounts.entries.values() if a['workspaceId'] == self.beta)
        self.assertEqual(other['password'], '')
        self.assertError(409, lambda: self.worker.login_accounts.credentials(other['id']))
        self.assertEqual(self.registry.users(self.beta)['users'][0]['password'], '')

    def test_create_partial_success_only_confirmed_rows_save_passwords(self):
        self.ready()
        self.fixture.fail_email = 'employee002@alpha.invalid'
        draft = self.preview()
        self.assertNotIn('fixture-password', json.dumps(draft))
        result = self.execute(draft)
        self.assertEqual([r['success'] for r in result['results']], [True, False])
        self.assertEqual(len(self.worker.login_accounts.entries), 1)
        account = next(iter(self.worker.login_accounts.entries.values()))
        self.assertEqual(account['password'], 'fixture-password')
        self.assertFalse(self.fixture.calls[-2][2]['changePasswordAtNextLogin'])
        self.assertNotIn('fixture-password', json.dumps(result))
        self.assertNotIn('fixture-password', json.dumps(self.registry.entry(self.alpha)))
        self.assertEqual([u['password'] for u in self.registry.users(self.alpha)['users']], ['fixture-password'])

    def test_human_preview_names_match_emails_and_execute_uses_fixed_rows(self):
        self.ready(sync=True)
        calls = len(self.fixture.calls)
        draft = self.preview(addressStyle='human', start='ignored', lastName='Ignored')
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual((draft['valid'], draft['invalid']), (2, 0))
        self.assertNotIn('fixture-password', json.dumps(draft))
        for row in draft['rows']:
            self.assertRegex(row['email'], r'^[a-z]+\d{4}@alpha\.invalid$')
            self.assertEqual(row['email'].split('@')[0][:-4], (row['firstName'] + row['lastName']).lower())
        expected = {r['email']: (r['firstName'], r['lastName']) for r in draft['rows']}
        self.assertEqual(len(expected), 2)
        with patch('employee_names.secrets.choice', side_effect=AssertionError('Execution must not generate names')):
            result = self.execute(draft)
        self.assertTrue(all(r['success'] for r in result['results']))
        created = [c for c in self.fixture.calls if c[0] == 'create']
        self.assertEqual({c[1] for c in created}, set(expected))
        for _, email, body in created:
            self.assertEqual((body['name']['givenName'], body['name']['familyName']), expected[email])
        reloaded = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        try:
            self.assertTrue(set(expected) <= {a['email'] for a in reloaded.login_accounts.entries.values()})
        finally:
            reloaded.shutdown()

    def test_human_generation_skips_directory_local_batch_and_open_preview_collisions(self):
        self.fixture.data['customer-alpha'].append(raw_user('ALICEADAMS0000@alpha.invalid'))
        self.worker.login_accounts.upsert({'email': 'aliceadams0001@alpha.invalid', 'password': 'fixture-pass'})
        self.ready(sync=True)
        with patch('employee_names.secrets.choice', side_effect=lambda names: names[0]), \
                patch('employee_names.secrets.randbelow', side_effect=[0, 1, 2, 2, 3, 2, 3, 4]):
            first = self.preview(addressStyle='human')
            second = self.registry.preview(self.alpha, dict(operation='create', addressStyle='human', count=1,
                domain='alpha.invalid', password='fixture-pass'))
        self.assertEqual([r['email'] for r in first['rows']], ['aliceadams0002@alpha.invalid', 'aliceadams0003@alpha.invalid'])
        self.assertEqual(second['rows'][0]['email'], 'aliceadams0004@alpha.invalid')

    def test_human_generation_exhaustion_fails_without_creating_or_returning_duplicate(self):
        self.ready()
        self.worker.login_accounts.upsert({'email': 'aliceadams0000@alpha.invalid', 'password': 'fixture-pass'})
        with patch('employee_names.secrets.choice', side_effect=lambda names: names[0]), \
                patch('employee_names.secrets.randbelow', return_value=0):
            self.assertError(409, lambda: self.preview(addressStyle='human'))
        self.assertFalse(self.registry.drafts)
        self.assertFalse(any(c[0] == 'create' for c in self.fixture.calls))

    def test_human_creation_failure_or_unknown_result_never_regenerates_or_retries(self):
        self.ready()
        draft = self.preview(addressStyle='human')
        self.fixture.fail_email, self.fixture.uncertain_email = [r['email'] for r in draft['rows']]
        result = self.execute(draft)
        self.assertTrue(all(not r['success'] for r in result['results']))
        self.assertTrue(result['results'][1]['uncertain'])
        self.assertEqual([c[1] for c in self.fixture.calls if c[0] == 'create'], [r['email'] for r in draft['rows']])
        self.assertFalse(self.worker.login_accounts.entries)

    def test_human_generation_validates_options_and_supports_full_batch(self):
        self.ready()
        for options in ({'count': 0}, {'count': 201}, {'count': True}, {'addressStyle': 'other'},
                        {'domain': 'foreign.invalid'}, {'domain': None}):
            payload = dict(operation='create', addressStyle='human', count=1, domain='alpha.invalid', password='fixture-pass')
            payload.update(options)
            self.assertError(400, lambda: self.registry.preview(self.alpha, payload))
        draft = self.registry.preview(self.alpha, dict(operation='create', addressStyle='human', count=200,
            domain='alpha.invalid', password='fixture-pass'))
        self.assertEqual(draft['valid'], 200)
        self.assertEqual(len({r['email'] for r in draft['rows']}), 200)

    def test_csv_and_explicit_rows_are_not_changed_by_human_mode(self):
        self.ready()
        payload = dict(operation='create', addressStyle='human', csv='email,firstName,lastName,password\nprovided@alpha.invalid,Given,Family,fixture-pass')
        draft = self.registry.preview(self.alpha, payload)
        self.assertEqual((draft['rows'][0]['email'], draft['rows'][0]['firstName'], draft['rows'][0]['lastName']),
                         ('provided@alpha.invalid', 'Given', 'Family'))
        del payload['csv']
        payload['rows'] = [dict(email='explicit@alpha.invalid', firstName='Explicit', lastName='Employee', password='fixture-pass')]
        draft = self.registry.preview(self.alpha, payload)
        self.assertEqual(draft['rows'][0]['email'], 'explicit@alpha.invalid')

    def test_sync_marks_removed_users_without_deleting_local_browser_or_password(self):
        self.ready(sync=True)
        account = next(iter(self.worker.login_accounts.entries.values()))
        self.worker.login_accounts.upsert({'id': account['id'], 'password': 'known-fixture-password'})
        self.worker.create({'id': 'kept-browser', 'name': 'Keep me', 'loginAccountId': account['id']})
        self.fixture.data['customer-alpha'].clear()
        self.finish(self.registry.start(self.alpha, 'sync'))
        local = self.worker.login_accounts.entry(account['id'])
        self.assertTrue(local['workspaceMissing'])
        self.assertEqual(local['password'], 'known-fixture-password')
        self.assertIn('kept-browser', self.worker.profiles)
        self.assertError(409, lambda: self.worker.login_accounts.credentials(account['id']))

    def test_cross_tenant_draft_rejected_and_single_use(self):
        self.ready(); self.ready(self.beta)
        draft = self.preview()
        self.assertError(409, lambda: self.registry.start(self.beta, 'execute', {'draftId': draft['draftId']}))
        self.assertEqual(self.execute(draft)['completed'], 2)
        self.assertError(409, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']}))

    def test_stale_revision_and_expired_drafts_rejected(self):
        self.ready()
        draft = self.preview()
        self.registry.drafts[draft['draftId']]['expires'] = 0
        self.assertError(409, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']}))
        draft = self.preview()
        self.registry.upsert({'id': self.alpha, 'name': 'Changed', 'adminEmail': 'admin@alpha.invalid'})
        self.ready()
        self.assertError(409, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']}))

    def test_preview_rejects_foreign_domain_duplicate_and_bad_password_rows(self):
        self.ready(sync=True)
        rows = [{'email': 'x@beta.invalid', 'password': 'fixture-password', 'firstName': 'X', 'lastName': 'Y'},
                {'email': 'existing@alpha.invalid', 'password': 'fixture-password', 'firstName': 'X', 'lastName': 'Y'},
                {'email': 'z@alpha.invalid', 'password': 'short', 'firstName': 'X', 'lastName': 'Y'}]
        draft = self.registry.preview(self.alpha, {'operation': 'create', 'rows': rows})
        self.assertEqual((draft['valid'], draft['invalid']), (0, 3))
        self.assertError(400, lambda: self.execute(draft))

    def test_csv_creation_and_first_login_flag(self):
        self.ready()
        draft = self.registry.preview(self.alpha, {'operation': 'create', 'csv': '\ufeffemail,firstName,lastName,password,department,title,recoveryEmail\nnew@alpha.invalid,New,User,fixture-pass,IT,Engineer,recovery@example.invalid', 'changePasswordAtNextLogin': True})
        result = self.execute(draft)
        self.assertTrue(result['results'][0]['success'])
        created = self.fixture.data['customer-alpha'][-1]
        self.assertTrue(created['changePasswordAtNextLogin'])
        self.assertEqual(created['organizations'][0]['department'], 'IT')

    def test_password_reset_updates_local_only_on_google_success(self):
        self.ready(sync=True)
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], '')
        draft = self.registry.preview(self.alpha, {'operation': 'reset', 'userIds': ['existing'], 'password': 'rejected-fixture-password'})
        with patch.object(self.registry, 'client_factory', side_effect=DirectoryError('Fixture refusal')):
            self.assertEqual(self.execute(draft)['state'], 'failed')
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], '')
        draft = self.registry.preview(self.alpha, {'operation': 'reset', 'userIds': ['existing'], 'password': 'new-fixture-password'})
        self.execute(draft)
        self.assertEqual(next(iter(self.worker.login_accounts.entries.values()))['password'], 'new-fixture-password')
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], 'new-fixture-password')
        self.finish(self.registry.start(self.alpha, 'sync'))
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], 'new-fixture-password')
        restored = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        try:
            self.assertEqual(restored.workspaces.users(self.alpha)['users'][0]['password'], 'new-fixture-password')
        finally:
            restored.shutdown()

    def test_directory_password_requires_matching_enterprise_user_and_email(self):
        self.ready(sync=True)
        self.ready(self.beta, sync=True)
        account = next(a for a in self.worker.login_accounts.entries.values() if a['workspaceId'] == self.alpha)
        account['password'] = 'private-fixture-password'
        self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], 'private-fixture-password')
        self.assertEqual(self.registry.users(self.beta)['users'][0]['password'], '')
        for key, value in (('workspaceId', self.beta), ('workspaceUserId', 'replaced-id'),
                           ('email', 'renamed@alpha.invalid'), ('workspaceMissing', True)):
            with self.subTest(field=key):
                original = account.get(key)
                account[key] = value
                self.assertEqual(self.registry.users(self.alpha)['users'][0]['password'], '')
                account[key] = original
        self.assertNotIn('private-fixture-password', json.dumps(self.registry.entry(self.alpha)))
        self.assertNotIn('private-fixture-password', json.dumps(self.registry.list()))
        response = self.registry.users(self.alpha)
        response['users'][0]['password'] = 'response-only'
        self.assertEqual(account['password'], 'private-fixture-password')

    def test_delete_requires_tenant_confirmation_and_preserves_local_account_and_browser(self):
        for email, extra in (('admin@alpha.invalid', {}), ('super@alpha.invalid', {'isAdmin': True}),
                             ('delegated@alpha.invalid', {'isDelegatedAdmin': True})):
            self.fixture.data['customer-alpha'].append(raw_user(email, **extra))
        self.ready(sync=True)
        account = next(a for a in self.worker.login_accounts.entries.values() if a['email'] == 'existing@alpha.invalid')
        self.worker.login_accounts.upsert({'id': account['id'], 'password': 'kept-fixture-password'})
        self.worker.create({'id': 'kept-browser', 'name': 'Keep browser', 'loginAccountId': account['id']})
        draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['existing', 'admin', 'super', 'delegated']})
        self.assertEqual((draft['valid'], draft['invalid']), (1, 3))
        for name in (None, 'Wrong enterprise'):
            self.assertError(400, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': name}))
        self.assertFalse(any(c[0] == 'delete' for c in self.fixture.calls))
        result = self.finish(self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))
        self.assertTrue(result['results'][0]['success'])
        self.assertNotIn('existing', [u['id'] for u in self.registry.users(self.alpha)['users']])
        local = self.worker.login_accounts.entry(account['id'])
        self.assertTrue(local['workspaceMissing'])
        self.assertEqual(local['password'], 'kept-fixture-password')
        self.assertEqual(self.worker.profiles['kept-browser']['loginAccountId'], account['id'])
        self.assertError(409, lambda: self.worker.login_accounts.credentials(account['id']))
        self.finish(self.registry.start(self.alpha, 'sync'))
        self.assertEqual(self.worker.login_accounts.entry(account['id'])['password'], 'kept-fixture-password')
        self.assertError(409, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))

    def test_delete_rechecks_live_admin_roles_and_employee_identity(self):
        self.ready(sync=True)
        for changes in ({'isAdmin': True}, {'isDelegatedAdmin': True}, {'primaryEmail': 'renamed@alpha.invalid'}, {'customerId': 'customer-beta'}):
            with self.subTest(changes=changes):
                draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['existing']})
                original = dict(self.fixture.data['customer-alpha'][0])
                self.fixture.data['customer-alpha'][0].update(changes)
                result = self.finish(self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))
                self.assertFalse(result['results'][0]['success'])
                self.assertFalse(any(c[0] == 'delete' for c in self.fixture.calls))
                self.fixture.data['customer-alpha'][0] = original

    def test_delete_partial_failure_and_unknown_results_do_not_remove_other_users_or_retry(self):
        self.fixture.data['customer-alpha'].append(raw_user('second@alpha.invalid'))
        self.ready(sync=True)
        self.fixture.fail_email = 'second@alpha.invalid'
        draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['existing', 'second']})
        self.ready(self.beta)
        self.assertError(409, lambda: self.registry.start(self.beta, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Beta'}))
        result = self.finish(self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))
        self.assertEqual([r['success'] for r in result['results']], [True, False])
        self.assertEqual([u['id'] for u in self.registry.users(self.alpha)['users']], ['second'])
        self.fixture.fail_email = None; self.fixture.uncertain_email = 'second@alpha.invalid'
        draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['second']})
        result = self.finish(self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))
        self.assertTrue(result['results'][0]['uncertain'])
        self.assertEqual(sum(c == ('delete', 'second') for c in self.fixture.calls), 2)
        self.assertEqual(len(self.registry.users(self.alpha)['users']), 1)
        self.assertFalse(next(a for a in self.worker.login_accounts.entries.values() if a['workspaceUserId'] == 'second')['workspaceMissing'])

    def test_delete_cancel_before_cloud_write_keeps_employee(self):
        self.ready(sync=True)
        draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['existing']})
        self.fixture.entered.clear(); self.fixture.gate = threading.Event()
        job = self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'})
        try:
            self.assertTrue(self.fixture.entered.wait(2))
            self.registry.cancel(job['id'])
        finally:
            self.fixture.gate.set()
        self.assertEqual(self.finish(job)['state'], 'cancelled')
        self.assertFalse(any(c[0] == 'delete' for c in self.fixture.calls))
        self.assertEqual(len(self.registry.users(self.alpha)['users']), 1)

    def test_delete_local_write_failure_disables_login_after_restart(self):
        self.ready(sync=True)
        account = next(iter(self.worker.login_accounts.entries.values()))
        self.worker.login_accounts.upsert({'id': account['id'], 'password': 'kept-fixture-password'})
        draft = self.registry.preview(self.alpha, {'operation': 'delete', 'userIds': ['existing']})
        with patch.object(self.worker.login_accounts, '_write', side_effect=OSError('Fixture disk failure')):
            result = self.finish(self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId'], 'confirmWorkspaceName': 'Alpha'}))
        self.assertTrue(result['results'][0]['cloudApplied'])
        self.assertFalse(result['results'][0]['success'])
        self.assertError(409, lambda: self.worker.login_accounts.credentials(account['id']))
        restored = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        try:
            self.assertError(409, lambda: restored.login_accounts.credentials(account['id']))
            self.assertEqual(restored.login_accounts.entry(account['id'])['password'], 'kept-fixture-password')
        finally:
            restored.shutdown()

    def test_profile_patch_preserves_existing_organization_fields(self):
        self.fixture.data['customer-alpha'][0]['organizations'] = [{'name': 'Company', 'department': 'Old', 'title': 'Engineer', 'primary': True}]
        self.ready(sync=True)
        draft = self.registry.preview(self.alpha, {'operation': 'update', 'userIds': ['existing'], 'patch': {'department': 'New'}})
        self.execute(draft)
        organization = self.fixture.data['customer-alpha'][0]['organizations'][0]
        self.assertEqual((organization['name'], organization['department'], organization['title']), ('Company', 'New', 'Engineer'))

    def test_changed_directory_invalidates_pending_employee_preview(self):
        self.fixture.data['customer-alpha'][0]['organizations'] = [{'department': 'Original', 'title': 'Engineer', 'primary': True}]
        self.ready(sync=True)
        draft = self.registry.preview(self.alpha, {'operation': 'update', 'userIds': ['existing'], 'patch': {'department': 'New'}})
        self.fixture.data['customer-alpha'][0]['organizations'][0]['title'] = 'Manager'
        self.finish(self.registry.start(self.alpha, 'sync'))
        self.assertError(409, lambda: self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']}))
        self.assertFalse(any(call[0] == 'update' for call in self.fixture.calls))

    def test_unchanged_directory_sync_keeps_pending_preview_valid(self):
        self.ready(sync=True)
        draft = self.registry.preview(self.alpha, {'operation': 'update', 'userIds': ['existing'], 'patch': {'department': 'New'}})
        self.finish(self.registry.start(self.alpha, 'sync'))
        self.assertTrue(self.execute(draft)['results'][0]['success'])

    def test_suspend_resume_tracks_status_and_protects_admin(self):
        self.fixture.data['customer-alpha'].append(raw_user('admin@alpha.invalid'))
        self.ready(sync=True)
        draft = self.registry.preview(self.alpha, {'operation': 'suspend', 'userIds': ['existing', 'admin']})
        self.assertEqual((draft['valid'], draft['invalid']), (1, 1))
        self.execute(draft)
        local = next(a for a in self.worker.login_accounts.entries.values() if a['email'] == 'existing@alpha.invalid')
        self.assertTrue(local['workspaceSuspended'])
        draft = self.registry.preview(self.alpha, {'operation': 'resume', 'userIds': ['existing']})
        self.execute(draft)
        self.assertFalse(self.worker.login_accounts.entry(local['id'])['workspaceSuspended'])

    def test_active_login_blocks_remote_changes_and_config_edit(self):
        self.ready(sync=True)
        account = next(iter(self.worker.login_accounts.entries.values()))
        self.worker.google_login.jobs['fixture'] = {'loginAccountId': account['id'], 'state': 'running', 'cancel': threading.Event()}
        draft = self.registry.preview(self.alpha, {'operation': 'reset', 'userIds': ['existing'], 'password': 'new-fixture-pass'})
        self.assertEqual(draft['valid'], 0)
        self.worker.google_login.jobs.clear()
        self.fixture.gate = threading.Event()
        job = self.registry.start(self.alpha, 'sync')
        self.assertError(409, lambda: self.registry.upsert({'id': self.alpha, 'name': 'Edit', 'adminEmail': 'admin@alpha.invalid'}))
        self.assertError(409, lambda: self.worker.login_accounts.upsert({'id': account['id'], 'password': 'replace-fixture'}))
        self.fixture.gate.set(); self.finish(job)

    def test_cancel_stops_remaining_rows_and_drops_secrets(self):
        self.ready()
        self.fixture.latency = .12
        draft = self.preview()
        job = self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']})
        self.registry.cancel(job['id'])
        result = self.finish(job)
        self.assertEqual(result['state'], 'cancelled')
        self.assertLess(result['completed'], 2)
        self.assertNotIn(draft['draftId'], self.registry.drafts)

    def test_pending_creation_reserves_email_against_local_creation(self):
        self.ready()
        draft = self.preview()
        self.fixture.gate = threading.Event()
        job = self.registry.start(self.alpha, 'execute', {'draftId': draft['draftId']})
        try:
            self.assertError(409, lambda: self.worker.login_accounts.upsert({'email': 'employee001@alpha.invalid', 'password': 'local-fixture-pass'}))
            self.assertNotIn('reservedEmails', self.registry.snapshot(job['id']))
        finally:
            self.fixture.gate.set(); self.finish(job)

    def test_network_unknown_result_never_saves_password_or_retries(self):
        self.ready()
        self.fixture.uncertain_email = 'employee001@alpha.invalid'
        result = self.execute(self.preview())
        self.assertTrue(result['results'][0]['uncertain'])
        self.assertEqual(sum(c[0] == 'create' and c[1] == self.fixture.uncertain_email for c in self.fixture.calls), 1)
        self.assertFalse(any(a['email'] == self.fixture.uncertain_email for a in self.worker.login_accounts.entries.values()))

    def test_cloud_success_local_save_failure_explicitly_reported(self):
        self.ready()
        draft = self.preview()
        with patch.object(self.worker.login_accounts, '_write', side_effect=OSError('hidden-fixture-secret')):
            result = self.execute(draft)
        self.assertTrue(all(r.get('cloudApplied') and not r['success'] for r in result['results']))
        self.assertNotIn('hidden-fixture-secret', json.dumps(result))
        self.assertEqual(len(self.worker.login_accounts.entries), 0)

    def test_proxy_reference_and_inflight_route_protection(self):
        proxy = self.worker.catalog.upsert({'name': 'Fixture route', 'proxy': 'http://127.0.0.1:7897'})
        record = self.registry.upsert({'id': self.alpha, 'name': 'Alpha', 'adminEmail': 'admin@alpha.invalid', 'proxyId': proxy['id']})
        self.assertError(409, lambda: self.worker.catalog.delete(proxy['id']))
        self.fixture.gate = threading.Event()
        job = self.registry.start(self.alpha, 'check')
        self.assertError(409, lambda: self.worker.catalog.upsert({'id': proxy['id'], 'name': 'Edit', 'proxy': 'http://127.0.0.1:8888'}))
        self.fixture.gate.set(); self.finish(job)
        self.assertEqual(self.fixture.routes[-1]['proxy'], 'http://127.0.0.1:7897')

    def test_interrupted_job_persists_uncertain_notice_without_password(self):
        self.ready()
        record = self.registry.entry(self.alpha)
        self.registry._commit(self.alpha, dict(record, lastJob={'id': 'old', 'state': 'running', 'results': []}))
        fresh = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        result = fresh.workspaces.entry(self.alpha)['lastJob']
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue(result['results'][-1]['uncertain'])
        fresh.shutdown()

    def test_http_connection_preview_execute_auth_and_origin(self):
        self.ready()
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.worker, 'fixture-token'))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True); thread.start()
        opener = build_opener(ProxyHandler({}))
        def request(path, payload=None, **headers):
            req = Request(f'http://127.0.0.1:{server.server_port}/{path}', data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Authorization': 'Bearer fixture-token', 'Content-Type': 'application/json', **headers})
            with opener.open(req, timeout=3) as response:
                return json.load(response)
        try:
            self.assertEqual(len(request('workspaces')['workspaces']), 2)
            draft = request(f'workspaces/{self.alpha}/preview', {'operation': 'create', 'count': 1, 'prefix': 'http', 'domain': 'alpha.invalid', 'password': 'http-fixture-pass'})
            self.finish(request(f'workspaces/{self.alpha}/execute', {'draftId': draft['draftId']}))
            self.assertEqual(len(request(f'workspaces/{self.alpha}/users')['users']), 1)
            for header, status in (({'Authorization': ''}, 401), ({'Origin': 'https://evil.invalid'}, 403)):
                with self.assertRaises(HTTPError) as raised:
                    request('workspaces', **header)
                self.assertEqual(raised.exception.code, status)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


class DirectoryTransportTests(unittest.TestCase):
    def client(self):
        record = {'key': validate_key(fixture_key()), 'adminEmail': 'admin@alpha.invalid'}
        return DirectoryClient(record, {})

    def test_fixed_https_endpoints_direct_route_and_no_redirects(self):
        client = self.client()
        response = Mock(status_code=200, content=b'{}')
        response.json.return_value = {'customerId': 'fixture'}
        client.session.request = Mock(return_value=response)
        try:
            self.assertFalse(client.session.trust_env)
            self.assertFalse(client.auth_session.trust_env)
            client.identity('admin@alpha.invalid')
            args, kwargs = client.session.request.call_args
            self.assertEqual(args, ('GET', 'https://admin.googleapis.com/admin/directory/v1/users/admin%40alpha.invalid'))
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(client.session._max_refresh_attempts, 0)
        finally:
            client.close()

    def test_mutation_timeout_is_uncertain_and_never_replayed(self):
        client = self.client()
        client.session.request = Mock(side_effect=TimeoutError('hidden password fixture'))
        try:
            with self.assertRaises(DirectoryError) as raised:
                client.create({'primaryEmail': 'test@alpha.invalid'})
            self.assertTrue(raised.exception.uncertain)
            self.assertEqual(client.session.request.call_count, 1)
            self.assertNotIn('hidden password', str(raised.exception))
        finally:
            client.close()

    def test_delete_uses_encoded_user_id_empty_body_and_accepts_no_content(self):
        client = self.client()
        response = Mock(status_code=204, content=b'')
        client.session.request = Mock(return_value=response)
        try:
            self.assertEqual(client.delete('user/id'), {})
            args, kwargs = client.session.request.call_args
            self.assertEqual(args, ('DELETE', 'https://admin.googleapis.com/admin/directory/v1/users/user%2Fid'))
            self.assertIsNone(kwargs['json'])
            self.assertFalse(kwargs['allow_redirects'])
            response.json.assert_not_called()
            client.session.request.side_effect = TimeoutError('fixture')
            with self.assertRaises(DirectoryError) as raised:
                client.delete('user/id')
            self.assertTrue(raised.exception.uncertain)
            self.assertEqual(client.session.request.call_count, 2)
        finally:
            client.close()

    def test_token_and_directory_requests_share_explicit_chained_proxy(self):
        record = {'key': validate_key(fixture_key()), 'adminEmail': 'admin@alpha.invalid'}
        relay = Mock(port=42310)
        with patch('workspace_api.ProxyRelay', return_value=relay) as factory:
            client = DirectoryClient(record, {'proxy': 'http://residential.invalid:443', 'proxyUsername': 'fixture-user',
                                              'proxyPassword': 'fixture-password', 'upstreamProxy': 'http://127.0.0.1:7897'})
            try:
                config = factory.call_args.args[0]
                self.assertEqual(config.upstream.port, 7897)
                self.assertEqual(client.auth_session.proxies, {'http': 'http://127.0.0.1:42310', 'https': 'http://127.0.0.1:42310'})
                self.assertEqual(client.session.proxies, client.auth_session.proxies)
            finally:
                client.close()
            relay.close.assert_called_once()

    def test_directory_pagination_keeps_customer_and_page_tokens(self):
        client = self.client()
        client.request = Mock(side_effect=[{'users': [{'id': 'a'}], 'nextPageToken': 'page-two'}, {'users': [{'id': 'b'}]}])
        try:
            self.assertEqual(client.users('customer-alpha', threading.Event()), [{'id': 'a'}, {'id': 'b'}])
            params = client.request.call_args.kwargs['params']
            self.assertEqual((params['customer'], params['pageToken']), ('customer-alpha', 'page-two'))
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()

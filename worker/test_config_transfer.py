"""Portable configuration round trips, auth refresh and rollback; no real Google calls."""
import base64
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from workspace_fixture import fixture_key
from server import BrowserWorker, ApiError, make_handler
from config_transfer import FILES, JOURNAL, atomic, decrypt, encrypt
from workspace_oauth import credentials_for

PASSWORD = 'offline-test-backup-password'
CLIENT = {'installed': {'client_id': '1234567890-offline.apps.googleusercontent.com', 'client_secret': 'offline-client-secret'}}


class ConfigTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.source = BrowserWorker(Path(self.temp.name) / 'source', Path('missing.exe'))
        self.target = BrowserWorker(Path(self.temp.name) / 'target', Path('missing.exe'))
        self.proxy = self.source.catalog.upsert({'name': '住宅线路', 'kind': 'residential',
            'proxy': 'http://proxy.invalid:443', 'proxyUsername': 'resident-user', 'proxyPassword': 'resident-secret',
            'upstreamProxy': 'http://127.0.0.1:7890', 'upstreamProxyUsername': 'up-user', 'upstreamProxyPassword': 'up-secret'})
        self.workspace = self.source.workspaces.upsert({'name': '示例企业', 'adminEmail': 'admin@example.invalid',
            'authType': 'oauth', 'oauthClientJson': json.dumps(CLIENT), 'proxyId': self.proxy['id']})
        record = self.source.workspaces.records[self.workspace['id']]
        record.update(customerId='customer-example', domains=['example.invalid'], status='connected',
            oauth={'token': 'old-access-token', 'refresh_token': 'offline-refresh-secret', 'expiry': '2000-01-01T00:00:00+00:00'},
            users=[{'id': 'employee-id', 'email': 'employee@example.invalid'}], lastJob={'id': 'old-job', 'state': 'completed'})
        self.source.workspaces._save()
        self.account = self.source.login_accounts.upsert({'email': 'employee@example.invalid', 'password': 'employee-secret'})
        self.source.login_accounts.entries[self.account['id']].update(workspaceId=record['id'], workspaceUserId='employee-id', workspaceName=record['name'])
        self.source.login_accounts._write(self.source.login_accounts.entries)
        self.profile = self.source.create({'name': '员工环境', 'proxyId': self.proxy['id'], 'loginAccountId': self.account['id']})

    def tearDown(self):
        self.source.shutdown(); self.target.shutdown(); self.temp.cleanup()

    def export(self, full=False):
        return self.source.config_transfer.export({'password': PASSWORD, 'includeProfiles': full})['text']

    def preview(self, text=None, mode='skip'):
        return self.target.config_transfer.preview({'text': text or self.export(), 'password': PASSWORD, 'mode': mode})

    def apply(self, preview):
        return self.target.config_transfer.apply({'planId': preview['planId'], 'password': PASSWORD})

    def test_default_export_hides_credentials_and_excludes_employee_and_browser_data(self):
        text = self.export()
        for secret in ('offline-refresh-secret', 'resident-secret', 'up-secret', 'offline-client-secret', 'employee-secret'):
            self.assertNotIn(secret, text)
        document = decrypt(text, PASSWORD)
        self.assertEqual(document['accounts'], [])
        self.assertEqual(document['profiles'], [])
        self.assertNotIn('users', document['workspaces'][0])
        self.assertNotIn('lastJob', document['workspaces'][0])
        self.assertEqual(document['workspaces'][0]['oauth'], {'refresh_token': 'offline-refresh-secret'})

    def test_restore_on_new_store_preserves_proxy_chain_and_can_refresh_oauth(self):
        preview = self.preview()
        self.assertNotIn('offline-refresh-secret', json.dumps(preview))
        self.assertEqual(preview['counts']['add'], 2)
        result = self.apply(preview)
        imported = self.target.workspaces.records[self.workspace['id']]
        self.assertEqual(imported['status'], 'unchecked')
        proxy = self.target.catalog.entries[self.proxy['id']]
        self.assertEqual(proxy['upstreamProxyPassword'], 'up-secret')
        self.assertEqual(proxy['proxyPassword'], 'resident-secret')
        credential = credentials_for(imported)
        request = Mock(return_value=Mock(status=200, data=json.dumps({'access_token': 'new-offline-access', 'expires_in': 3600}).encode(), headers={}))
        credential.refresh(request)
        self.assertEqual(credential.token, 'new-offline-access')
        self.assertIn(b'refresh_token=offline-refresh-secret', request.call_args.kwargs['body'])
        self.assertTrue(Path(result['backupPath']).is_file())
        self.assertEqual(decrypt(Path(result['backupPath']).read_text(encoding='utf-8'), PASSWORD)['workspaces'], [])
        restarted = BrowserWorker(self.target.data_dir, Path('missing.exe'))
        try:
            self.assertEqual(restarted.workspaces.records[self.workspace['id']]['oauth']['refresh_token'], 'offline-refresh-secret')
        finally:
            restarted.shutdown()

    def test_full_round_trip_preserves_bindings_without_browser_cookies(self):
        result = self.apply(self.preview(self.export(True)))
        self.assertEqual(result['imported'], 4)
        profile = self.target.profiles[self.profile['id']]
        self.assertEqual(profile['loginAccountId'], self.account['id'])
        self.assertEqual(profile['proxyId'], self.proxy['id'])
        self.assertEqual(self.target.login_accounts.entries[self.account['id']]['password'], 'employee-secret')
        self.assertFalse((self.target.data_dir / self.profile['id'] / 'Cookies').exists())

    def test_new_environment_does_not_adopt_an_orphaned_browser_directory(self):
        orphan = self.target.data_dir / 'profiles' / self.profile['id']
        orphan.mkdir(parents=True)
        (orphan / 'Cookies').write_bytes(b'unrelated-local-cookie-fixture')
        self.apply(self.preview(self.export(True)))
        imported = next(iter(self.target.profiles.values()))
        self.assertNotEqual(imported['id'], self.profile['id'])
        self.assertEqual((orphan / 'Cookies').read_bytes(), b'unrelated-local-cookie-fixture')
        self.assertEqual(imported['loginAccountId'], self.account['id'])

    def test_wrong_password_or_corruption_never_modifies_destination(self):
        text = self.export()
        for value, password in ((text, 'wrong-password'), ('not-json', PASSWORD)):
            with self.assertRaises(ApiError):
                self.target.config_transfer.preview({'text': value, 'password': password})
        envelope = json.loads(text); envelope['ciphertext'] = base64.b64encode(b'0' * 32).decode()
        with self.assertRaises(ApiError):
            self.preview(json.dumps(envelope))
        self.assertEqual(self.target.workspaces.records, {})
        self.assertEqual(self.target.catalog.entries, {})

    def test_skip_then_replace_and_restore_preimport_backup(self):
        self.apply(self.preview())
        self.target.catalog.entries[self.proxy['id']]['proxyPassword'] = 'local-secret'
        self.target.catalog.save()
        self.assertEqual(self.apply(self.preview())['imported'], 0)
        self.assertEqual(self.target.catalog.entries[self.proxy['id']]['proxyPassword'], 'local-secret')
        result = self.apply(self.preview(mode='replace'))
        self.assertEqual(self.target.catalog.entries[self.proxy['id']]['proxyPassword'], 'resident-secret')
        backup = Path(result['backupPath']).read_text(encoding='utf-8')
        self.apply(self.preview(backup, mode='replace'))
        self.assertEqual(self.target.catalog.entries[self.proxy['id']]['proxyPassword'], 'local-secret')

    def test_changed_destination_or_password_invalidates_plan(self):
        plan = self.preview()
        self.target.catalog.upsert({'name': 'local', 'proxy': 'http://127.0.0.1:7891'})
        with self.assertRaises(ApiError):
            self.apply(plan)
        plan = self.preview()
        with self.assertRaises(ApiError):
            self.target.config_transfer.apply({'planId': plan['planId'], 'password': 'different-password'})

    def test_natural_identity_remaps_enterprise_and_account_bindings(self):
        existing = self.target.workspaces.upsert({'name': 'Existing tenant', 'adminEmail': 'admin@example.invalid',
            'authType': 'oauth', 'oauthClientJson': json.dumps(CLIENT)})
        account = self.target.login_accounts.upsert({'email': 'employee@example.invalid', 'password': 'original'})
        self.apply(self.preview(self.export(True), 'replace'))
        self.assertEqual(len(self.target.workspaces.records), 1)
        self.assertEqual(len(self.target.login_accounts.entries), 1)
        self.assertEqual(self.target.profiles[self.profile['id']]['loginAccountId'], account['id'])
        self.assertEqual(self.target.login_accounts.entries[account['id']]['workspaceId'], existing['id'])

    def test_rejects_invalid_references_duplicate_identities_and_binding_conflicts(self):
        document = decrypt(self.export(True), PASSWORD)
        document['profiles'][0]['proxyId'] = 'missing-proxy'
        with self.assertRaises(ApiError):
            self.preview(encrypt(document, PASSWORD))
        document = decrypt(self.export(True), PASSWORD)
        document['accounts'].append(dict(document['accounts'][0], id='duplicate'))
        with self.assertRaises(ApiError):
            self.preview(encrypt(document, PASSWORD))
        account = self.target.login_accounts.upsert({'email': 'employee@example.invalid', 'password': 'original'})
        self.target.create({'name': '本机已有环境', 'loginAccountId': account['id']})
        with self.assertRaises(ApiError):
            self.preview(self.export(True))

    def test_failed_write_rolls_back_disk_and_memory(self):
        self.target.catalog.upsert({'name': '保留线路', 'proxy': 'http://127.0.0.1:7891'})
        before = {name: (self.target.data_dir / name).read_bytes() if (self.target.data_dir / name).exists() else None for name in FILES}
        entries = copy.deepcopy(self.target.config_transfer._stores())
        plan = self.preview(self.export(True))
        failed = False
        def flaky(path, data):
            nonlocal failed
            if path.name == 'workspaces.json' and not failed:
                failed = True
                raise OSError('fixture disk failure')
            return atomic(path, data)
        with patch('config_transfer.atomic', side_effect=flaky):
            with self.assertRaises(ApiError):
                self.apply(plan)
        self.assertEqual(self.target.config_transfer._stores(), entries)
        self.assertFalse((self.target.data_dir / JOURNAL).exists())
        for name, original in before.items():
            path = self.target.data_dir / name
            self.assertEqual(path.read_bytes() if path.exists() else None, original)

    def test_interrupted_import_is_recovered_before_loading_stores(self):
        self.target.catalog.upsert({'name': '保留线路', 'proxy': 'http://127.0.0.1:7891'})
        original = (self.target.data_dir / 'proxies.json').read_bytes()
        originals = {name: base64.b64encode((self.target.data_dir / name).read_bytes()).decode() if (self.target.data_dir / name).exists() else None for name in FILES}
        atomic(self.target.data_dir / JOURNAL, json.dumps(originals).encode())
        atomic(self.target.data_dir / 'proxies.json', b'corrupt-partial-write')
        restarted = BrowserWorker(self.target.data_dir, Path('missing.exe'))
        try:
            self.assertEqual((self.target.data_dir / 'proxies.json').read_bytes(), original)
            self.assertEqual(len(restarted.catalog.entries), 1)
        finally:
            restarted.shutdown()

    def test_active_work_rejects_import_and_service_account_can_migrate(self):
        service = self.source.workspaces.upsert({'name': '服务账号企业', 'adminEmail': 'admin@service.invalid', 'keyJson': fixture_key()})
        plan = self.preview()
        with patch.object(self.target.config_transfer, '_idle', side_effect=ApiError(409, 'busy')):
            with self.assertRaises(ApiError):
                self.apply(plan)
        self.apply(plan)
        self.assertIn('private_key', self.target.workspaces.records[service['id']]['key'])

    def test_running_browser_and_proxy_diagnostic_block_actual_idle_guard(self):
        self.target.create({'name': 'idle-guard fixture'})
        plan = self.preview()
        with patch.object(self.target, 'state', return_value={'state': 'starting'}):
            with self.assertRaises(ApiError):
                self.apply(plan)
        self.target.catalog.inflight.add('synthetic-inflight')
        try:
            with self.assertRaises(ApiError):
                self.apply(plan)
        finally:
            self.target.catalog.inflight.clear()
        self.assertEqual(self.apply(plan)['imported'], 2)

    def test_authenticated_http_round_trip(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.target, 'offline-local-api-token'))
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{http.server_port}/config-transfer/'
        opener = build_opener(ProxyHandler({}))
        def post(route, body, token='offline-local-api-token'):
            request = Request(url + route, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
            with opener.open(request, timeout=10) as response:
                return json.load(response)
        try:
            with self.assertRaises(HTTPError) as error:
                post('preview', {}, token='wrong')
            self.assertEqual(error.exception.code, 401)
            preview = post('preview', {'text': self.export(True), 'password': PASSWORD})
            result = post('import', {'planId': preview['planId'], 'password': PASSWORD})
            self.assertEqual(result['imported'], 4)
            exported = post('export', {'password': PASSWORD})
            self.assertEqual(len(decrypt(exported['text'], PASSWORD)['workspaces']), 1)
        finally:
            http.shutdown(); http.server_close(); thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()

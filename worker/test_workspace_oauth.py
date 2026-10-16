"""Real loopback OAuth callbacks with offline Google and Directory providers."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from workspace_fixture import DirectoryFixture
from server import BrowserWorker, ApiError, make_handler
from workspace_api import DirectoryError, SCOPES, TOKEN_URI, connect
from workspace_oauth import credentials_for, exchange, validate_client


CLIENT = {'installed': {'client_id': '1234567890-offline.apps.googleusercontent.com',
    'client_secret': 'offline-client-secret', 'auth_uri': 'https://untrusted.invalid/auth',
    'token_uri': 'https://untrusted.invalid/token', 'redirect_uris': ['https://untrusted.invalid/callback']}}


def tokens():
    return {'token': 'offline-access-token', 'refresh_token': 'offline-refresh-token',
            'expiry': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.registry = self.worker.workspaces
        self.fixture = DirectoryFixture()
        self.registry.client_factory = self.fixture.connect
        self.registry.oauth_exchange = Mock(side_effect=lambda *args: tokens())
        self.alpha = self.add('Alpha', 'admin@alpha.invalid')['id']
        self.beta = self.add('Beta', 'admin@beta.invalid')['id']
        self.http = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.worker.shutdown()
        for attempt in self.registry.oauth_attempts.values():
            for _ in range(50):
                if attempt.server.fileno() == -1:
                    break
                time.sleep(.01)
        self.temp.cleanup()

    def add(self, name, email, **extra):
        return self.registry.upsert(dict(name=name, adminEmail=email, authType='oauth', oauthClientJson=json.dumps(CLIENT), **extra))

    def start(self, wid=None):
        result = self.registry.oauth_start(wid or self.alpha)
        return result, parse_qs(urlsplit(result['authorizationUrl']).query)

    def callback(self, result, query, **extra):
        params = dict(state=query['state'][0], code='offline-authorization-code', **extra)
        url = query['redirect_uri'][0] + '?' + urlencode(params)
        with self.http.open(url, timeout=10) as response:
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
            response.read()
        return self.registry.oauth_status(result['id'])

    def finish_job(self, job):
        for _ in range(200):
            result = self.registry.snapshot(job['id'])
            if result['state'] not in ('queued', 'running', 'cancelling'):
                return result
            time.sleep(.01)
        self.fail('Job timed out')

    def test_desktop_client_only_and_endpoint_pinning(self):
        normalized = validate_client(CLIENT)
        self.assertEqual(set(normalized), {'client_id', 'client_secret'})
        for invalid in ({'web': CLIENT['installed']}, {}, 'not-json', {'installed': {'client_id': 'bad', 'client_secret': 'secret'}}):
            with self.assertRaises(DirectoryError):
                validate_client(invalid)

    def test_authorization_url_pkce_nonce_scopes_and_loopback(self):
        result, query = self.start()
        self.assertEqual(urlsplit(result['authorizationUrl']).netloc, 'accounts.google.com')
        callback = urlsplit(query['redirect_uri'][0])
        self.assertEqual(callback.hostname, '127.0.0.1')
        self.assertEqual(callback.path, '/')
        attempt = self.registry.oauth_attempts[result['id']]
        expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'], [expected])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertTrue(set(SCOPES).issubset(query['scope'][0].split()))
        self.assertEqual(query['access_type'], ['offline'])
        self.assertEqual(query['nonce'], [attempt.nonce])
        self.assertNotIn('untrusted.invalid', result['authorizationUrl'])

    def test_callback_persists_only_verified_tenant_and_survives_restart(self):
        result, query = self.start()
        self.assertEqual(self.callback(result, query)['state'], 'completed')
        record = self.registry.entry(self.alpha)
        self.assertEqual(record['oauth'], tokens() | {'expiry': record['oauth']['expiry']})
        self.assertEqual(record['customerId'], 'customer-alpha')
        self.assertEqual(record['status'], 'connected')
        self.assertNotIn('oauth', self.registry.entry(self.beta))
        reloaded = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        try:
            self.assertTrue(reloaded.workspaces.public(reloaded.workspaces.entry(self.alpha))['authorized'])
            self.assertEqual(reloaded.workspaces.entry(self.alpha)['oauth']['refresh_token'], 'offline-refresh-token')
        finally:
            reloaded.shutdown()

    def test_public_responses_and_snapshots_never_expose_tokens_secret_or_callback_state(self):
        result, query = self.start()
        self.callback(result, query)
        public = json.dumps([self.registry.list(), self.registry.users(self.alpha), self.registry.oauth_status(result['id'])])
        for secret in ('offline-client-secret', 'offline-refresh-token', 'offline-access-token', query['state'][0], query['nonce'][0]):
            self.assertNotIn(secret, public)

    def test_last_authorization_is_public_and_persisted_without_callback_secrets(self):
        result, query = self.start()
        self.registry.oauth_exchange = Mock(side_effect=DirectoryError('unused'))
        self.registry.oauth_cancel(result['id'])
        latest = self.registry.public(self.registry.entry(self.alpha))['authorization']
        self.assertEqual(latest['state'], 'cancelled')
        disk = json.loads(self.registry.path.read_text(encoding='utf-8'))
        saved = disk[self.alpha]['lastAuthorization']
        self.assertEqual(saved['state'], 'cancelled')
        self.assertNotIn(query['state'][0], json.dumps(saved))

    def test_state_mismatch_host_path_and_duplicate_parameters_do_not_consume_attempt(self):
        result, query = self.start()
        base = query['redirect_uri'][0]
        for suffix, headers in (('?state=wrong&code=test', {}),
                                ('?' + urlencode({'state': query['state'][0], 'code': 'x'}) + '&state=duplicate', {}),
                                ('?state=x&code=x', {'Host': 'attacker.invalid'})):
            with self.assertRaises(HTTPError) as error:
                self.http.open(Request(base + suffix, headers=headers), timeout=3)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.registry.oauth_status(result['id'])['state'], 'waiting')
        self.registry.oauth_exchange.assert_not_called()
        self.assertEqual(self.callback(result, query)['state'], 'completed')

    def test_denied_cancelled_and_expired_leave_prior_credentials_untouched(self):
        previous = tokens()
        self.registry.records[self.alpha]['oauth'] = copy.deepcopy(previous)
        result, query = self.start()
        status = self.callback(result, query, error='access_denied')
        self.assertEqual(status['state'], 'cancelled')
        self.assertEqual(self.registry.entry(self.alpha)['oauth'], previous)
        result, query = self.start()
        self.registry.oauth_cancel(result['id'])
        self.assertEqual(self.registry.oauth_status(result['id'])['state'], 'cancelled')
        result, query = self.start()
        self.registry.oauth_attempts[result['id']].expires = time.monotonic() - 1
        for _ in range(60):
            if self.registry.oauth_status(result['id'])['state'] == 'expired':
                break
            time.sleep(.01)
        self.assertEqual(self.registry.oauth_status(result['id'])['state'], 'expired')
        self.assertEqual(self.registry.entry(self.alpha)['oauth'], previous)

    def test_busy_guards_and_cancellation_during_exchange_never_commit(self):
        gate, entered = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set(); gate.wait(4); return tokens()
        self.registry.oauth_exchange = delayed
        result, query = self.start()
        errors = []
        def callback():
            try:
                self.callback(result, query)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=callback); thread.start()
        self.assertTrue(entered.wait(2))
        for action in (lambda: self.registry.oauth_start(self.alpha),
                       lambda: self.registry.upsert({'id': self.alpha, 'name': 'Changed', 'adminEmail': 'admin@alpha.invalid'}),
                       lambda: self.registry.start(self.alpha, 'check')):
            with self.assertRaises(ApiError):
                action()
        self.registry.oauth_cancel(result['id']); gate.set(); thread.join(4)
        self.assertEqual(errors, [])
        self.assertNotIn('oauth', self.registry.entry(self.alpha))

    def test_multiple_enterprises_are_independent(self):
        alpha, aq = self.start()
        beta, bq = self.start(self.beta)
        self.assertNotEqual(aq['state'], bq['state'])
        self.assertEqual(self.callback(beta, bq)['state'], 'completed')
        self.assertEqual(self.callback(alpha, aq)['state'], 'completed')
        self.assertEqual(self.registry.entry(self.alpha)['customerId'], 'customer-alpha')
        self.assertEqual(self.registry.entry(self.beta)['customerId'], 'customer-beta')

    def test_duplicate_customer_and_existing_customer_pin(self):
        result, query = self.start(); self.callback(result, query)
        duplicate = self.add('Duplicate', 'other@alpha.invalid')['id']
        result, query = self.start(duplicate)
        self.assertEqual(self.callback(result, query)['state'], 'failed')
        self.assertNotIn('oauth', self.registry.entry(duplicate))
        self.fixture.customer_override = 'customer-wrong'
        result, query = self.start()
        self.assertEqual(self.callback(result, query)['state'], 'failed')
        self.assertEqual(self.registry.entry(self.alpha)['customerId'], 'customer-alpha')

    def test_admin_or_client_change_drops_grant_but_name_proxy_edit_preserves_it(self):
        result, query = self.start(); self.callback(result, query)
        saved = copy.deepcopy(self.registry.entry(self.alpha)['oauth'])
        self.registry.upsert({'id': self.alpha, 'name': 'Renamed', 'adminEmail': 'admin@alpha.invalid'})
        self.assertEqual(self.registry.entry(self.alpha)['oauth'], saved)
        self.registry.upsert({'id': self.alpha, 'name': 'Renamed', 'adminEmail': 'other@alpha.invalid'})
        self.assertNotIn('oauth', self.registry.entry(self.alpha))

    def test_successful_authorization_reuses_employee_sync_and_create(self):
        result, query = self.start(); self.callback(result, query)
        job = self.finish_job(self.registry.start(self.alpha, 'sync'))
        self.assertEqual(job['state'], 'completed')
        preview = self.registry.preview(self.alpha, {'operation': 'create', 'count': 1, 'domain': 'alpha.invalid', 'password': 'offline-password'})
        job = self.finish_job(self.registry.start(self.alpha, 'execute', {'draftId': preview['draftId']}))
        self.assertTrue(job['results'][0]['success'])

    def test_proxy_is_snapshotted_and_protected_during_authorization(self):
        proxy = self.worker.catalog.upsert({'name': 'Offline route', 'proxy': 'http://127.0.0.1:12345'})
        self.registry.upsert({'id': self.alpha, 'name': 'Alpha', 'adminEmail': 'admin@alpha.invalid', 'proxyId': proxy['id']})
        result, query = self.start()
        with self.assertRaises(ApiError):
            self.worker.catalog.upsert(dict(proxy, name='Changed'))
        self.callback(result, query)
        self.assertEqual(self.registry.oauth_exchange.call_args.args[4]['proxy'], proxy['proxy'])
        self.assertEqual(self.fixture.routes[-1]['proxy'], proxy['proxy'])

    def test_credentials_refresh_persists_and_uses_fixed_google_endpoint(self):
        record = copy.deepcopy(self.registry.entry(self.alpha)); record['oauth'] = tokens()
        captured = []
        credentials = credentials_for(record, captured.append)
        def refresh(instance, request):
            self.assertEqual(instance._token_uri, TOKEN_URI)
            instance.token = 'refreshed-access'; instance.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        with patch('google.oauth2.credentials.Credentials.refresh', refresh):
            credentials.refresh(Mock())
        self.assertEqual(captured[0]['token'], 'refreshed-access')
        self.assertEqual(captured[0]['refresh_token'], 'offline-refresh-token')
        with patch('google.oauth2.credentials.Credentials.refresh', side_effect=Exception('SECRET')):
            with self.assertRaises(DirectoryError) as error:
                credentials.refresh(Mock())
        self.assertNotIn('SECRET', str(error.exception))

    def test_directory_transport_refresh_writes_tokens_to_disk(self):
        result, query = self.start(); self.callback(result, query)
        self.registry.client_factory = connect
        record = copy.deepcopy(self.registry.entry(self.alpha))
        def refresh(instance, request):
            instance.token = 'persisted-refreshed-access'
            instance.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        with patch('google.oauth2.credentials.Credentials.refresh', refresh):
            with self.registry._client(record, {}) as client:
                self.assertFalse(client.auth_session.trust_env)
                self.assertFalse(client.session.trust_env)
                client.session.credentials.refresh(Mock())
        disk = json.loads(self.registry.path.read_text(encoding='utf-8'))
        self.assertEqual(disk[self.alpha]['oauth']['token'], 'persisted-refreshed-access')
        self.assertEqual(disk[self.alpha]['oauth']['refresh_token'], 'offline-refresh-token')

    def test_initial_identity_failure_or_disk_failure_does_not_save_new_grant(self):
        self.fixture.customer_override = ''
        result, query = self.start()
        with patch.object(self.registry, '_identity', side_effect=DirectoryError('Offline identity rejected')):
            self.assertEqual(self.callback(result, query)['state'], 'failed')
        self.assertNotIn('oauth', self.registry.entry(self.alpha))
        result, query = self.start()
        with patch.object(self.registry, '_save', side_effect=OSError('Offline disk failure')):
            self.assertEqual(self.callback(result, query)['state'], 'failed')
        self.assertNotIn('oauth', self.registry.entry(self.alpha))

    def test_authenticated_http_routes_and_no_browser_origin_access(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.worker, 'offline-bearer'))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}/'
        def call(path, body=None, headers=None):
            return self.http.open(Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                headers=headers or {'Authorization': 'Bearer offline-bearer'}), timeout=4)
        try:
            with call(f'workspaces/{self.alpha}/oauth-start', {}) as response:
                attempt = json.load(response)
            with call('workspace-oauth/' + attempt['id']) as response:
                self.assertEqual(json.load(response)['state'], 'waiting')
            for headers in ({'Authorization': 'wrong'}, {'Authorization': 'Bearer offline-bearer', 'Origin': 'https://attacker.invalid'}):
                with self.assertRaises(HTTPError):
                    call('workspace-oauth/' + attempt['id'], headers=headers)
            with call('workspace-oauth/' + attempt['id'] + '/cancel', {}) as response:
                self.assertEqual(json.load(response)['state'], 'cancelled')
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


class ExchangeTests(unittest.TestCase):
    def test_network_errors_explain_explicit_direct_or_selected_proxy_without_secrets(self):
        import requests
        session = Mock()
        context = Mock(); context.__enter__ = Mock(return_value=session); context.__exit__ = Mock(return_value=False)
        session.post.side_effect = requests.exceptions.ConnectTimeout('SECRET-url-or-token')
        args = (validate_client(CLIENT), 'code', 'verifier', 'http://127.0.0.1:1234/', {}, 'nonce', 'admin@alpha.invalid')
        with patch('workspace_oauth.google_session', return_value=context):
            with self.assertRaises(DirectoryError) as direct:
                exchange(*args)
            self.assertIn('\u672c\u673a\u76f4\u8fde', str(direct.exception))
            self.assertIn('\u8d85\u65f6', str(direct.exception))
            self.assertNotIn('SECRET', str(direct.exception))
            proxied_args = list(args); proxied_args[4] = {'proxy': 'http://127.0.0.1:7897'}
            with self.assertRaises(DirectoryError) as proxied:
                exchange(*proxied_args)
            self.assertIn('\u4e0a\u6e38\u4ee3\u7406', str(proxied.exception))
            self.assertNotIn('\u672c\u673a\u76f4\u8fde', str(proxied.exception))

    def test_google_token_errors_are_classified_without_echoing_provider_details(self):
        session = Mock(); response = Mock(status_code=400); session.post.return_value = response
        response.json.return_value = {'error': 'invalid_grant', 'error_description': 'SECRET-code'}
        context = Mock(); context.__enter__ = Mock(return_value=session); context.__exit__ = Mock(return_value=False)
        with patch('workspace_oauth.google_session', return_value=context):
            with self.assertRaises(DirectoryError) as error:
                exchange(validate_client(CLIENT), 'code', 'verifier', 'http://127.0.0.1:1234/', {}, 'nonce', 'admin@alpha.invalid')
        self.assertIn('HTTP 400', str(error.exception))
        self.assertIn('\u6388\u6743\u7801', str(error.exception))
        self.assertNotIn('SECRET', str(error.exception))

    def test_exchange_checks_scopes_signed_identity_nonce_and_admin_email(self):
        session = Mock()
        response = Mock(status_code=200)
        data = dict(access_token='access', refresh_token='refresh', id_token='signed-token', scope=' '.join(SCOPES), expires_in=3600)
        response.json.return_value = data; session.post.return_value = response
        context = Mock(); context.__enter__ = Mock(return_value=session); context.__exit__ = Mock(return_value=False)
        identity = dict(nonce='expected-nonce', email_verified=True, email='admin@alpha.invalid')
        with patch('workspace_oauth.google_session', return_value=context), patch('google.oauth2.id_token.verify_oauth2_token', return_value=identity) as verify:
            args = (validate_client(CLIENT), 'code', 'verifier', 'http://127.0.0.1:1234/oauth/callback', {}, 'expected-nonce', 'admin@alpha.invalid')
            result = exchange(*args)
            self.assertEqual(result['refresh_token'], 'refresh')
            self.assertEqual(session.post.call_args.args[0], TOKEN_URI)
            self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
            self.assertEqual(session.post.call_args.kwargs['data']['code_verifier'], 'verifier')
            for bad in (dict(identity, email='wrong@beta.invalid'), dict(identity, nonce='wrong'), dict(identity, email_verified=False)):
                verify.return_value = bad
                with self.assertRaises(DirectoryError):
                    exchange(*args)
            verify.return_value = identity
            for bad in (dict(data, scope=SCOPES[0]), dict(data, refresh_token='')):
                response.json.return_value = bad
                with self.assertRaises(DirectoryError):
                    exchange(*args)
            response.status_code = 400
            with self.assertRaises(DirectoryError):
                exchange(*args)


if __name__ == '__main__':
    unittest.main()

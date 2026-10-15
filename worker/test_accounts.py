"""Account metadata isolation and honest, read-only login observations."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from account_status import classify_observation
from server import BrowserWorker, ApiError, validate_profile


class AccountTests(unittest.TestCase):
    def test_login_evidence_is_conservative_and_domain_scoped(self):
        cases = [
            ({'url': 'https://www.dola.com/chat/', 'ready': 'complete'}, 'unknown'),
            ({'url': 'https://www.dola.com/chat/', 'ready': 'complete', 'logout': True}, 'logged_in'),
            ({'url': 'https://www.dola.com/chat/', 'ready': 'loading', 'logout': True}, 'unknown'),
            ({'url': 'https://dola.com/signin'}, 'login_required'),
            ({'url': 'https://dola.com/chat/', 'login': True, 'logout': True}, 'login_required'),
            ({'url': 'https://dola.com/chat/', 'restricted': True, 'login': True}, 'region_blocked'),
            ({'url': 'https://dola.com.evil.invalid/chat/', 'ready': 'complete', 'logout': True}, 'unknown'),
            ({'url': 'http://127.0.0.1/probe', 'login': True}, 'unknown'),
        ]
        for observation, expected in cases:
            with self.subTest(observation=observation):
                self.assertEqual(classify_observation(observation)[0], expected)

    def test_account_metadata_survives_environment_edit_and_restart(self):
        with TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), Path('missing.exe'))
            worker.create({'id': 'one', 'name': '账号一', 'proxy': 'http://proxy.invalid:80', 'proxyUsername': 'fixture-user', 'proxyPassword': 'fixture-secret'})
            original = dict(worker.profile('one'))
            state = worker.update_account('one', {'name': '工作账号', 'accountEmail': 'test@example.invalid', 'accountGroup': '主账号', 'accountNotes': '测试备注\n第二行', 'proxy': 'http://ignored.invalid:80'})
            self.assertEqual(state['name'], '工作账号')
            self.assertNotIn('proxyPassword', state)
            self.assertEqual(worker.profile('one')['proxyPassword'], original['proxyPassword'])
            self.assertEqual(worker.profile('one')['proxy'], original['proxy'])
            worker.create({'id': 'one', 'locale': 'ja-JP', 'timezone': 'Asia/Tokyo', 'keepProxyPassword': True})
            restored = BrowserWorker(Path(directory), Path('missing.exe')).profile('one')
            self.assertEqual(restored['accountEmail'], 'test@example.invalid')
            self.assertEqual(restored['accountNotes'], '测试备注\n第二行')
            self.assertEqual(restored['proxyPassword'], 'fixture-secret')

    def test_running_account_uses_only_read_only_page_observation(self):
        with TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), Path('missing.exe'))
            worker.create({'id': 'one'}); worker.create({'id': 'two'})
            runtime = {'process': SimpleNamespace(poll=lambda: None)}
            worker.runtime['one'] = runtime
            worker.command = Mock(return_value={'result': {'value': {'url': 'https://dola.com/chat/', 'ready': 'complete', 'logout': True}}})
            result = worker.check_account('one')
            self.assertEqual(result['status'], 'logged_in')
            self.assertIn('checkedAt', result)
            args = worker.command.call_args.args
            self.assertEqual(args[1], 'Runtime.evaluate')
            self.assertNotIn('fetch(', args[2]['expression'])
            self.assertNotIn('click(', args[2]['expression'])
            self.assertNotIn('two', worker.account_checks)
            self.assertEqual(worker.check_account('two')['reason'], 'browser_closed')
            self.assertEqual(worker.command.call_count, 1)

    def test_server_login_requires_current_account_control_and_explicit_boolean(self):
        base = {'url': 'https://www.dola.com/chat/123', 'ready': 'complete',
                'serverLogin': True, 'accountIdentity': True, 'accountMenu': True}
        self.assertEqual(classify_observation(base), ('logged_in', 'page_session_confirmed'))
        for change in ({'serverLogin': 'true'}, {'serverLogin': 1}, {'accountIdentity': False},
                       {'accountMenu': False}, {'ready': 'loading'}, {'url': 'https://dola.com.evil.invalid/chat'}):
            with self.subTest(change=change):
                self.assertEqual(classify_observation(dict(base, **change))[0], 'unknown')
        self.assertEqual(classify_observation(dict(base, login=True))[0], 'login_required')
        self.assertEqual(classify_observation(dict(base, restricted=True))[0], 'region_blocked')
        self.assertEqual(classify_observation(dict(base, url='https://www.dola.com/security/region-restricted'))[0], 'region_blocked')
        self.assertEqual(classify_observation(dict(base, serverLogin=False, accountMenu=False)), ('login_required', 'page_session_signed_out'))
        self.assertEqual(classify_observation(dict(base, serverLogin=False))[0], 'unknown')

    def test_account_update_failure_rolls_back(self):
        with TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), Path('missing.exe')); worker.create({'id': 'one'})
            original = dict(worker.profile('one'))
            worker.save = Mock(side_effect=OSError('fixture'))
            with self.assertRaises(OSError): worker.update_account('one', {'name': 'changed'})
            self.assertEqual(worker.profile('one'), original)

    def test_invalid_account_metadata_rejected(self):
        for payload in ({'accountNotes': 'x' * 1001}, {'accountGroup': []}, {'accountEmail': 'bad\x00value'}):
            with self.subTest(payload=payload), self.assertRaises(ApiError): validate_profile(payload)


if __name__ == '__main__':
    unittest.main()

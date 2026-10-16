"""Isolated login state-machine and credential tests; no third-party requests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google_credentials import GoogleCredentialStore
from google_login import trusted_google, trusted_dola, google_submit_expression
from server import BrowserWorker, ApiError, make_handler
from http.server import ThreadingHTTPServer
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError


EMAIL, PASSWORD = 'fixture@example.invalid', 'isolated-secret-VALUE-EMAIL-"-\\-测试'


class GoogleLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name), Path('missing.exe'))
        self.worker.create({'id': 'one', 'name': 'Test', 'startUrl': 'https://www.dola.com/'})
        self.worker.create({'id': 'two'})
        self.runtime = {'process': SimpleNamespace(poll=lambda: None), 'cdpPort': 9222, 'targetId': 'dola'}
        self.worker.runtime['one'] = self.runtime
        self.manager = self.worker.google_login
        self.google = {'id': 'google', 'url': 'https://accounts.google.com/v3/signin/identifier'}
        self.dola = {'id': 'dola', 'url': 'https://www.dola.com/chat/'}
        self.worker.target = lambda runtime: self.dola

    def tearDown(self):
        self.manager.close()
        self.worker.runtime.clear()
        self.temp.cleanup()

    def run_flow(self, stages, *, existing_google=False, resume=None, cancel_after=None):
        from google_login import GOOGLE_OBSERVE, DOLA_OBSERVE, OBSERVE_PAGE
        job = {'id': 'fixture-job', 'state': 'running', 'stage': 'opening_dola', 'startedAt': 0,
               'updatedAt': 0, 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        self.calls = []
        counter = {'index': 0, 'clicked': existing_google}
        self.manager._pages = lambda _: [self.dola] + ([self.google] if counter['clicked'] else [])

        def evaluate(runtime, page, expression):
            self.calls.append((page['id'], expression))
            if expression == OBSERVE_PAGE:
                if counter['index'] and stages[min(counter['index'] - 1, len(stages) - 1)].get('complete'):
                    return {'url': self.dola['url'], 'ready': 'complete', 'logout': True}
                return {'url': self.dola['url'], 'ready': 'complete', 'login': True}
            if expression == DOLA_OBSERVE:
                return {'trusted': True, 'manual': False}
            if expression == GOOGLE_OBSERVE:
                index = counter['index']; counter['index'] += 1
                if cancel_after is not None and counter['index'] >= cancel_after:
                    job['cancel'].set()
                return dict(trusted=True, **stages[min(index, len(stages) - 1)])
            if 'input.dispatchEvent' in expression:
                return {'submitted': True}
            counter['clicked'] = True
            return True

        self.manager._evaluate = evaluate
        # Keep production state transitions intact; avoid one-second test waits.
        job['cancel'].wait = lambda *_: False
        self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD, resume)
        return job

    def test_official_origins_reject_lookalikes_http_and_nonstandard_ports(self):
        self.assertTrue(trusted_google('https://accounts.google.com/signin'))
        self.assertTrue(trusted_dola('https://www.dola.com/chat'))
        for url in ('http://accounts.google.com', 'https://accounts.google.com.evil.invalid',
                    'https://accounts-google.com', 'https://accounts.google.com:444',
                    'https://google.com', 'https://accounts.google.com@evil.invalid', 'https://accounts.google.com:invalid'):
            self.assertFalse(trusted_google(url))
        for url in ('https://dola.com.evil.invalid', 'http://www.dola.com', 'https://dola.com:444', 'https://dola.com:invalid'):
            self.assertFalse(trusted_dola(url))

    def test_unrelated_dola_tab_cannot_confirm_current_login(self):
        from google_login import OBSERVE_PAGE
        unrelated = {'id': 'unrelated', 'url': 'https://www.dola.com/chat/'}
        self.manager._pages = lambda _: [self.dola, unrelated]
        job = {'id': 'fixture', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        def evaluate(rt, page, expression):
            self.assertNotEqual(page['id'], 'unrelated')
            if expression == OBSERVE_PAGE:
                return {'url': self.dola['url'], 'ready': 'complete', 'login': True}
            job['cancel'].set()
            return {'trusted': True, 'manual': True}
        self.manager._evaluate = evaluate
        self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        self.assertNotEqual(job['state'], 'completed')
        self.assertNotIn('one', self.worker.account_checks)

    def test_success_requires_dola_evidence_and_persists_only_status(self):
        job = self.run_flow([{'email': True}, {'password': True}, {'complete': True}])
        self.assertEqual((job['state'], job['stage']), ('completed', 'logged_in'))
        self.assertEqual(self.worker.account_checks['one']['status'], 'logged_in')
        self.assertNotIn('two', self.worker.account_checks)
        snapshot = json.dumps(self.manager.snapshot('one'))
        self.assertNotIn(PASSWORD, snapshot); self.assertNotIn(EMAIL, snapshot)
        files = ''.join(p.read_text(encoding='utf-8') for p in Path(self.temp.name).glob('*.json'))
        self.assertNotIn(PASSWORD, files); self.assertNotIn(EMAIL, files)

    def test_each_credential_is_submitted_once_despite_visible_transition_fields(self):
        self.run_flow([{'email': True}, {'email': True}, {'password': True}, {'password': True}, {'complete': True}])
        submits = [expression for _, expression in self.calls if 'input.dispatchEvent' in expression]
        self.assertEqual(len(submits), 2)
        self.assertEqual(sum(json.dumps(PASSWORD) in e for e in submits), 1)
        self.assertTrue(all(page == 'google' for page, expression in self.calls if 'input.dispatchEvent' in expression))

    def test_navigation_loses_password_reply_without_replaying_submission(self):
        from google_login import NavigationInterrupted
        original_run = self.manager._run
        attempts = []
        def run(*args):
            evaluate = self.manager._evaluate
            def interrupted(rt, page, expression):
                if 'input.dispatchEvent' in expression and json.dumps(PASSWORD) in expression:
                    attempts.append(expression)
                    raise NavigationInterrupted('Execution context destroyed during successful submit')
                return evaluate(rt, page, expression)
            self.manager._evaluate = interrupted
            return original_run(*args)
        self.manager._run = run
        job = self.run_flow([{'email': True}, {'password': True}, {'password': True}, {'complete': True}])
        self.assertEqual((job['state'], job['stage']), ('completed', 'logged_in'))
        self.assertEqual(len(attempts), 1)

    def test_navigation_observation_recovers_without_false_manual_gate(self):
        from google_login import DOLA_OBSERVE, GOOGLE_OBSERVE, NavigationInterrupted
        original_run = self.manager._run
        interrupted = set()
        def run(*args):
            evaluate = self.manager._evaluate
            def navigating(rt, page, expression):
                if expression in (DOLA_OBSERVE, GOOGLE_OBSERVE) and expression not in interrupted:
                    interrupted.add(expression)
                    raise NavigationInterrupted('Target navigated')
                return evaluate(rt, page, expression)
            self.manager._evaluate = navigating
            return original_run(*args)
        self.manager._run = run
        job = self.run_flow([{'email': True}, {'password': True}, {'complete': True}])
        self.assertEqual(job['stage'], 'logged_in')
        self.assertEqual(len(interrupted), 2)

    def test_origin_changed_after_target_snapshot_waits_for_fresh_observation(self):
        from google_login import GOOGLE_OBSERVE
        self.manager._evaluate = lambda *_: {'trusted': False}
        self.assertIsNone(self.manager._observe(self.runtime, self.google, GOOGLE_OBSERVE))

    def test_loading_entry_is_reported_until_provider_control_appears(self):
        from google_login import dola_click_expression
        original_run = self.manager._run
        updates, attempts = [], []
        update = self.manager._update
        self.manager._update = lambda pid, job, state, stage: (updates.append(stage), update(pid, job, state, stage))[-1]
        def run(*args):
            evaluate = self.manager._evaluate
            def delayed(rt, page, expression):
                if expression in (dola_click_expression('google'), dola_click_expression('login')):
                    attempts.append(expression)
                    if len(attempts) <= 4:
                        return False
                return evaluate(rt, page, expression)
            self.manager._evaluate = delayed
            return original_run(*args)
        self.manager._run = run
        job = self.run_flow([{'email': True}, {'complete': True}])
        self.assertEqual(job['stage'], 'logged_in')
        self.assertIn('loading_dola', updates)
        self.assertNotIn('dola_confirmation', updates)

    def test_disappearing_callback_cannot_mask_main_page_confirmation(self):
        from google_login import DOLA_OBSERVE, OBSERVE_PAGE, NavigationInterrupted
        callback = dict(self.google, url='https://www.dola.com/auth/callback')
        self.manager._evaluate = lambda rt, page, expression: (_ for _ in ()).throw(NavigationInterrupted()) if page['id'] == callback['id'] else ({'trusted': True} if expression == DOLA_OBSERVE else {'url': self.dola['url'], 'ready': 'complete', 'logout': True})
        self.assertFalse(self.manager._confirm('one', self.runtime, 0, [self.dola, callback]))
        self.assertTrue(self.manager._confirm('one', self.runtime, 0, [self.dola]))

    def test_resumed_own_oauth_can_confirm_success_but_preexisting_binding_cannot(self):
        from google_login import DOLA_OBSERVE
        self.manager._pages = lambda _: [self.dola]
        self.manager._evaluate = lambda rt, page, ex: {'trusted': True} if ex == DOLA_OBSERVE else {'url': self.dola['url'], 'ready': 'complete', 'logout': True}
        for attempted, expected in ((False, 'existing_session_unverified'), (True, 'logged_in')):
            job = {'id': 'resumed', 'state': 'running', 'stage': '', 'cancel': threading.Event(), 'loginAccountId': 'bound', 'oauthStarted': attempted}
            self.manager.jobs['one'] = job
            self.manager._run('one', job, self.runtime, 0, EMAIL, None)
            self.assertEqual(job['stage'], expected)
            self.assertEqual(self.worker.account_checks['one']['identityStatus'], 'unverified')

    def test_captcha_2fa_consent_chooser_and_rejection_pause_without_filling(self):
        for observation, stage in (({'challenge': True, 'password': True}, 'verification_required'),
                                   ({'consent': True, 'email': True}, 'consent_required'),
                                   ({'chooser': True}, 'choose_account'),
                                   ({'error': True, 'password': True}, 'google_rejected')):
            with self.subTest(stage=stage):
                job = self.run_flow([observation])
                self.assertEqual((job['state'], job['stage']), ('needs_attention', stage))
                self.assertFalse(any('input.dispatchEvent' in e for _, e in self.calls))

    def test_cancellation_before_fill_never_sends_password(self):
        job = self.run_flow([{'password': True}], cancel_after=1)
        self.assertEqual(job['state'], 'cancelled')
        self.assertFalse(any('input.dispatchEvent' in e for _, e in self.calls))

    def test_mismatched_google_identity_stops_before_submission(self):
        # Patch around run_flow's evaluator setup through a wrapper.
        original_run = self.manager._run
        def run(*args):
            underlying = self.manager._evaluate
            self.manager._evaluate = lambda rt, pg, ex: {'submitted': False, 'reason': 'account_mismatch'} if 'input.dispatchEvent' in ex else underlying(rt, pg, ex)
            return original_run(*args)
        self.manager._run = run
        job = self.run_flow([{'password': True}])
        self.assertEqual(job['stage'], 'account_mismatch')
        self.assertNotIn('one', self.worker.account_checks)

    def test_existing_unrelated_google_popup_is_not_selected(self):
        # Existing popup is excluded unless explicitly bound by a previous job.
        from google_login import GOOGLE_OBSERVE
        self.manager._pages = lambda _: [self.dola, self.google]
        job = {'id': 'fixture', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        seen = []
        def evaluate(rt, page, expression):
            seen.append((page, expression))
            if expression == GOOGLE_OBSERVE:
                self.fail('Unrelated Google page observed')
            if expression.startswith('(()'):
                job['cancel'].set()
            return {'url': self.dola['url'], 'ready': 'complete'}
        self.manager._evaluate = evaluate
        self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        self.assertFalse(any(PASSWORD in expression for _, expression in seen))

    def entry_flow(self, *, opens_after=2, pending=False):
        from google_login import DOLA_OBSERVE, OBSERVE_PAGE, dola_click_expression
        job = {'id': 'entry-fixture', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        clock = [0.0]
        clicks = [0]
        self.manager._pages = lambda _: [self.dola] + ([dict(id='pending', url='about:blank')] if pending and clicks[0] else [])
        def evaluate(rt, page, expression):
            if expression == DOLA_OBSERVE:
                return {'trusted': True, 'manual': False}
            if expression == OBSERVE_PAGE:
                return {'url': self.dola['url'], 'ready': 'complete', 'logout': clicks[0] >= opens_after, 'login': clicks[0] < opens_after}
            if expression == dola_click_expression('google'):
                clicks[0] += 1
                return True
            return False
        self.manager._evaluate = evaluate
        job['cancel'].wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds) or False
        with patch('google_login.time.monotonic', side_effect=lambda: clock[0]):
            self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        return job, clicks[0], clock[0]

    def test_provider_click_without_reaction_is_retried_after_grace(self):
        job, clicks, elapsed = self.entry_flow()
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(clicks, 2)
        self.assertGreaterEqual(elapsed, 4)

    def test_unresponsive_provider_stops_after_three_entry_attempts(self):
        job, clicks, elapsed = self.entry_flow(opens_after=99)
        self.assertEqual(clicks, 3)
        self.assertEqual(job['stage'], 'google_entry_unresponsive')
        self.assertGreaterEqual(elapsed, 120)

    def test_manual_provider_click_after_retry_limit_continues_credentials(self):
        from google_login import DOLA_OBSERVE, GOOGLE_OBSERVE, OBSERVE_PAGE, dola_click_expression
        job = {'id': 'manual-entry', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        clock, clicks, submits = [0.0], [], []
        self.manager._pages = lambda _: [self.dola] + ([self.google] if clock[0] >= 16 else [])
        def evaluate(rt, page, expression):
            if expression == DOLA_OBSERVE:
                return {'trusted': True, 'manual': False}
            if expression == OBSERVE_PAGE:
                return {'url': self.dola['url'], 'ready': 'complete', 'logout': len(submits) == 2, 'login': len(submits) < 2}
            if expression == GOOGLE_OBSERVE:
                return {'trusted': True, 'email': not submits, 'password': len(submits) == 1}
            if expression == dola_click_expression('google'):
                clicks.append(clock[0]); return True
            if 'input.dispatchEvent' in expression:
                submits.append(expression); return {'submitted': True}
            return False
        self.manager._evaluate = evaluate
        job['cancel'].wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds) or False
        with patch('google_login.time.monotonic', side_effect=lambda: clock[0]):
            self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        self.assertEqual((job['state'], job['stage']), ('completed', 'logged_in'))
        self.assertEqual(len(clicks), 3)
        self.assertEqual(len(submits), 2)
        self.assertEqual(sum(json.dumps(PASSWORD) in expression for expression in submits), 1)

    def test_unrelated_popup_opened_during_loading_is_excluded(self):
        from google_login import DOLA_OBSERVE, GOOGLE_OBSERVE, OBSERVE_PAGE, dola_click_expression
        job = {'id': 'delayed-entry', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        clock, clicks = [0.0], []
        self.manager._pages = lambda _: [self.dola] + ([self.google] if clock[0] >= 1 else [])
        def evaluate(rt, page, expression):
            if expression == GOOGLE_OBSERVE:
                self.fail('Unrelated popup opened before provider click must not receive credentials')
            if expression == OBSERVE_PAGE:
                return {'url': self.dola['url'], 'ready': 'complete', 'login': True}
            if expression == DOLA_OBSERVE:
                return {'trusted': True, 'manual': False}
            if expression == dola_click_expression('google') and clock[0] >= 2:
                clicks.append(clock[0]); return True
            return False
        self.manager._evaluate = evaluate
        job['cancel'].wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds) or False
        with patch('google_login.time.monotonic', side_effect=lambda: clock[0]):
            self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        self.assertEqual(len(clicks), 3)
        self.assertEqual(job['stage'], 'google_entry_unresponsive')

    def test_pending_popup_never_causes_duplicate_provider_windows(self):
        job, clicks, _ = self.entry_flow(opens_after=99, pending=True)
        self.assertEqual(clicks, 1)
        self.assertEqual(job['stage'], 'timeout')

    def test_paused_google_popup_can_resume_after_user_selects_account(self):
        job = self.run_flow([{'password': True}, {'complete': True}], existing_google=True, resume='google')
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(job['targetId'], 'google')

    def test_unrelated_page_is_preserved_without_navigation(self):
        self.dola['url'] = 'http://127.0.0.1/probe'
        job = self.run_flow([{'email': True}])
        self.assertEqual(job['stage'], 'open_dola')
        self.assertFalse(any('input.dispatchEvent' in e or 'Page.navigate' in e for _, e in self.calls))

    def test_errors_never_echo_secret_script_or_exception(self):
        self.manager._pages = lambda _: (_ for _ in ()).throw(RuntimeError(PASSWORD))
        job = {'id': 'fixture', 'state': 'running', 'stage': '', 'cancel': threading.Event()}
        self.manager.jobs['one'] = job
        self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD)
        self.assertEqual(job['stage'], 'connection_failed')
        self.assertNotIn(PASSWORD, json.dumps(job, default=str))

    def test_validation_duplicate_and_closed_browser(self):
        with patch('google_login.threading.Thread'):
            for payload in ({'email': 'bad', 'password': PASSWORD}, {'email': EMAIL, 'password': ''},
                            {'email': EMAIL, 'password': PASSWORD, 'remember': 'true'},
                            {'email': EMAIL, 'password': PASSWORD, 'totp': 'not-supported'}):
                with self.assertRaises(ApiError): self.manager.start('one', payload)
            self.manager.start('one', {'email': EMAIL, 'password': PASSWORD})
            with self.assertRaises(ApiError): self.manager.start('one', {'email': EMAIL, 'password': PASSWORD})
            with self.assertRaises(ApiError): self.manager.start('two', {'email': EMAIL, 'password': PASSWORD})
        self.assertFalse(self.manager.vault.path.exists())

    def test_plaintext_restart_roundtrip_per_environment_and_forget(self):
        vault = self.manager.vault
        vault.save('one', EMAIL, PASSWORD)
        raw = vault.path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(raw)['one'], {'profileId': 'one', 'email': EMAIL, 'password': PASSWORD})
        restored = GoogleCredentialStore(Path(self.temp.name))
        self.assertEqual(restored.load('one')['password'], PASSWORD)
        self.assertIsNone(restored.load('two'))
        records = json.loads(raw); records['two'] = records['one']; vault.path.write_text(json.dumps(records), encoding='utf-8')
        with self.assertRaises(OSError): restored.load('two')
        vault.forget('one'); self.assertIsNone(vault.load('one'))

    def test_saved_password_requires_exact_environment_and_email(self):
        self.manager.vault.save('one', EMAIL, PASSWORD)
        with patch('google_login.threading.Thread') as thread:
            for pid, email in (('one', 'other@example.invalid'), ('two', EMAIL)):
                if pid == 'two': self.worker.runtime['two'] = self.runtime
                with self.assertRaises(ApiError): self.manager.start(pid, {'email': email, 'useSaved': True})
            job = self.manager.start('one', {'email': EMAIL.upper(), 'useSaved': True})
            self.assertEqual(job['state'], 'running')
            self.assertEqual(thread.call_args.kwargs['args'][5], PASSWORD)
            self.assertNotIn(PASSWORD, json.dumps(job))

    def test_script_literal_preserves_special_characters(self):
        expression = google_submit_expression('password', PASSWORD, EMAIL)
        self.assertIn(json.dumps(PASSWORD), expression)
        self.assertIn("identity!==", expression)
        self.assertIn("location.origin!=='https://accounts.google.com'", expression)

    def onboarding_flow(self, *, allow=True, adult=False, rejected=None):
        from google_login import GOOGLE_OBSERVE, DOLA_OBSERVE, OBSERVE_PAGE
        job = {'id': 'onboarding', 'state': 'running', 'stage': '', 'cancel': threading.Event(),
               'options': {'allowOnboarding': allow, 'ageConfirmed': adult}}
        self.manager.jobs['one'] = job
        step = {'index': 0, 'clicks': []}
        def pages(_):
            if step['index'] >= 2:
                return [self.dola, dict(self.google, url='https://www.dola.com/auth/callback')]
            return [self.dola, self.google]
        self.manager._pages = pages
        def evaluate(rt, page, expression):
            if expression == OBSERVE_PAGE:
                return {'url': self.dola['url'], 'ready': 'complete', 'logout': step['index'] >= 3}
            if expression == DOLA_OBSERVE:
                age = page['id'] == 'google' and step['index'] == 2
                return {'trusted': True, 'age': age, 'manual': age, 'security': False}
            if expression == GOOGLE_OBSERVE:
                return {'trusted': True, 'welcome': step['index'] == 0, 'consent': step['index'] == 1}
            if 'const kind=' in expression:
                if rejected: return {'clicked': False, 'reason': rejected}
                step['clicks'].append(('welcome', 'consent', 'age')[step['index']]); step['index'] += 1
                return {'clicked': True}
            self.fail('Unexpected onboarding browser action')
        self.manager._evaluate = evaluate
        job['cancel'].wait = lambda *_: False
        self.manager._run('one', job, self.runtime, 0, EMAIL, PASSWORD, 'google')
        return job, step

    def test_workspace_consent_and_callback_age_complete_in_bound_popup(self):
        job, step = self.onboarding_flow(adult=True)
        self.assertEqual((job['state'], job['stage']), ('completed', 'logged_in'))
        self.assertEqual(step['clicks'], ['welcome', 'consent', 'age'])

    def test_onboarding_requires_flags_and_age_cannot_report_success_behind_overlay(self):
        job, step = self.onboarding_flow(allow=False)
        self.assertEqual(job['stage'], 'workspace_welcome_required')
        self.assertEqual(step['clicks'], [])
        job, step = self.onboarding_flow(adult=False)
        self.assertEqual((job['state'], job['stage']), ('needs_attention', 'age_confirmation_required'))
        self.assertEqual(step['clicks'], ['welcome', 'consent'])
        self.assertNotIn('one', self.worker.account_checks)

    def test_unsupported_consent_or_identity_change_stops_without_fallback_click(self):
        for reason in ('consent_required', 'account_mismatch', 'page_changed'):
            job, step = self.onboarding_flow(adult=True, rejected=reason)
            self.assertEqual((job['state'], job['stage']), ('needs_attention', reason))
            self.assertEqual(step['clicks'], [])

    def test_visible_age_overlay_blocks_login_confirmation_even_with_loader_evidence(self):
        from google_login import DOLA_OBSERVE, OBSERVE_PAGE
        self.manager._evaluate = lambda rt, pg, ex: {'trusted': True, 'manual': True, 'age': True} if ex == DOLA_OBSERVE else {'url': self.dola['url'], 'ready': 'complete', 'logout': True}
        self.assertFalse(self.manager._confirm('one', self.runtime, 0, [self.dola]))
        self.assertNotIn('one', self.worker.account_checks)

    def test_callback_age_overlay_blocks_earlier_main_tab_login_evidence(self):
        from google_login import DOLA_OBSERVE
        callback = dict(self.google, url='https://www.dola.com/auth/callback')
        def evaluate(rt, page, expression):
            if expression == DOLA_OBSERVE:
                return {'trusted': True, 'manual': page['id'] == 'google'}
            return {'url': self.dola['url'], 'ready': 'complete', 'logout': True}
        self.manager._evaluate = evaluate
        self.assertFalse(self.manager._confirm('one', self.runtime, 0, [self.dola, callback]))
        self.assertNotIn('one', self.worker.account_checks)

    def test_resume_requires_same_runtime_and_never_retains_or_replays_password(self):
        with patch('google_login.threading.Thread') as thread:
            self.manager.start('one', {'email': EMAIL, 'password': PASSWORD, 'allowOnboarding': True})
            job = self.manager.jobs['one']; job.update(state='needs_attention', stage='age_confirmation_required', targetId='google')
            self.assertNotIn(PASSWORD, repr(job))
            result = self.manager.resume('one', {'allowOnboarding': True, 'ageConfirmed': True})
            args = thread.call_args.kwargs['args']
            self.assertIsNone(args[5]); self.assertEqual(args[6], 'google')
            self.assertNotIn(EMAIL, json.dumps(result)); self.assertNotIn(PASSWORD, json.dumps(result))
            with self.assertRaises(ApiError): self.manager.resume('one', {})
            self.manager.jobs['one']['state'] = 'needs_attention'
            self.worker.accounts.generations['one'] = 1
            with self.assertRaises(ApiError): self.manager.resume('one', {})

    def test_age_option_is_strict_boolean_and_resume_cannot_receive_credentials(self):
        with patch('google_login.threading.Thread'):
            for key in ('ageConfirmed', 'allowOnboarding'):
                with self.assertRaises(ApiError): self.manager.start('one', {'email': EMAIL, 'password': PASSWORD, key: 'true'})
            with self.assertRaises(ApiError): self.manager.resume('one', {'password': PASSWORD})

    def test_authenticated_api_status_and_errors_do_not_return_secrets(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.worker, 'fixture-token'))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True); thread.start()
        opener = build_opener(ProxyHandler({}))
        def request(path, payload=None, token='fixture-token', origin=None):
            headers = {'Authorization': 'Bearer ' + token}
            if origin: headers['Origin'] = origin
            data = None if payload is None else json.dumps(payload).encode('utf-8')
            return opener.open(Request(f'http://127.0.0.1:{server.server_port}/'+path, data=data, headers=headers), timeout=5)
        try:
            with self.assertRaises(HTTPError) as denied:
                request('profiles/one/google-login', token='wrong')
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            with self.assertRaises(HTTPError) as denied:
                request('profiles/one/google-login', {'email': EMAIL, 'password': PASSWORD}, origin='https://evil.invalid')
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
            with patch.object(self.manager, '_run'):
                with request('profiles/one/google-login', {'email': EMAIL, 'password': PASSWORD}) as response:
                    result = json.load(response)
                self.assertEqual(result['state'], 'running')
                self.assertNotIn(PASSWORD, json.dumps(result)); self.assertNotIn(EMAIL, json.dumps(result))
                with request('profiles/one/google-login/cancel', {}) as response:
                    self.assertEqual(json.load(response)['state'], 'cancelling')
                self.manager.jobs['one']['state'] = 'needs_attention'
                with request('profiles/one/google-login/continue', {'allowOnboarding': True, 'ageConfirmed': True}) as response:
                    result = json.load(response)
                self.assertEqual(result['state'], 'running')
                self.assertNotIn(PASSWORD, json.dumps(result)); self.assertNotIn(EMAIL, json.dumps(result))
                with self.assertRaises(HTTPError) as denied:
                    request('profiles/one/google-login/continue', {}, origin='https://www.dola.com')
                self.assertEqual(denied.exception.code, 403); denied.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()

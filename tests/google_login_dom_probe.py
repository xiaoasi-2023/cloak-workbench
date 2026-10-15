"""Run production login scripts in real Chromium with offline CDP responses.

All navigations (including official provider origins) are intercepted before
network transmission and fulfilled from local synthetic HTML. No real users,
Google authentication, proxy suppliers or external services are contacted.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile
import time
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from server import BrowserWorker
from google_login import GOOGLE_OBSERVE, DOLA_OBSERVE, google_submit_expression, dola_click_expression, onboarding_expression


EMAIL = 'dom-fixture@example.invalid'
SECRET = 'offline-VALUE-EMAIL-"-\\-中文-secret'


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True); args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    checks = []
    def check(label, condition):
        if not condition: raise RuntimeError(label)
        checks.append(label)

    with tempfile.TemporaryDirectory(prefix='cloak-google-dom-') as directory:
        binary = Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe'
        worker = BrowserWorker(Path(directory), binary)
        worker.create({'id': 'dom', 'startUrl': 'about:blank'})
        worker.launch('dom', embedded=True)
        runtime = worker.runtime['dom']; page = worker.target(runtime)
        socket = connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=3, close_timeout=1)
        serial = 0
        response_html = {'html': '<!doctype html><body>Offline fixture</body>'}
        def send(method, params=None):
            nonlocal serial
            serial += 1; request_id = serial
            socket.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
            until = time.monotonic() + 8
            while time.monotonic() < until:
                reply = json.loads(socket.recv(timeout=8))
                if reply.get('method') == 'Fetch.requestPaused':
                    # Even favicon / subresource requests remain offline.
                    fetch_id = reply['params']['requestId']; serial += 1
                    socket.send(json.dumps({'id': serial, 'method': 'Fetch.fulfillRequest', 'params': {
                        'requestId': fetch_id, 'responseCode': 200,
                        'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html; charset=utf-8'},
                                            {'name': 'Content-Security-Policy', 'value': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; form-action 'none'"}],
                        'body': base64.b64encode(response_html['html'].encode('utf-8')).decode('ascii')}}))
                elif reply.get('id') == request_id:
                    if reply.get('error'): raise RuntimeError('Offline browser fixture command failed')
                    return reply.get('result', {})
            raise TimeoutError('Offline browser fixture timed out')

        def evaluate(expression):
            result = send('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
            if result.get('exceptionDetails'): raise RuntimeError('Production login script failed to evaluate')
            return result.get('result', {}).get('value')

        def document(url, html):
            response_html['html'] = '<!doctype html><meta charset="utf-8"><body>'+html+'</body>'
            send('Page.navigate', {'url': url})
            for _ in range(50):
                if evaluate('document.readyState') == 'complete' and evaluate('location.href') == url:
                    return
                time.sleep(.02)
            raise RuntimeError('Offline document did not load')

        form = '''<input id="identifierId"><button id="identifierNext" onclick="window.submits=(window.submits||0)+1">Next</button>
        <div data-email="dom-fixture@example.invalid">Account</div><input name="Passwd" type="password"><button id="passwordNext" onclick="window.passwordSubmits=(window.passwordSubmits||0)+1">Next</button>'''
        try:
            send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})
            document('https://accounts.google.com/v3/signin/identifier', form)
            observation = evaluate(GOOGLE_OBSERVE)
            check('Official Google fixture identifies visible input fields', observation['trusted'] and observation['email'] and observation['password'] and not observation['challenge'])
            submitted = evaluate(google_submit_expression('email', EMAIL, EMAIL))
            check('Email input uses native setter and dispatches one submission', submitted['submitted'] and evaluate('window.submits') == 1 and evaluate('document.querySelector("#identifierId").value=== ' + json.dumps(EMAIL)))
            submitted = evaluate(google_submit_expression('password', SECRET, EMAIL))
            check('Password literal preserves Chinese, quotes and placeholder words', submitted['submitted'] and evaluate('document.querySelector("input[name=Passwd]").value === ' + json.dumps(SECRET)))
            check('Password submits once on matching selected account', evaluate('window.passwordSubmits') == 1)
            evaluate("document.querySelector('[data-email]').setAttribute('data-email','other@example.invalid');document.querySelector('input[name=Passwd]').value='';")
            rejected = evaluate(google_submit_expression('password', SECRET, EMAIL))
            check('Wrong account blocks password transmission in actual DOM', rejected['reason'] == 'account_mismatch' and evaluate('document.querySelector("input[name=Passwd]").value.length') == 0 and evaluate('window.passwordSubmits') == 1)
            evaluate("document.querySelector('#identifierId').style.display='none'")
            check('Hidden transitional email input is never filled', evaluate(google_submit_expression('email', EMAIL, EMAIL))['reason'] == 'not_ready' and not evaluate(GOOGLE_OBSERVE)['email'])
            document('https://accounts.google.com/v3/signin/challenge/totp', '<input name="totpPin" type="tel">Two-step verification')
            check('Real DOM OTP challenge requests manual handover', evaluate(GOOGLE_OBSERVE)['challenge'])
            document('https://accounts.google.com/v3/signin/identifier', form + '<input autocomplete="one-time-code">')
            check('Challenge appearing between observation and submit blocks credential filling', evaluate(google_submit_expression('password', SECRET, EMAIL))['reason'] == 'verification_required' and evaluate('window.passwordSubmits') is None)
            document('https://accounts.google.com/signin/oauth/consent', '<button id="submit_button">Continue</button>Wants access to account')
            check('Real DOM authorization is detected without clicking consent', evaluate(GOOGLE_OBSERVE)['consent'] and evaluate('window.passwordSubmits') is None)
            document('https://accounts.google.com/v3/signin/identifier', form + '<button id="submit_button">Approve</button>')
            check('Consent appearing alongside credential form is never submitted', evaluate(google_submit_expression('email', EMAIL, EMAIL))['reason'] == 'consent_required' and evaluate('window.submits') is None)
            document('https://accounts.google.com/v3/signin/challenge/pwd', '<input name="Passwd" aria-invalid="true">Wrong password')
            check('Real DOM rejected password reports error', evaluate(GOOGLE_OBSERVE)['error'])
            document('https://accounts.google.com.evil.invalid/signin', form)
            check('Lookalike origin blocks script before credential input', evaluate(google_submit_expression('password', SECRET, EMAIL))['reason'] == 'untrusted' and not evaluate(GOOGLE_OBSERVE)['trusted'])
            document('https://www.dola.com/chat/', '<button onclick="window.downloadClicked=true">Google Play</button><button onclick="window.oauthClicked=true">Googleで続ける</button>')
            check('Dola Google choice avoids Google Play download control', evaluate(dola_click_expression('google')) is True and evaluate('window.oauthClicked') is True and evaluate('window.downloadClicked') is None)
            document('https://www.dola.com/chat/', '<button onclick="window.loginClicked=true">登录</button>')
            check('Dola localized login entry is clickable', evaluate(dola_click_expression('login')) is True and evaluate('window.loginClicked') is True)
            document('https://www.dola.com/chat/', '<div>Confirm your age</div>')
            check('Dola age confirmation is left for manual completion', evaluate(DOLA_OBSERVE)['manual'])
            welcome = f'<h1>ようこそ</h1>新しいアカウントへようこそ Google Workspace {EMAIL}<button onclick="window.accepted=true">理解しました</button>'
            document('https://accounts.google.com/v3/signin/speedbump/workspacetermsofservice', welcome)
            check('Japanese Workspace welcome is recognized before redirect wait', evaluate(GOOGLE_OBSERVE)['welcome'])
            check('Authorized Workspace acknowledgement clicks the localized button', evaluate(onboarding_expression('welcome', EMAIL))['clicked'] and evaluate('window.accepted') is True)
            document('https://accounts.google.com/v3/signin/speedbump/workspacetermsofservice', welcome)
            check('Wrong Workspace identity cannot accept account terms', evaluate(onboarding_expression('welcome', 'other@example.invalid'))['reason']=='account_mismatch' and evaluate('window.accepted') is None)
            consent = f'<h1>登录dola.com</h1>{EMAIL}<p>Google 将允许dola.com访问信息</p><div>名称和个人资料照片</div><div>邮箱</div><button onclick="window.cancelled=true">取消</button><button onclick="window.authorized=true">继续</button>'
            document('https://accounts.google.com/signin/oauth/id', consent)
            check('Screenshot basic OAuth identity screen is recognized as consent', evaluate(GOOGLE_OBSERVE)['consent'])
            check('Authorized basic Dola consent clicks continue and avoids cancel', evaluate(onboarding_expression('consent', EMAIL))['clicked'] and evaluate('window.authorized') is True and evaluate('window.cancelled') is None)
            for label, url, html in (
                ('Additional OAuth scopes stay manual', 'https://accounts.google.com/signin/oauth/id?scope=openid%20https://www.googleapis.com/auth/drive', consent),
                ('Additional permission checkboxes stay manual', 'https://accounts.google.com/signin/oauth/id', consent+'<input type="checkbox">'),
                ('Different application consent stays manual', 'https://accounts.google.com/signin/oauth/id', consent.replace('登录dola.com','登录other.invalid')),
                ('Lookalike application heading stays manual', 'https://accounts.google.com/signin/oauth/id', consent.replace('登录dola.com','登录dola.com.evil.invalid')),
                ('Extra Gmail permissions stay manual', 'https://accounts.google.com/signin/oauth/id', consent+'<div>Read Gmail</div>'),
                ('Changed consent route stays manual', 'https://accounts.google.com/signin/oauth/consent', consent),
                ('Unknown identity cannot authorize consent', 'https://accounts.google.com/signin/oauth/id', consent.replace(EMAIL,'other@example.invalid')),
                ('Challenge appearing before consent prevents click', 'https://accounts.google.com/signin/oauth/id', consent+'<input autocomplete="one-time-code">'),
            ):
                document(url, html)
                check(label, not evaluate(onboarding_expression('consent', EMAIL))['clicked'] and evaluate('window.authorized') is None)
            age='<div role="dialog"><h2>年齢を確認してください</h2><p>続行するには、あなたが18歳以上であることを確認してください。</p><button onclick="window.denied=true">いいえ、違います</button><button onclick="window.ageAccepted=true">OK</button></div>'
            document('https://www.dola.com/auth/callback', age)
            check('Japanese age prompt from screenshot is recognized', evaluate(DOLA_OBSERVE)['age'])
            check('Authorized age action is scoped to modal OK and avoids refusal', evaluate(onboarding_expression('age', EMAIL))['clicked'] and evaluate('window.ageAccepted') is True and evaluate('window.denied') is None)
            document('https://www.dola.com/auth/callback', age+'<input autocomplete="one-time-code">')
            check('Security challenge alongside age prompt is never confirmed', not evaluate(onboarding_expression('age', EMAIL))['clicked'] and evaluate('window.ageAccepted') is None)
            document('https://www.dola.com/auth/callback', age.replace('18歳以上','13歳以上'))
            check('Different age threshold is not asserted', not evaluate(onboarding_expression('age', EMAIL))['clicked'] and evaluate('window.ageAccepted') is None)
            document('https://dola.com.evil.invalid/auth/callback', age)
            check('Lookalike Dola cannot receive age confirmation', not evaluate(onboarding_expression('age', EMAIL))['clicked'] and evaluate('window.ageAccepted') is None)
            document('https://dola.com.evil.invalid/chat/', '<button onclick="window.oauthClicked=true">Google</button>')
            check('Lookalike Dola origin cannot trigger OAuth entry', evaluate(dola_click_expression('google')) is False and evaluate('window.oauthClicked') is None)
            (args.out/'google-dom-checks.json').write_text(json.dumps({'ok': True, 'offline': True, 'checks': checks}, indent=2), encoding='utf-8')
            print(f'{len(checks)} real Chromium offline login DOM checks passed')
        finally:
            socket.close(); worker.shutdown()


if __name__ == '__main__':
    main()

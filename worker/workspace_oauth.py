"""Desktop OAuth: fixed Google endpoints, PKCE and short-lived loopback callbacks."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from proxy_support import ProxyRelay, parse_proxy
from workspace_api import DirectoryError, SCOPES, TOKEN_URI

AUTH_URI = 'https://accounts.google.com/o/oauth2/v2/auth'
OAUTH_SCOPES = [*SCOPES, 'openid', 'email']
ACTIVE = ('waiting', 'exchanging')


def validate_client(value):
    try:
        info = json.loads(value) if isinstance(value, str) else value
        client = info['installed']
        client_id, secret = client['client_id'], client['client_secret']
        if (not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com')
                or not 20 < len(client_id) < 200 or any(c.isspace() for c in client_id)
                or not isinstance(secret, str) or not 5 < len(secret) < 512):
            raise ValueError()
        # Never use auth/token/redirect URLs supplied by an imported credential.
        return {'client_id': client_id, 'client_secret': secret}
    except Exception:
        raise DirectoryError('请导入应用类型为“桌面应用”的 OAuth 客户端 JSON，不是服务账号密钥或网页应用凭据。') from None


@contextmanager
def google_session(proxy):
    import requests
    class Session(requests.Session):
        def request(self, *args, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(*args, **kwargs)
    session, relay = Session(), None
    session.trust_env = False
    try:
        if proxy.get('proxy'):
            relay = ProxyRelay(parse_proxy(proxy))
            route = f'http://127.0.0.1:{relay.port}'
            session.proxies = {'http': route, 'https': route}
        yield session
    finally:
        session.close()
        if relay:
            relay.close()


def token_data(credentials):
    expiry = credentials.expiry
    return {'token': credentials.token, 'refresh_token': credentials.refresh_token,
            'expiry': expiry.replace(tzinfo=timezone.utc).isoformat() if expiry else None}


def credentials_for(record, on_refresh=None):
    from google.oauth2.credentials import Credentials
    saved, client = record.get('oauth') or {}, record['oauthClient']
    if not saved.get('refresh_token'):
        raise DirectoryError('此企业尚未授权或授权已失效，请点击“Google 管理员授权”。')

    class PersistedCredentials(Credentials):
        def refresh(self, request):
            try:
                super().refresh(request)
            except Exception:
                raise DirectoryError('Google 授权刷新失败，请检查代理；若授权已撤销或测试令牌到期，请重新进行管理员授权。') from None
            if on_refresh:
                on_refresh(token_data(self))

    expiry = saved.get('expiry')
    expiry = datetime.fromisoformat(expiry).astimezone(timezone.utc).replace(tzinfo=None) if expiry else None
    return PersistedCredentials(token=saved.get('token'), refresh_token=saved['refresh_token'],
                                token_uri=TOKEN_URI, client_id=client['client_id'], client_secret=client['client_secret'],
                                scopes=OAUTH_SCOPES, expiry=expiry)


def exchange(client, code, verifier, redirect_uri, proxy, nonce, email):
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import verify_oauth2_token
    from google.auth.exceptions import TransportError
    import requests
    stage = 'Google 令牌兑换'
    route_hint = '当前为本机直连，请在企业连接设置中选择可访问 Google 的管理 API 代理。' if not proxy.get('proxy') else '请检查所选管理 API 代理及其上游代理。'
    try:
        with google_session(proxy) as session:
            response = session.post(TOKEN_URI, data={'grant_type': 'authorization_code', 'code': code,
                'client_id': client['client_id'], 'client_secret': client['client_secret'],
                'redirect_uri': redirect_uri, 'code_verifier': verifier}, timeout=(10, 25), allow_redirects=False)
            try:
                if response.status_code != 200:
                    try:
                        error = response.json().get('error')
                    except Exception:
                        error = None
                    known = {'invalid_grant': '授权码已过期、已使用或回调不匹配，请重新发起管理员授权。',
                             'invalid_client': 'OAuth 客户端凭据无效，请重新导入 Google 下载的桌面应用 JSON。',
                             'unauthorized_client': '此 OAuth 客户端不允许桌面授权，请检查客户端类型和 Google 应用配置。',
                             'access_denied': 'Google 拒绝授权兑换，请检查测试用户与企业应用访问策略。'}
                    raise DirectoryError(f'Google 令牌兑换失败（HTTP {response.status_code}）。' +
                                         known.get(error, '请检查桌面客户端配置、测试用户和 API 代理后重新授权。'))
                data = response.json()
            finally:
                response.close()
            if not all(isinstance(data.get(k), str) and data[k] for k in ('access_token', 'refresh_token', 'id_token')):
                raise DirectoryError('Google 未返回离线授权令牌，请重新授权并同意全部请求权限。')
            if not set(SCOPES).issubset(set(data.get('scope', '').split())):
                raise DirectoryError('缺少员工管理或域名读取权限，请重新授权并勾选全部请求权限。')
            # Certificate requests also follow the explicitly selected management proxy.
            stage = 'Google 身份签名校验'
            class NoRedirectRequest(Request):
                def __call__(self, *args, **kwargs):
                    kwargs['allow_redirects'] = False
                    kwargs.setdefault('timeout', 15)
                    return super().__call__(*args, **kwargs)
            identity = verify_oauth2_token(data['id_token'], NoRedirectRequest(session=session), client['client_id'], clock_skew_in_seconds=30)
            if identity.get('nonce') != nonce or identity.get('email_verified') is not True:
                raise DirectoryError('Google 登录身份校验失败，请重新授权。')
            if identity.get('email', '').casefold() != email.casefold():
                raise DirectoryError('授权账号与填写的管理员邮箱不一致，请选择该企业的管理员账号重新授权。')
            expires = int(data.get('expires_in', 3600))
            return {'token': data['access_token'], 'refresh_token': data['refresh_token'],
                    'expiry': (datetime.now(timezone.utc) + timedelta(seconds=max(0, min(expires, 86400)))).isoformat()}
    except DirectoryError:
        raise
    except requests.exceptions.ProxyError:
        raise DirectoryError(stage + '未完成：代理连接失败。' + route_hint) from None
    except requests.exceptions.Timeout:
        raise DirectoryError(stage + '超时。' + route_hint) from None
    except requests.exceptions.SSLError:
        raise DirectoryError(stage + '未完成：TLS 证书连接失败，请检查系统时间及代理证书配置。') from None
    except requests.exceptions.ConnectionError:
        raise DirectoryError(stage + '未完成：无法连接 Google。' + route_hint) from None
    except TransportError:
        raise DirectoryError(stage + '未完成：无法读取 Google 身份签名公钥。' + route_hint) from None
    except Exception:
        raise DirectoryError(stage + '未完成，请检查网络、系统时间及客户端配置后重新授权。') from None


class OAuthAttempt:
    """Only public status escapes this object; callback codes never enter logs."""
    def __init__(self, record, proxy, exchange_fn, complete, ttl=300):
        self.id = uuid.uuid4().hex[:16]
        self.workspace_id, self.email = record['id'], record['adminEmail']
        self.state, self.message = 'waiting', '请在系统浏览器中选择对应的企业管理员并完成 Google 授权。'
        self.expires = time.monotonic() + ttl
        self.lock, self.done = threading.RLock(), threading.Event()
        self.csrf, self.nonce, self.verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        attempt = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass

            def reply(self, code, message):
                body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Cloak Workbench</title>'
                        '<h2>Cloak Workbench</h2><p>' + escape(message) + '</p></html>').encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
                try:
                    self.end_headers(); self.wfile.write(body)
                except (ConnectionError, OSError):
                    pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                if self.headers.get('Host') != f'127.0.0.1:{attempt.server.server_port}' or parsed.path != '/' or len(self.path) > 16384:
                    self.reply(400, 'Invalid callback.'); return
                try:
                    query = parse_qs(parsed.query, max_num_fields=20)
                except ValueError:
                    self.reply(400, 'Invalid callback parameters.'); return
                states = query.get('state', [])
                if len(states) != 1 or not hmac.compare_digest(states[0].encode('utf-8'), attempt.csrf.encode('ascii')):
                    self.reply(400, 'Invalid OAuth state. Return to the workbench.'); return
                with attempt.lock:
                    if attempt.state != 'waiting' or time.monotonic() > attempt.expires:
                        self.reply(409, 'This authorization has ended. Return to the workbench.'); return
                    if query.get('error'):
                        attempt.finish('cancelled', 'Google 授权被拒绝或取消，可重新发起授权。')
                        self.reply(200, '已取消 Google 授权，可以关闭此页并返回工作台。'); return
                    codes = query.get('code', [])
                    if len(codes) != 1 or not codes[0]:
                        self.reply(400, 'Missing authorization code.'); return
                    attempt.state, attempt.message = 'exchanging', '正在核实 Google 管理员身份与企业域名…'
                try:
                    tokens = exchange_fn(record['oauthClient'], codes[0], attempt.verifier, attempt.redirect_uri,
                                         proxy, attempt.nonce, attempt.email)
                    # Registry commit and cancellation share the worker lock in complete().
                    complete(attempt, tokens)
                    self.reply(200, attempt.message + ' 可以关闭此页并返回工作台。')
                except DirectoryError as exc:
                    attempt.finish('failed', str(exc))
                    self.reply(200, str(exc) + ' 请返回工作台，处理后重新发起授权；不要刷新本次回调链接。')
                except Exception:
                    attempt.finish('failed', '授权未保存，请检查网络和本机存储后重新授权。')
                    self.reply(200, '授权未保存，请返回工作台检查网络或本机存储，然后重新授权。')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = .2
        self.redirect_uri = f'http://127.0.0.1:{self.server.server_port}/'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        self.authorization_url = AUTH_URI + '?' + urlencode({'client_id': record['oauthClient']['client_id'],
            'redirect_uri': self.redirect_uri, 'response_type': 'code', 'scope': ' '.join(OAUTH_SCOPES),
            'state': self.csrf, 'nonce': self.nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent select_account', 'login_hint': self.email})

    def start(self):
        threading.Thread(target=self._listen, daemon=True, name='workspace-oauth-' + self.id).start()

    def _listen(self):
        try:
            while not self.done.is_set():
                if time.monotonic() > self.expires:
                    self.finish('expired', '授权等待已超时，请重新点击管理员授权。'); break
                self.server.handle_request()
        finally:
            self.server.server_close()

    def finish(self, state, message):
        with self.lock:
            if self.state not in ACTIVE:
                return
            self.state, self.message = state, message
            self.done.set()

    def snapshot(self):
        with self.lock:
            return {'id': self.id, 'workspaceId': self.workspace_id, 'adminEmail': self.email,
                    'state': self.state, 'message': self.message,
                    'expiresIn': max(0, int(self.expires - time.monotonic()))}

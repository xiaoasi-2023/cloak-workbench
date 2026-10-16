"""Google Directory API transport. Service keys never determine network endpoints."""
from contextlib import contextmanager
import json
from urllib.parse import quote

from proxy_support import ProxyRelay, parse_proxy

SCOPES = ['https://www.googleapis.com/auth/admin.directory.user',
          'https://www.googleapis.com/auth/admin.directory.domain.readonly']
TOKEN_URI = 'https://oauth2.googleapis.com/token'
BASE = 'https://admin.googleapis.com/admin/directory/v1/'


class DirectoryError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def validate_key(value):
    """Validate structure and signer without exposing key material in errors."""
    try:
        from google.oauth2 import service_account
        info = json.loads(value) if isinstance(value, str) else dict(value)
        if info.get('type') != 'service_account' or not info.get('client_id') or not info.get('client_email', '').endswith('.iam.gserviceaccount.com'):
            raise ValueError()
        # Never honor credential-supplied token URLs or universe domains.
        safe = {k: info[k] for k in ('type', 'client_email', 'client_id', 'private_key', 'private_key_id', 'project_id') if k in info}
        safe['token_uri'] = TOKEN_URI
        service_account.Credentials.from_service_account_info(safe, scopes=SCOPES)
        return safe
    except Exception:
        raise DirectoryError('请选择有效的 Google 服务账号 JSON 密钥，不支持普通 API Key 或 OAuth 客户端文件。') from None


class DirectoryClient:
    def __init__(self, record, proxy, on_refresh=None):
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2 import service_account
        import requests
        self.relay = None
        self.session = None
        self.auth_session = requests.Session()
        self.auth_session.trust_env = False
        self.route_hint = ('请检查所选管理 API 代理及其上游代理。' if proxy.get('proxy') else
                           '当前为本机直连，请在企业连接设置中选择可访问 Google 的管理 API 代理。')
        try:
            if proxy.get('proxy'):
                self.relay = ProxyRelay(parse_proxy(proxy))
                route = f'http://127.0.0.1:{self.relay.port}'
                self.auth_session.proxies = {'http': route, 'https': route}
            if record.get('authType') == 'oauth':
                from workspace_oauth import credentials_for
                credentials = credentials_for(record, on_refresh)
            else:
                credentials = service_account.Credentials.from_service_account_info(record['key'], scopes=SCOPES).with_subject(record['adminEmail'])
            self.session = AuthorizedSession(credentials, auth_request=Request(session=self.auth_session), refresh_timeout=15, max_refresh_attempts=0)
            self.session.trust_env = False
            self.session.proxies = dict(self.auth_session.proxies)
        except DirectoryError:
            self.close()
            raise
        except Exception:
            self.close()
            raise DirectoryError('无法初始化企业连接，请检查密钥、代理和依赖。') from None

    def close(self):
        if self.session:
            self.session.close()
        self.auth_session.close()
        if self.relay:
            self.relay.close()

    def request(self, method, path, body=None, params=None):
        try:
            response = self.session.request(method, BASE + path, json=body, params=params, timeout=(10, 25),
                                            allow_redirects=False, max_allowed_time=45)
        except DirectoryError:
            raise
        except Exception:
            raise DirectoryError('Google 请求未完成。' + self.route_hint
                                 + (' 写入结果未知，请先同步核实，不要直接重复提交。' if method != 'GET' else ''), method != 'GET') from None
        try:
            if response.status_code >= 400:
                status = response.status_code
                messages = {400: 'Google 拒绝参数，请检查域名、密码策略和已存在的组织单位。',
                            401: 'Google 认证失败，请重新进行管理员授权，或检查服务账号密钥。',
                            403: 'Google 拒绝权限或配额，请检查授权的两项作用域、管理员权限、Admin SDK API 与账号额度。',
                            404: 'Google 用户或组织单位不存在。', 409: 'Google 账号已存在，请同步列表后处理。',
                            429: 'Google 请求限流，本次未自动重试，请稍后重新预览。'}
                uncertain = method != 'GET' and status >= 500
                raise DirectoryError(messages.get(status, f'Google API 返回 HTTP {status}。') +
                                     (' 写入结果未知，请先同步核实。' if uncertain else ''), uncertain)
            if not 200 <= response.status_code < 300:
                raise DirectoryError('Google 返回了意外重定向，已停止请求。', method != 'GET')
            return response.json() if response.content else {}
        except DirectoryError:
            raise
        except Exception:
            raise DirectoryError('Google 返回内容无法解析，请同步核实结果。', method != 'GET') from None
        finally:
            response.close()

    def identity(self, email):
        return self.request('GET', 'users/' + quote(email, safe=''))

    def domains(self, customer):
        return self.request('GET', 'customer/' + quote(customer, safe='') + '/domains').get('domains', [])

    def users(self, customer, cancel):
        rows, token, seen = [], None, set()
        while not cancel.is_set():
            result = self.request('GET', 'users', params={'customer': customer, 'maxResults': 500, 'orderBy': 'email',
                                                          **({'pageToken': token} if token else {})})
            rows.extend(result.get('users', []))
            token = result.get('nextPageToken')
            if not token:
                return rows
            if token in seen:
                raise DirectoryError('Google 返回重复分页标记，目录未覆盖，请重试同步。')
            seen.add(token)
        return None

    def create(self, body):
        return self.request('POST', 'users', body)

    def update(self, user_id, body):
        return self.request('PATCH', 'users/' + quote(user_id, safe=''), body)

    def user(self, user_id):
        return self.request('GET', 'users/' + quote(user_id, safe=''))

    def delete(self, user_id):
        return self.request('DELETE', 'users/' + quote(user_id, safe=''))


@contextmanager
def connect(record, proxy, on_refresh=None):
    client = DirectoryClient(record, proxy, on_refresh)
    try:
        yield client
    finally:
        client.close()

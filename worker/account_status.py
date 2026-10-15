"""Read-only, conservative Dola page observations. Never submits a message."""
from datetime import datetime, timezone
from urllib.parse import urlsplit


OBSERVE_PAGE = r"""(() => {
  const visible = e => e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const controls = [...document.querySelectorAll('button,a,[role="button"]')].filter(visible);
  const text = e => (e.innerText || e.getAttribute('aria-label') || '').trim();
  const login = [...document.querySelectorAll('[class*="login-btn-header"]')].some(visible)
    || controls.some(e => /^(log\s?in|sign\s?in|登录|登入|ログイン|로그인)$/i.test(text(e)));
  // An explicit sign-out control is positive evidence. A cookie, avatar, or
  // anonymous chat interface alone is insufficient to establish login state.
  const logout = controls.some(e => /^(log\s?out|sign\s?out|退出登录|退出登入|ログアウト|로그아웃)$/i.test(text(e)));
  // Dola's chat loader returns an explicit server login flag. Only project
  // booleans: never return account identity, tokens or the loader payload.
  // The current account control corroborates it after client-side navigation.
  const layout = window._ROUTER_DATA?.loaderData?.chat_layout?.chat_layout;
  const settings = layout?.userSetting;
  const serverLogin = settings?.code === 0 && typeof settings?.data?.is_login === 'boolean'
    ? settings.data.is_login : null;
  const user = layout?.accountInfo?.data;
  const accountIdentity = !!(user && typeof user.user_id_str === 'string' && /^[1-9]\d*$/.test(user.user_id_str));
  const accountMenu = !!document.querySelector('[data-sidebar-footer-account-menu] button');
  const page = (document.body?.innerText || '').slice(0, 16000);
  const restricted = /この国または地域ではDolaは利用できません|Dola is (?:not available|unavailable) in (?:your|this) (?:country|region)|当前(?:国家|地区).*?(?:不可用|不支持)/i.test(page);
  return {url: location.href, ready: document.readyState, login, logout, restricted,
          serverLogin, accountIdentity, accountMenu};
})()"""


def classify_observation(observation):
    host = (urlsplit(observation.get('url', '')).hostname or '').lower()
    if host != 'dola.com' and not host.endswith('.dola.com'):
        return 'unknown', 'not_dola_page'
    parsed = urlsplit(observation.get('url', ''))
    if observation.get('restricted') or parsed.path.lower().startswith('/security/region-restricted'):
        return 'region_blocked', 'region_message'
    path = urlsplit(observation.get('url', '')).path.lower()
    if any(segment in ('login', 'signin') for segment in path.split('/')) or observation.get('login'):
        return 'login_required', 'visible_login'
    if observation.get('logout') and observation.get('ready') == 'complete':
        return 'logged_in', 'visible_logout'
    if observation.get('ready') == 'complete':
        if (observation.get('serverLogin') is True and observation.get('accountIdentity') is True
                and observation.get('accountMenu') is True):
            return 'logged_in', 'page_session_confirmed'
        if observation.get('serverLogin') is False and not observation.get('accountMenu'):
            return 'login_required', 'page_session_signed_out'
    return 'unknown', 'insufficient_evidence'


def checked_result(status, reason):
    return {'status': status, 'reason': reason,
            # Page login evidence never establishes a match to a bound email.
            'identityStatus': 'unverified' if status == 'logged_in' else 'unknown',
            'checkedAt': datetime.now(timezone.utc).isoformat()}


def validate_account(payload, error):
    result = {}
    for key, limit in (('accountEmail', 254), ('accountGroup', 60), ('accountNotes', 1000)):
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise error(400, f'Invalid {key}')
        result[key] = value.strip()
    return result

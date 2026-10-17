"""Password-protected portable configuration, with preview and recoverable commits."""
import base64
import copy
import hashlib
import json
import os
import re
import secrets
import time

from account_management import now
from workspace_api import DirectoryError, validate_key
from workspace_oauth import validate_client

MAX_FILE = 12 * 1024 * 1024
MAX_PLAIN = 8 * 1024 * 1024
FILES = ('proxies.json', 'workspaces.json', 'login-accounts.json', 'profiles.json')
KINDS = ('proxies', 'workspaces', 'accounts', 'profiles')
LABELS = dict(zip(KINDS, ('代理', '企业连接', '账号', '环境')))
JOURNAL = '.config-import-journal.json'


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def atomic(path, content):
    temp = path.with_name(path.name + '.transfer.tmp')
    with temp.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def recover_import(directory):
    """An interrupted multi-file commit rolls back before any stores are loaded."""
    journal = directory / JOURNAL
    if not journal.exists():
        return
    if journal.stat().st_size > MAX_FILE * 4:
        raise OSError('配置恢复记录过大，请保留资料并检查日志。')
    originals = json.loads(journal.read_text(encoding='utf-8'))
    if not isinstance(originals, dict) or set(originals) != set(FILES):
        raise OSError('配置恢复记录不完整，请保留资料并检查日志。')
    decoded = {name: base64.b64decode(value, validate=True) if value is not None else None
               for name, value in originals.items()}
    for name, content in decoded.items():
        if content is None:
            (directory / name).unlink(missing_ok=True)
        else:
            atomic(directory / name, content)
    journal.unlink()


def encrypt(document, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8'))
    body = packed(document)
    if len(body) > MAX_PLAIN:
        raise ValueError('配置超过备份大小上限，请减少账号或环境数量。')
    cipher = AESGCM(key).encrypt(nonce, body, b'qihang-config-v1')
    return json.dumps({'format': 'qihang-config', 'version': 1,
                       'salt': base64.b64encode(salt).decode(), 'nonce': base64.b64encode(nonce).decode(),
                       'ciphertext': base64.b64encode(cipher).decode()}, ensure_ascii=False)


def decrypt(text, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_FILE:
        raise ValueError('备份文件过大或格式不正确。')
    try:
        envelope = json.loads(text)
        if envelope.get('format') != 'qihang-config' or type(envelope.get('version')) is not int or envelope['version'] != 1:
            raise ValueError()
        salt, nonce, cipher = (base64.b64decode(envelope[k], validate=True) for k in ('salt', 'nonce', 'ciphertext'))
        if len(salt) != 16 or len(nonce) != 12 or not 16 <= len(cipher) <= MAX_PLAIN + 16:
            raise ValueError()
        key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8'))
        return json.loads(AESGCM(key).decrypt(nonce, cipher, b'qihang-config-v1'))
    except Exception:
        raise ValueError('无法打开备份：密码不正确，或文件已损坏、版本不受支持。') from None


class ConfigTransfer:
    def __init__(self, worker, error, validate_profile):
        self.worker, self.error, self.validate_profile = worker, error, validate_profile
        self.plans = {}

    def _password(self, payload):
        value = payload.get('password')
        if not isinstance(value, str) or not 8 <= len(value) <= 256:
            raise self.error(400, '请填写 8–256 位备份密码，换电脑导入时需要相同密码。')
        return value

    def _stores(self):
        return [self.worker.catalog.entries, self.worker.workspaces.records,
                self.worker.login_accounts.entries, self.worker.profiles]

    def _stamp(self):
        return hashlib.sha256(packed(self._stores())).hexdigest()

    def _document(self, accounts=False, profiles=False):
        w = self.worker
        proxies = [w.catalog.validate(r) for r in w.catalog.entries.values()]
        tenants = []
        for r in w.workspaces.records.values():
            record = {k: copy.deepcopy(r[k]) for k in ('id', 'name', 'adminEmail', 'enabled', 'authType',
                      'proxyId', 'customerId', 'domains', 'key', 'oauthClient') if k in r}
            if r.get('authType') == 'oauth' and (r.get('oauth') or {}).get('refresh_token'):
                # Access tokens and pending callback state are deliberately not portable.
                record['oauth'] = {'refresh_token': r['oauth']['refresh_token']}
            tenants.append(record)
        return {'format': 'qihang-config-content', 'version': 1, 'createdAt': now(), 'proxies': proxies,
                'workspaces': tenants,
                'accounts': copy.deepcopy(list(w.login_accounts.entries.values())) if accounts or profiles else [],
                'profiles': [self.validate_profile(r) for r in w.profiles.values()] if profiles else []}

    def export(self, payload):
        password = self._password(payload)
        if any(type(payload.get(k, False)) is not bool for k in ('includeAccounts', 'includeProfiles')):
            raise self.error(400, '备份选项格式不正确。')
        with self.worker.lock:
            document = self._document(payload.get('includeAccounts', False), payload.get('includeProfiles', False))
        try:
            text = encrypt(document, password)
        except ValueError as exc:
            raise self.error(400, str(exc)) from None
        return {'text': text, 'counts': {k: len(document[k]) for k in KINDS}, 'createdAt': document['createdAt']}

    def _validate(self, document):
        if (not isinstance(document, dict) or document.get('format') != 'qihang-config-content'
                or type(document.get('version')) is not int or document['version'] != 1):
            raise ValueError('不支持的配置备份版本。')
        result = {k: [] for k in KINDS}
        for kind in KINDS:
            records = document.get(kind)
            if not isinstance(records, list) or len(records) > 10000:
                raise ValueError('备份中的配置列表格式不正确或数量过多。')
            ids, identities = set(), set()
            for raw in records:
                if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', raw['id']) or raw['id'] in ids:
                    raise ValueError('备份中有重复或无效的记录标识。')
                ids.add(raw['id'])
                if kind == 'proxies':
                    record = self.worker.catalog.validate(raw)
                elif kind == 'profiles':
                    record = self.validate_profile(raw)
                elif kind == 'workspaces':
                    name, email, auth = raw.get('name'), raw.get('adminEmail'), raw.get('authType', 'service_account')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not self._email(email) or type(raw.get('enabled', True)) is not bool:
                        raise ValueError('备份中的企业名称或管理员邮箱不正确。')
                    record = {k: copy.deepcopy(raw[k]) for k in ('id', 'name', 'adminEmail', 'proxyId', 'customerId', 'domains') if k in raw}
                    record.update(authType=auth, enabled=raw.get('enabled', True))
                    if auth == 'oauth':
                        record['oauthClient'] = validate_client({'installed': raw.get('oauthClient')})
                        oauth = raw.get('oauth', {})
                        if not isinstance(oauth, dict):
                            raise ValueError('企业授权格式不正确。')
                        token = oauth.get('refresh_token', '')
                        if not isinstance(token, str) or len(token) > 16384:
                            raise ValueError('企业授权格式不正确。')
                        if token:
                            record['oauth'] = {'refresh_token': token}
                    elif auth == 'service_account':
                        record['key'] = validate_key(raw.get('key'))
                    else:
                        raise ValueError('不支持的企业连接方式。')
                    domains = record.get('domains', [])
                    if not isinstance(domains, list) or len(domains) > 1000 or any(not isinstance(d, str) or len(d) > 254 for d in domains):
                        raise ValueError('企业域名列表格式不正确。')
                    if not isinstance(record.get('customerId', ''), str) or len(record.get('customerId', '')) > 128:
                        raise ValueError('企业标识格式不正确。')
                else:
                    record = {k: copy.deepcopy(raw[k]) for k in ('id', 'name', 'email', 'password', 'group', 'notes', 'createdAt',
                        'updatedAt', 'workspaceId', 'workspaceName', 'workspaceUserId', 'workspaceSuspended', 'workspaceMissing') if k in raw}
                    if not self._email(record.get('email')):
                        raise ValueError('备份中的账号邮箱不正确。')
                    for k, limit in (('name', 120), ('password', 4096), ('group', 60), ('notes', 1000),
                                     ('workspaceName', 80), ('workspaceUserId', 128), ('createdAt', 64), ('updatedAt', 64)):
                        v = record.get(k, '')
                        if not isinstance(v, str) or len(v) > limit or '\x00' in v:
                            raise ValueError('备份中的账号字段格式不正确。')
                    record.setdefault('name', record['email'])
                    record.setdefault('password', '')
                for field in ('proxyId', 'loginAccountId', 'workspaceId'):
                    ref = record.get(field, '')
                    if not isinstance(ref, str) or ref and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ref):
                        raise ValueError('备份中的配置关联不正确。')
                identity = self._identity(kind, record)
                if identity in identities:
                    raise ValueError('备份中存在重复的账号或企业身份。')
                identities.add(identity)
                result[kind].append(record)
        for kind, field, target in (('workspaces', 'proxyId', 'proxies'), ('accounts', 'workspaceId', 'workspaces'),
                                    ('profiles', 'proxyId', 'proxies'), ('profiles', 'loginAccountId', 'accounts')):
            known = {r['id'] for r in result[target]}
            for r in result[kind]:
                if r.get(field) and r[field] not in known:
                    raise ValueError('备份缺少被关联的代理、企业或账号，不能部分恢复。')
        return result

    @staticmethod
    def _email(value):
        return isinstance(value, str) and bool(re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,120}\.[^\s@]{2,30}', value))

    @staticmethod
    def _identity(kind, r):
        if kind == 'accounts':
            return r['email'].casefold()
        if kind == 'workspaces':
            return ('customer', r['customerId']) if r.get('customerId') else ('admin', r['adminEmail'].casefold())
        return r['id']

    def _plan(self, incoming, mode):
        targets = copy.deepcopy(self._stores())
        maps, rows = {}, []
        for kind, store in zip(KINDS, targets):
            mapping = maps[kind] = {}
            for source in incoming[kind]:
                candidate = copy.deepcopy(source)
                existing = next((r for r in store.values() if self._identity(kind, r) == self._identity(kind, candidate)), None)
                # A second connection for the same admin is also a conflict even
                # if only one side has discovered its Google customer ID yet.
                if kind == 'workspaces' and existing is None:
                    existing = next((r for r in store.values() if r['adminEmail'].casefold() == candidate['adminEmail'].casefold()), None)
                target_id = existing['id'] if existing else candidate['id']
                if not existing and target_id in store:
                    target_id = secrets.token_hex(8)
                if kind == 'profiles' and not existing:
                    while (self.worker.data_dir / 'profiles' / target_id).exists() or target_id in store and target_id != candidate['id']:
                        target_id = secrets.token_hex(8)
                mapping[source['id']] = target_id
                action = 'skip' if existing and mode == 'skip' else 'replace' if existing else 'add'
                rows.append({'kind': kind, 'type': LABELS[kind], 'name': candidate.get('email') if kind == 'accounts' else candidate['name'],
                             'action': action, 'authorization': ('portable' if (candidate.get('oauth') or {}).get('refresh_token') or candidate.get('key') else 'missing') if kind == 'workspaces' else ''})
                if action == 'skip':
                    continue
                candidate['id'] = target_id
                for field, ref_kind in (('proxyId', 'proxies'), ('workspaceId', 'workspaces'), ('loginAccountId', 'accounts')):
                    if candidate.get(field):
                        candidate[field] = maps[ref_kind][candidate[field]]
                if kind == 'proxies':
                    candidate.update(revision=(existing or {}).get('revision', 0) + 1, check=None)
                if kind == 'workspaces':
                    candidate.update(revision=(existing or {}).get('revision', 0) + 1, status='unchecked', users=[])
                if kind == 'profiles' and existing:
                    # Existing local browser data stays in its original directory.
                    # Changing a binding must not mislabel that browser's cookies.
                    if existing.get('loginAccountId', '') != candidate.get('loginAccountId', ''):
                        raise self.error(409, '同名标识的环境绑定账号不同，请选择跳过已有配置，或先解除原绑定。')
                store[target_id] = candidate
        bound = set()
        for profile in targets[3].values():
            account = profile.get('loginAccountId')
            if account and account in bound:
                raise self.error(409, '备份中的账号已被本机其他环境绑定。请先解除冲突绑定，或在原电脑导出时取消环境设置。')
            if account:
                bound.add(account)
        return targets, rows

    def preview(self, payload):
        password = self._password(payload)
        mode = payload.get('mode', 'skip')
        if mode not in ('skip', 'replace'):
            raise self.error(400, '请选择跳过或替换已有配置。')
        try:
            document = decrypt(payload.get('text'), password)
            with self.worker.lock:
                incoming = self._validate(document)
                _, rows = self._plan(incoming, mode)
                self.plans = {k: v for k, v in self.plans.items() if v['expires'] > time.monotonic()}
                if len(self.plans) >= 4:
                    self.plans.pop(next(iter(self.plans)))
                pid = secrets.token_urlsafe(24)
                self.plans[pid] = dict(incoming=incoming, mode=mode, stamp=self._stamp(),
                                       passwordHash=hashlib.sha256(password.encode('utf-8')).digest(), expires=time.monotonic() + 600)
        except (ValueError, DirectoryError, TypeError, KeyError, AttributeError):
            raise self.error(400, '无法预览备份：密码不正确、文件损坏或配置格式不受支持。') from None
        return {'planId': pid, 'createdAt': document.get('createdAt', ''), 'rows': rows,
                'counts': {a: sum(r['action'] == a for r in rows) for a in ('add', 'replace', 'skip')},
                'note': '企业授权将随配置恢复；导入后请检测企业连接并同步员工。Google 已撤销或过期的授权仍需重新授权。'}

    def _idle(self):
        w = self.worker
        if (any(w.state(pid)['state'] in ('starting', 'running', 'closing') for pid in w.profiles)
                or any(w.workspaces._busy(wid) for wid in w.workspaces.records)
                or w.catalog.inflight or any(j['state'] in ('queued', 'running', 'cancelling') for j in w.accounts.jobs.values())
                or any(j['state'] in (*w.google_login.ACTIVE, 'needs_attention') for j in w.google_login.jobs.values())
                or any(j['state'] in ('preparing', 'running', 'needs_attention', 'cancelling') for j in w.videos.jobs.values())):
            raise self.error(409, '请先关闭浏览器并结束登录、检测、企业或视频任务，再导入配置。')

    def apply(self, payload):
        password = self._password(payload)
        with self.worker.lock:
            plan = self.plans.get(payload.get('planId', ''))
            if not plan or plan['expires'] < time.monotonic():
                raise self.error(409, '导入预览已过期，请重新选择备份并预览。')
            if not secrets.compare_digest(plan['passwordHash'], hashlib.sha256(password.encode('utf-8')).digest()):
                raise self.error(409, '备份密码已改变，请重新预览。')
            self._idle()
            if plan['stamp'] != self._stamp():
                raise self.error(409, '本机配置在预览后发生变化，请重新预览，避免覆盖最新资料。')
            targets, rows = self._plan(plan['incoming'], plan['mode'])
            if not any(r['action'] != 'skip' for r in rows):
                self.plans.clear()
                return {'imported': 0, 'skipped': len(rows), 'backupPath': ''}
            directory = self.worker.data_dir
            backup = directory / 'config-backups' / ('before-import-' + secrets.token_hex(8) + '.qhbackup')
            backup.parent.mkdir(exist_ok=True)
            originals = {name: base64.b64encode((directory / name).read_bytes()).decode() if (directory / name).exists() else None for name in FILES}
            previous = self._stores()
            try:
                atomic(backup, encrypt(self._document(True, True), password).encode('utf-8'))
                atomic(directory / JOURNAL, packed(originals))
                for name, store in zip(FILES, targets):
                    atomic(directory / name, packed(store if name == 'workspaces.json' else list(store.values())))
                self.worker.catalog.entries, self.worker.workspaces.records, self.worker.login_accounts.entries, self.worker.profiles = targets
                (directory / JOURNAL).unlink()
            except Exception:
                self.worker.catalog.entries, self.worker.workspaces.records, self.worker.login_accounts.entries, self.worker.profiles = previous
                try:
                    recover_import(directory)
                except Exception:
                    raise self.error(500, '配置恢复未完成，请关闭程序后重新启动以自动恢复，勿继续修改资料。') from None
                raise self.error(500, '导入未完成，原配置已恢复。请检查磁盘空间和目录权限。') from None
            self.plans.clear()
            return {'imported': sum(r['action'] != 'skip' for r in rows), 'skipped': sum(r['action'] == 'skip' for r in rows),
                    'backupPath': str(backup), 'note': '已恢复配置和授权资料。请检测企业连接、同步员工，并核实新电脑的上游代理地址。'}

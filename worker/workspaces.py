"""Multi-tenant Workspace connections, directory snapshots and explicit batch jobs."""
import copy
import csv
import hashlib
import io
import json
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager

from account_management import now
from employee_names import employee_identity
from proxy_sessions import effective_entry
from workspace_api import DirectoryError, SCOPES, connect, validate_key
from workspace_oauth import ACTIVE, OAuthAttempt, exchange, validate_client


class Workspaces:
    def __init__(self, worker, error):
        self.worker, self.error = worker, error
        self.path = worker.data_dir / 'workspaces.json'
        self.records = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        for record in self.records.values():
            last = record.get('lastJob') or {}
            if last.get('state') in ('queued', 'running', 'cancelling'):
                last.update(state='interrupted', finishedAt=now())
                last.setdefault('results', []).append({'success': False, 'uncertain': True,
                    'message': '上次程序在任务期间退出；部分 Google 操作结果未知，请先同步核实，不能直接重复创建。'})
        self.drafts, self.jobs = {}, {}
        self.closed = False
        self.client_factory = connect
        self.oauth_attempts = {}
        self.oauth_exchange = exchange

    def _save(self):
        temp = self.path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def _commit(self, wid, record):
        previous = self.records.get(wid)
        self.records[wid] = record
        try:
            self._save()
        except Exception:
            if previous is None:
                self.records.pop(wid, None)
            else:
                self.records[wid] = previous
            raise self.error(500, '企业数据保存失败，原配置保留。') from None

    def entry(self, wid):
        if wid not in self.records:
            raise self.error(404, '企业连接不存在。')
        return self.records[wid]

    def public(self, record):
        keys = ('id', 'name', 'adminEmail', 'proxyId', 'enabled', 'revision', 'customerId', 'domains', 'checkedAt', 'syncedAt', 'status', 'lastJob')
        result = {key: copy.deepcopy(record.get(key)) for key in keys}
        oauth = record.get('authType') == 'oauth'
        credential = record.get('oauthClient' if oauth else 'key') or {}
        result.update(authType='oauth' if oauth else 'service_account', clientId=credential.get('client_id', ''),
                      serviceEmail=credential.get('client_email', ''), keySet=bool(credential),
                      authorized=bool((record.get('oauth') or {}).get('refresh_token')) if oauth else True,
                      userCount=len(record.get('users', [])))
        attempts = [a for a in self.oauth_attempts.values() if a.workspace_id == record['id']]
        result['authorization'] = attempts[-1].snapshot() if attempts else copy.deepcopy(record.get('lastAuthorization'))
        last = record.get('lastJob') or {}
        if last.get('id') in self.jobs:
            result['lastJob'] = self.snapshot(last['id'])
        return result

    def list(self):
        with self.worker.lock:
            return {'workspaces': [self.public(r) for r in self.records.values()], 'scopes': SCOPES}

    def users(self, wid):
        with self.worker.lock:
            record = self.entry(wid)
            users = copy.deepcopy(record.get('users', []))
            accounts = {a.get('workspaceUserId'): a for a in self.worker.login_accounts.entries.values()
                        if a.get('workspaceId') == wid and not a.get('workspaceMissing')}
            for user in users:
                account = accounts.get(user['id'])
                # Only this management view exposes locally known passwords. Never
                # copy passwords into directory snapshots, previews or job results.
                user['password'] = account.get('password', '') if account and account['email'].casefold() == user['email'].casefold() else ''
            return {'users': users, 'syncedAt': record.get('syncedAt'),
                    'workspace': self.public(record)}

    def _busy(self, wid):
        return (any(j['workspaceId'] == wid and j['state'] in ('queued', 'running', 'cancelling') for j in self.jobs.values())
                or any(a.workspace_id == wid and a.state in ACTIVE for a in self.oauth_attempts.values()))

    def account_busy(self, email):
        return any(j['state'] in ('queued', 'running', 'cancelling') and email.lower() in j.get('reservedEmails', set())
                   for j in self.jobs.values())

    def upsert(self, payload):
        with self.worker.lock:
            if set(payload) - {'id', 'name', 'adminEmail', 'proxyId', 'enabled', 'keyJson', 'authType', 'oauthClientJson', 'expectedRevision'}:
                raise self.error(400, '企业表单包含不支持的字段。')
            old = self.entry(payload['id']) if payload.get('id') else None
            if 'expectedRevision' in payload:
                revision = payload['expectedRevision']
                if type(revision) is not int or not old or revision != old['revision']:
                    raise self.error(409, '企业配置已更新，请重新载入连接设置后再保存，避免覆盖最新网络线路。')
            if old and self._busy(old['id']):
                raise self.error(409, '请等待或取消此企业正在执行的任务。')
            name, admin, proxy_id = payload.get('name', ''), payload.get('adminEmail', ''), payload.get('proxyId', (old or {}).get('proxyId', ''))
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise self.error(400, '请填写 1–80 字企业名称。')
            if not isinstance(admin, str) or not re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,120}\.[^\s@]{2,30}', admin):
                raise self.error(400, '请填写完整的管理员邮箱。')
            if not isinstance(proxy_id, str):
                raise self.error(400, '请选择管理 API 使用的网络线路。')
            if proxy_id:
                self.worker.catalog.entry(proxy_id)
            enabled = payload.get('enabled', (old or {}).get('enabled', True))
            if not isinstance(enabled, bool):
                raise self.error(400, '启用状态必须为布尔值。')
            auth_type = payload.get('authType', (old or {}).get('authType', 'service_account'))
            if auth_type not in ('service_account', 'oauth'):
                raise self.error(400, '请选择管理员 OAuth 或服务账号连接方式。')
            supplied = payload.get('oauthClientJson' if auth_type == 'oauth' else 'keyJson')
            if supplied:
                if not isinstance(supplied, str) or len(supplied) > 32768:
                    raise self.error(400, '服务账号 JSON 文件过大或格式错误。')
                try:
                    key = validate_client(supplied) if auth_type == 'oauth' else validate_key(supplied)
                except DirectoryError as exc:
                    raise self.error(400, str(exc)) from None
            elif old and old.get('authType', 'service_account') == auth_type:
                key = old['oauthClient' if auth_type == 'oauth' else 'key']
            else:
                raise self.error(400, '请导入桌面应用 OAuth 客户端 JSON。' if auth_type == 'oauth' else '请导入服务账号 JSON 密钥。')
            wid = old['id'] if old else uuid.uuid4().hex[:12]
            record = dict(copy.deepcopy(old or {}), id=wid, name=name.strip(), adminEmail=admin.strip(), proxyId=proxy_id,
                          enabled=enabled, authType=auth_type)
            if auth_type == 'oauth':
                if not old or old.get('authType') != 'oauth' or old.get('oauthClient') != key or old['adminEmail'].casefold() != admin.strip().casefold():
                    record.pop('oauth', None)
                record['oauthClient'] = key
                record.pop('key', None)
            else:
                record['key'] = key
                record.pop('oauthClient', None)
                record.pop('oauth', None)
            if old and record == old:
                return self.public(old)
            connection_fields = ('adminEmail', 'proxyId', 'enabled', 'authType', 'key', 'oauthClient')
            changed = not old or any(record.get(k) != old.get(k) for k in connection_fields)
            record['revision'] = (old or {}).get('revision', 0) + 1
            record['status'] = 'unchecked' if changed else old.get('status', 'unchecked')
            if old and any(record.get(k) != old.get(k) for k in ('adminEmail', 'authType', 'key', 'oauthClient')):
                record.pop('lastAuthorization', None)
            self._commit(wid, record)
            return self.public(record)

    def _ready(self, wid):
        record = self.entry(wid)
        if not record['enabled']:
            raise self.error(409, '此企业连接已停用。')
        if record.get('status') != 'connected' or not record.get('customerId'):
            raise self.error(409, '请先完成管理员授权或服务账号配置，并检测企业连接。')
        if self._busy(wid):
            raise self.error(409, '此企业已有任务执行中。')
        return record

    def _proxy(self, record):
        if record.get('proxyId'):
            return effective_entry(self.worker.catalog.entry(record['proxyId']), 'ws' + record['id'])
        return {}

    @contextmanager
    def _client(self, record, proxy):
        def refreshed(tokens):
            with self.worker.lock:
                current = self.entry(record['id'])
                if self.closed or current['revision'] != record['revision']:
                    raise DirectoryError('企业连接已变更，请重新检测。')
                self._commit(record['id'], dict(current, oauth=tokens))
        context = (connect(record, proxy, refreshed) if self.client_factory is connect
                   else self.client_factory(record, proxy))
        with context as client:
            yield client

    def oauth_start(self, wid):
        with self.worker.lock:
            record = self.entry(wid)
            if self.closed or not record['enabled'] or self._busy(wid):
                raise self.error(409, '企业已停用、正在授权或已有任务执行中。')
            if record.get('authType') != 'oauth':
                raise self.error(400, '请先选择管理员 OAuth 并导入桌面客户端 JSON。')
            if sum(a.state in ACTIVE for a in self.oauth_attempts.values()) >= 4:
                raise self.error(409, '最多同时等待 4 个企业授权。')
            snapshot, proxy = copy.deepcopy(record), self._proxy(record)

            def complete(attempt, tokens):
                candidate = dict(snapshot, oauth=tokens)
                with self.worker.lock:
                    if self.closed or attempt.state not in ACTIVE:
                        return
                with self.client_factory(candidate, proxy) as client:
                    customer = self._identity(client, candidate)
                    domains = sorted({d['domainName'].lower() for d in client.domains(customer) if d.get('verified') and d.get('domainName')})
                    if not domains:
                        raise DirectoryError('企业没有可用的已验证域名。')
                with self.worker.lock:
                    current = self.entry(wid)
                    with attempt.lock:
                        if self.closed or attempt.state not in ACTIVE or time.monotonic() > attempt.expires:
                            return
                        if current['revision'] != snapshot['revision'] or self._proxy(current) != proxy:
                            raise DirectoryError('企业配置或代理已变化，请重新授权。')
                        if any(r['id'] != wid and r.get('customerId') == customer for r in self.records.values()):
                            raise DirectoryError('此 Google 企业已有连接，请使用原连接，不能重复绑定。')
                        self._commit(wid, dict(current, oauth=tokens, customerId=customer, domains=domains, status='connected', checkedAt=now()))
                        attempt.finish('completed', '管理员授权成功，已核实企业与域名。现在可以同步员工。')

            self.oauth_attempts = {k: a for k, a in self.oauth_attempts.items() if a.state in ACTIVE or k in list(self.oauth_attempts)[-20:]}
            attempt = OAuthAttempt(snapshot, proxy, self.oauth_exchange, complete)
            self.oauth_attempts[attempt.id] = attempt
            attempt.start()
            return dict(attempt.snapshot(), authorizationUrl=attempt.authorization_url)

    def oauth_status(self, attempt_id):
        with self.worker.lock:
            attempt = self.oauth_attempts.get(attempt_id)
            if not attempt:
                raise self.error(404, '授权会话已结束或程序已重启，请重新授权。')
            result = attempt.snapshot()
            if result['state'] not in ACTIVE:
                record = self.entry(attempt.workspace_id)
                saved = dict(result, expiresIn=0)
                if record.get('lastAuthorization') != saved:
                    try:
                        self._commit(attempt.workspace_id, dict(record, lastAuthorization=saved))
                    except self.error:
                        result['message'] += ' 最近授权结果未写入磁盘，请检查本机存储。'
            return result

    def oauth_cancel(self, attempt_id):
        with self.worker.lock:
            self.oauth_status(attempt_id)
            self.oauth_attempts[attempt_id].finish('cancelled', '已取消本次授权，原有授权保留。')
            return self.oauth_status(attempt_id)

    def preview(self, wid, payload):
        with self.worker.lock:
            record = self._ready(wid)
            operation = payload.get('operation')
            if operation not in ('create', 'update', 'reset', 'suspend', 'resume', 'delete'):
                raise self.error(400, '不支持的企业操作。')
            if operation == 'create':
                rows = payload.get('rows')
                if payload.get('csv'):
                    try:
                        rows = list(csv.DictReader(io.StringIO(payload['csv'].lstrip('\ufeff'))))
                    except Exception:
                        raise self.error(400, 'CSV 格式错误。') from None
                if rows is None:
                    count, prefix, domain = payload.get('count', 1), payload.get('prefix', 'employee'), payload.get('domain', '')
                    style = payload.get('addressStyle', 'sequence')
                    if style not in ('human', 'sequence'):
                        raise self.error(400, '请选择人名加四位数字或前缀加递增序号。')
                    if type(count) is not int or not 1 <= count <= 200:
                        raise self.error(400, '每批应创建 1–200 人。')
                    if not isinstance(domain, str) or domain.lower().strip() not in record.get('domains', []):
                        raise self.error(400, '请选择此企业的已验证域名。')
                    domain = domain.lower().strip()
                    if style == 'human':
                        occupied = {u['email'].casefold() for u in record.get('users', [])}
                        occupied.update(a['email'].casefold() for a in self.worker.login_accounts.entries.values())
                        occupied.update(r['email'].casefold() for draft in self.drafts.values()
                                        if draft['expires'] > time.monotonic() and draft['operation'] == 'create'
                                        for r in draft['rows'])
                        rows = []
                        try:
                            for _ in range(count):
                                email, first, last = employee_identity(domain, occupied)
                                rows.append({'email': email, 'firstName': first, 'lastName': last,
                                             'password': payload.get('password', ''), 'orgUnitPath': payload.get('orgUnitPath', '/')})
                        except ValueError as exc:
                            raise self.error(409, str(exc)) from None
                    else:
                        start = payload.get('start', 1)
                        if type(start) is not int or not 1 <= start <= 999999:
                            raise self.error(400, '起始序号为 1–999999。')
                        if not isinstance(prefix, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,35}', prefix):
                            raise self.error(400, '账号前缀请使用字母、数字、点、下划线或连字符。')
                        rows = [{'email': f'{prefix}{start+i:03d}@{domain}', 'firstName': f'{prefix}{start+i:03d}',
                                 'lastName': payload.get('lastName', 'User'), 'password': payload.get('password', ''),
                                 'orgUnitPath': payload.get('orgUnitPath', '/')} for i in range(count)]
            else:
                ids = payload.get('userIds')
                if not isinstance(ids, list) or not ids or len(ids) > 200 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
                    raise self.error(400, '请选择 1–200 个不同员工。')
                users = {u['id']: u for u in record.get('users', [])}
                if any(uid not in users for uid in ids):
                    raise self.error(409, '员工目录已变化，请同步后重新选择。')
                rows = [dict(users[uid], password=payload.get('password', ''), patch=payload.get('patch', {})) for uid in ids]
            if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
                raise self.error(400, '每批应包含 1–200 行员工数据。')
            force_change = payload.get('changePasswordAtNextLogin', False)
            if not isinstance(force_change, bool):
                raise self.error(400, '首次改密选项格式错误。')
            prepared, shown, seen = [], [], set()
            for index, row in enumerate(rows, 1):
                try:
                    if not isinstance(row, dict):
                        raise ValueError('员工行必须为对象。')
                    email = row.get('email', '')
                    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@[^\s@]+\.[^\s@]+', email):
                        raise ValueError('邮箱格式错误。')
                    email = email.lower()
                    if email.split('@')[1] not in record.get('domains', []):
                        raise ValueError('邮箱不属于此企业已验证域名。')
                    if email in seen:
                        raise ValueError('批次内邮箱重复。')
                    seen.add(email)
                    if operation == 'create' and any(u['email'].lower() == email for u in record.get('users', [])):
                        raise ValueError('企业目录已有此邮箱。')
                    body = {}
                    if operation in ('create', 'reset'):
                        password = row.get('password', '')
                        if not isinstance(password, str) or not 8 <= len(password) <= 100 or any(ord(c) < 32 or ord(c) > 126 for c in password):
                            raise ValueError('密码需要 8–100 位可打印 ASCII 字符。')
                        body.update(password=password, changePasswordAtNextLogin=force_change)
                    if operation == 'create':
                        first, last, ou = row.get('firstName', ''), row.get('lastName', ''), row.get('orgUnitPath', '/')
                        if any(not isinstance(v, str) or not v.strip() or len(v) > 60 for v in (first, last)):
                            raise ValueError('姓名必须填写，名和姓分别不超过 60 字。')
                        if not isinstance(ou, str) or not ou.startswith('/') or len(ou) > 512:
                            raise ValueError('组织单位路径应以 / 开头。')
                        body.update(primaryEmail=email, name={'givenName': first.strip(), 'familyName': last.strip()}, orgUnitPath=ou)
                        recovery = row.get('recoveryEmail', '')
                        if recovery:
                            if not isinstance(recovery, str) or len(recovery) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', recovery):
                                raise ValueError('辅助邮箱格式错误。')
                            body['recoveryEmail'] = recovery
                        if row.get('department') or row.get('title'):
                            if any(not isinstance(row.get(k, ''), str) or len(row.get(k, '')) > 60 for k in ('department', 'title')):
                                raise ValueError('部门与职位不超过 60 字。')
                            body['organizations'] = [{'department': row.get('department', ''), 'title': row.get('title', ''), 'primary': True}]
                    elif operation == 'update':
                        patch = row['patch']
                        if not isinstance(patch, dict) or set(patch) - {'firstName', 'lastName', 'department', 'title', 'recoveryEmail', 'orgUnitPath'} or not patch:
                            raise ValueError('请选择要修改的资料字段。')
                        for k, v in patch.items():
                            if not isinstance(v, str) or len(v) > (512 if k == 'orgUnitPath' else 254 if k == 'recoveryEmail' else 60) or any(ord(c) < 32 for c in v):
                                raise ValueError('资料字段长度或格式错误。')
                        if any(k in patch for k in ('firstName', 'lastName')):
                            first, last = patch.get('firstName', row['firstName']), patch.get('lastName', row['lastName'])
                            if not first.strip() or not last.strip():
                                raise ValueError('姓名不能为空。')
                            body['name'] = {'givenName': first.strip(), 'familyName': last.strip()}
                        if 'orgUnitPath' in patch:
                            if not patch['orgUnitPath'].startswith('/'):
                                raise ValueError('组织单位路径应以 / 开头。')
                            body['orgUnitPath'] = patch['orgUnitPath']
                        if 'recoveryEmail' in patch:
                            if patch['recoveryEmail'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', patch['recoveryEmail']):
                                raise ValueError('辅助邮箱格式错误。')
                            body['recoveryEmail'] = patch['recoveryEmail']
                        if 'department' in patch or 'title' in patch:
                            body['organizations'] = [dict(row.get('organization') or {}, **{k: patch[k] for k in ('department', 'title') if k in patch}, primary=True)]
                    elif operation in ('suspend', 'resume'):
                        if email == record['adminEmail'].lower():
                            raise ValueError('不能在此批次暂停或恢复连接管理员。')
                        body['suspended'] = operation == 'suspend'
                    elif operation == 'delete':
                        if email == record['adminEmail'].lower() or row.get('isAdmin') or row.get('isDelegatedAdmin'):
                            raise ValueError('不能删除连接管理员或具有管理权限的员工。')
                    local = next((a for a in self.worker.login_accounts.entries.values() if a['email'].lower() == email), None)
                    if local and local.get('workspaceId') not in (None, '', wid):
                        raise ValueError('此邮箱已关联其他企业，请先核实本机记录。')
                    if local and self.worker.login_accounts._active(local['id']) and operation != 'create':
                        raise ValueError('此员工正在自动登录，请先停止登录任务。')
                    if operation == 'create' and local:
                        raise ValueError('本机账号库已有此邮箱，请核实后处理。')
                    prepared.append({'email': email, 'id': row.get('id'), 'body': body})
                    shown.append({'row': index, 'email': email, 'valid': True,
                                  'message': '将删除 Google 员工账号；本机记录和浏览器保留，停止自动登录。' if operation == 'delete' else '可执行'})
                    if operation == 'create':
                        shown[-1].update(firstName=body['name']['givenName'], lastName=body['name']['familyName'])
                except ValueError as exc:
                    shown.append({'row': index, 'email': row.get('email', '') if isinstance(row, dict) else '', 'valid': False, 'message': str(exc)})
            self.drafts = {k: v for k, v in self.drafts.items() if v['expires'] > time.monotonic()}
            if len(self.drafts) >= 30:
                raise self.error(409, '预览数量过多，请稍后重试。')
            draft_id = secrets.token_urlsafe(24)
            self.drafts[draft_id] = {'workspaceId': wid, 'revision': record['revision'], 'operation': operation, 'rows': prepared,
                                     'expires': time.monotonic() + 600, 'proxy': self._proxy(record),
                                     'directory': self._directory_signature(record)}
            return {'draftId': draft_id, 'workspaceId': wid, 'workspaceName': record['name'], 'operation': operation,
                    'rows': shown, 'valid': len(prepared), 'invalid': len(shown) - len(prepared), 'expiresIn': 600,
                    'changePasswordAtNextLogin': force_change}

    @staticmethod
    def _directory_signature(record):
        users = sorted(record.get('users', []), key=lambda u: u['id'])
        return hashlib.sha256(json.dumps(users, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def start(self, wid, operation, payload=None):
        with self.worker.lock:
            if self.closed:
                raise self.error(409, '程序正在退出。')
            if len([j for j in self.jobs.values() if j['state'] in ('queued', 'running', 'cancelling')]) >= 4:
                raise self.error(409, '最多同时执行 4 个企业任务。')
            if operation == 'check':
                record = self.entry(wid)
                if not record['enabled'] or self._busy(wid):
                    raise self.error(409, '企业已停用或已有任务执行中。')
                if record.get('authType') == 'oauth' and not (record.get('oauth') or {}).get('refresh_token'):
                    raise self.error(409, '请先点击 Google 管理员授权，授权成功后再检测连接。')
            else:
                record = self._ready(wid)
            rows = []
            if operation == 'execute':
                draft_id = (payload or {}).get('draftId')
                draft = self.drafts.get(draft_id) if isinstance(draft_id, str) else None
                if not draft or draft['workspaceId'] != wid or draft['revision'] != record['revision'] or draft['expires'] < time.monotonic() or draft['proxy'] != self._proxy(record):
                    raise self.error(409, '预览已失效或不属于此企业，请重新预览。')
                if draft['directory'] != self._directory_signature(record):
                    raise self.error(409, '员工目录已更新，请返回修改并重新预览，避免使用过期资料。')
                if not draft['rows']:
                    raise self.error(400, '没有可执行的员工行。')
                if draft['operation'] == 'delete' and (payload or {}).get('confirmWorkspaceName') != record['name']:
                    raise self.error(400, '删除员工前，请输入完整企业名称确认。')
                for row in draft['rows']:
                    local = next((a for a in self.worker.login_accounts.entries.values() if a['email'].lower() == row['email']), None)
                    if local and self.worker.login_accounts._active(local['id']):
                        raise self.error(409, '选中员工正在自动登录，请停止后重新预览。')
                    if draft['operation'] == 'create' and local:
                        raise self.error(409, '本机已存在待创建邮箱，请重新预览。')
                operation, rows = draft['operation'], draft['rows']
                del self.drafts[draft_id]
            elif operation not in ('check', 'sync'):
                raise self.error(400, '不支持的任务。')
            job_id = uuid.uuid4().hex[:12]
            job = {'id': job_id, 'workspaceId': wid, 'workspaceName': record['name'], 'operation': operation, 'state': 'queued',
                   'total': len(rows) or 1, 'completed': 0, 'results': [], 'startedAt': now(), 'cancel': threading.Event(),
                   'phase': '等待连接 Google', 'proxyId': record.get('proxyId', '')}
            job['reservedEmails'] = {r['email'] for r in rows} if rows else {u['email'] for u in record.get('users', [])}
            self.jobs = {k: v for k, v in self.jobs.items() if v['state'] in ('queued', 'running', 'cancelling') or k in list(self.jobs)[-40:]}
            self.jobs[job_id] = job
            try:
                self._commit(wid, dict(self.entry(wid), lastJob=self.snapshot(job_id)))
            except Exception:
                del self.jobs[job_id]
                raise
            threading.Thread(target=self._run, args=(job, copy.deepcopy(record), rows, self._proxy(record)), daemon=True,
                             name='workspace-' + job_id).start()
            return self.snapshot(job_id)

    def snapshot(self, job_id):
        with self.worker.lock:
            if job_id not in self.jobs:
                raise self.error(404, '企业任务不存在，重启后请查看企业最近结果。')
            return copy.deepcopy({k: v for k, v in self.jobs[job_id].items() if k not in ('cancel', 'reservedEmails')})

    def cancel(self, job_id):
        with self.worker.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise self.error(404, '企业任务不存在。')
            if job['state'] in ('queued', 'running', 'cancelling'):
                job['cancel'].set()
                job['state'] = 'cancelling'
            return self.snapshot(job_id)

    @staticmethod
    def _user(raw, customer):
        if raw.get('customerId') != customer or not raw.get('id') or not raw.get('primaryEmail'):
            raise DirectoryError('用户所属企业不匹配，已停止同步或写入本机。')
        organization = next((o for o in raw.get('organizations', []) if o.get('primary')), (raw.get('organizations') or [{}])[0])
        return {'id': str(raw['id']), 'email': raw['primaryEmail'].lower(), 'firstName': raw.get('name', {}).get('givenName', ''),
                'lastName': raw.get('name', {}).get('familyName', ''), 'name': raw.get('name', {}).get('fullName') or
                ' '.join(filter(None, (raw.get('name', {}).get('givenName'), raw.get('name', {}).get('familyName')))),
                'suspended': raw.get('suspended', False), 'orgUnitPath': raw.get('orgUnitPath', '/'),
                'isAdmin': bool(raw.get('isAdmin')), 'isDelegatedAdmin': bool(raw.get('isDelegatedAdmin')),
                'department': organization.get('department', ''), 'title': organization.get('title', ''),
                'organization': {k: v for k, v in organization.items() if k in ('name', 'department', 'title', 'description', 'domain', 'type', 'customType', 'costCenter', 'location', 'symbol')},
                'recoveryEmail': raw.get('recoveryEmail', ''), 'changePasswordAtNextLogin': raw.get('changePasswordAtNextLogin', False)}

    def _local(self, record, user, password=None, records=None):
        inventory = self.worker.login_accounts
        entries = inventory.entries if records is None else records
        existing = next((a for a in entries.values() if a.get('workspaceId') == record['id'] and a.get('workspaceUserId') == user['id']), None)
        existing = existing or next((a for a in entries.values() if a['email'].lower() == user['email']), None)
        if existing and (existing.get('workspaceId') not in (None, '', record['id']) or inventory._active(existing['id'])):
            raise self.error(409, '本机账号关联其他企业或正在自动登录，请稍后核实保存。')
        if existing and existing['email'].lower() != user['email'] and inventory.public(existing)['bindings']:
            raise self.error(409, '企业邮箱已更名，本机账号仍绑定浏览器，请先解除绑定后重新同步。')
        target = copy.deepcopy(existing) if existing else {'id': uuid.uuid4().hex[:12], 'createdAt': now(), 'password': password or '',
                                                         'name': user['name'] or user['email'], 'group': record['name'], 'notes': ''}
        target.update(email=user['email'], workspaceId=record['id'], workspaceName=record['name'], workspaceUserId=user['id'],
                      workspaceSuspended=user['suspended'], workspaceMissing=False, updatedAt=now())
        if password is not None:
            target['password'] = password
        if records is None:
            inventory._write(dict(inventory.entries, **{target['id']: target}))
        else:
            records[target['id']] = target
        return target['id']

    def _identity(self, client, record):
        identity = client.identity(record['adminEmail'])
        customer = identity.get('customerId')
        if not isinstance(customer, str) or not customer:
            raise DirectoryError('无法识别企业 Customer ID，请核实管理员权限。')
        if record.get('customerId') and record['customerId'] != customer:
            raise DirectoryError('此连接已绑定另一企业，不能更换为不同企业；请新增企业连接。')
        return customer

    def _run(self, job, record, rows, proxy):
        failure = False
        try:
            with self.worker.lock:
                job['state'] = 'running'
                job['phase'] = '正在验证管理员身份与企业归属'
            with self._client(record, proxy) as client:
                if job['cancel'].is_set():
                    return
                customer = self._identity(client, record)
                operation = job['operation']
                if operation == 'check':
                    with self.worker.lock:
                        job['phase'] = '正在读取企业域名'
                        if any(r['id'] != record['id'] and r.get('customerId') == customer for r in self.records.values()):
                            raise DirectoryError('此 Google 企业已存在于其他连接，请使用原连接管理其域名和员工。')
                    domains = sorted({d['domainName'].lower() for d in client.domains(customer) if d.get('verified') and d.get('domainName')})
                    if not domains:
                        raise DirectoryError('企业没有可用的已验证域名。')
                    with self.worker.lock:
                        if not job['cancel'].is_set() and not self.closed:
                            current = dict(self.entry(record['id']), customerId=customer, domains=domains, status='connected', checkedAt=now())
                            self._commit(record['id'], current)
                            job['results'].append({'success': True, 'message': '连接成功，已核实企业与域名。'})
                            job['completed'] = 1
                elif operation == 'sync':
                    with self.worker.lock:
                        job['phase'] = '正在分页读取员工目录'
                    raw = client.users(customer, job['cancel'])
                    if raw is None or job['cancel'].is_set():
                        return
                    users = [self._user(u, customer) for u in raw]
                    with self.worker.lock:
                        if self.closed or job['cancel'].is_set():
                            return
                        self._commit(record['id'], dict(self.entry(record['id']), users=users, syncedAt=now()))
                        job['total'] = max(1, len(users))
                        local_records = copy.deepcopy(self.worker.login_accounts.entries)
                        for user in users:
                            try:
                                aid = self._local(record, user, records=local_records)
                                job['results'].append({'email': user['email'], 'success': True, 'accountId': aid, 'message': '目录与本机资料已同步；未返回密码。'})
                            except Exception:
                                job['results'].append({'email': user['email'], 'success': False, 'message': '企业目录已更新，本机账号未保存，请核实绑定或登录任务后重新同步。'})
                        current_ids = {u['id'] for u in users}
                        for account in local_records.values():
                            if account.get('workspaceId') == record['id'] and account.get('workspaceUserId') not in current_ids:
                                account['workspaceMissing'] = True
                        try:
                            self.worker.login_accounts._write(local_records)
                        except Exception:
                            for result in job['results']:
                                result.update(success=False, message='企业目录已更新，但本机账号同步保存失败，请重新同步。')
                                result.pop('accountId', None)
                        if not users:
                            job['results'].append({'success': True, 'message': '企业目录为空。'})
                        job['completed'] = job['total']
                else:
                    with self.worker.lock:
                        job['phase'] = '正在执行已确认的员工操作'
                    for row in rows:
                        if job['cancel'].is_set() or self.closed:
                            break
                        result = {'email': row['email'], 'success': False}
                        try:
                            # Recheck active local login immediately before each remote mutation.
                            with self.worker.lock:
                                local = next((a for a in self.worker.login_accounts.entries.values() if a['email'].lower() == row['email']), None)
                                if local and self.worker.login_accounts._active(local['id']):
                                    raise DirectoryError('此员工开始了自动登录，本行未执行。')
                            if operation == 'delete':
                                with self.worker.lock:
                                    job['phase'] = '正在核实并删除员工：' + row['email']
                                # Re-read identity and roles before each deletion; a
                                # role change after preview must never delete an admin.
                                current_user = self._user(client.user(row['id']), customer)
                                if (current_user['id'] != row['id'] or current_user['email'] != row['email'] or
                                        current_user['email'] == record['adminEmail'].lower() or
                                        current_user['isAdmin'] or current_user['isDelegatedAdmin']):
                                    raise DirectoryError('员工身份或管理权限已变化，本行未删除，请同步后核实。')
                                client.delete(row['id'])
                            else:
                                raw = client.create(row['body']) if operation == 'create' else client.update(row['id'], row['body'])
                            result['cloudApplied'] = True
                            with self.worker.lock:
                                current = self.entry(record['id'])
                                cached = {u['id']: u for u in current.get('users', [])}
                                if operation == 'delete':
                                    cached.pop(row['id'], None)
                                    self._commit(record['id'], dict(current, users=list(cached.values())))
                                    inventory = self.worker.login_accounts
                                    accounts = copy.deepcopy(inventory.entries)
                                    for account in accounts.values():
                                        if account.get('workspaceId') == record['id'] and account.get('workspaceUserId') == row['id']:
                                            account.update(workspaceMissing=True, updatedAt=now())
                                    try:
                                        inventory._write(accounts)
                                    except Exception:
                                        # Keep login disabled in memory even if the
                                        # disk write fails; the cached directory also
                                        # protects the account after a restart.
                                        inventory.entries = accounts
                                        raise
                                    result.update(success=True, message='Google 员工已删除；本机账号、密码和浏览器保留，自动登录已停用。')
                                else:
                                    user = self._user(raw, customer)
                                    cached[user['id']] = user
                                    self._commit(record['id'], dict(current, users=list(cached.values())))
                                    aid = self._local(record, user, row['body'].get('password'))
                                    result.update(success=True, accountId=aid, message='Google 操作成功，账号资料已保存到本机。')
                        except DirectoryError as exc:
                            result.update(message=str(exc), uncertain=exc.uncertain)
                        except Exception:
                            result['message'] = ('Google 已删除，但本机保存未完成；请先同步核实，勿重复提交删除。' if operation == 'delete' else
                                                 'Google 已处理，但本机保存未完成；请先同步核实，再补录密码，勿重复创建。') if result.get('cloudApplied') else '本行未完成，请同步核实后重试。'
                        with self.worker.lock:
                            job['results'].append(result)
                            job['completed'] += 1
                            try:
                                self._commit(record['id'], dict(self.entry(record['id']), lastJob=self.snapshot(job['id'])))
                            except Exception:
                                job['cancel'].set()
        except DirectoryError as exc:
            failure = True
            with self.worker.lock:
                job['results'].append({'success': False, 'message': str(exc), 'uncertain': exc.uncertain})
        except Exception:
            failure = True
            with self.worker.lock:
                job['results'].append({'success': False, 'message': '企业任务未完成，请检查网络、管理配置或本机存储。'})
        finally:
            rows.clear()  # Drop initial/reset passwords as soon as the job finishes.
            with self.worker.lock:
                job['state'] = 'failed' if failure else 'cancelled' if job['cancel'].is_set() or self.closed else 'completed'
                job['finishedAt'] = now()
                if job['operation'] == 'check' and failure:
                    self.records[record['id']] = dict(self.entry(record['id']), status='failed')
                try:
                    self._commit(record['id'], dict(self.entry(record['id']), lastJob=self.snapshot(job['id'])))
                except Exception:
                    job['results'].append({'success': False, 'message': '最近任务结果未写入磁盘，请保留当前结果。'})

    def close(self):
        self.closed = True
        self.drafts.clear()
        for job in list(self.jobs.values()):
            job['cancel'].set()
        for attempt in list(self.oauth_attempts.values()):
            attempt.finish('cancelled', '程序已退出，请重新授权。')

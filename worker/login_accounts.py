"""Independent Google account inventory; public records never contain passwords."""
import copy
import json
import re
import uuid

from account_management import now


class LoginAccounts:
    def __init__(self, worker, error):
        self.worker, self.error = worker, error
        self.path = worker.data_dir / 'login-accounts.json'
        self.entries = {}
        if self.path.exists():
            records = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(records, list):
                raise OSError('Invalid login account inventory')
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get('id'), str) or record['id'] in self.entries:
                    raise OSError('Invalid login account record')
                self.entries[record['id']] = record

    def _write(self, records):
        temp = self.path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(list(records.values()), ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
        self.entries = records

    def entry(self, account_id):
        if not isinstance(account_id, str) or account_id not in self.entries:
            raise self.error(404, '账号不存在，请重新选择。')
        return self.entries[account_id]

    def public(self, record):
        result = {k: record.get(k, '') for k in ('id', 'name', 'email', 'group', 'notes', 'createdAt', 'updatedAt', 'workspaceId', 'workspaceName', 'workspaceUserId')}
        result['workspaceSuspended'] = bool(record.get('workspaceSuspended'))
        result['workspaceMissing'] = bool(record.get('workspaceMissing'))
        result['passwordSet'] = bool(record.get('password'))
        result['bindings'] = [{'id': p['id'], 'name': p['name']} for p in self.worker.profiles.values()
                              if p.get('loginAccountId') == record['id']]
        return result

    def list(self):
        with self.worker.lock:
            # Explicit account management view includes the user-visible password.
            # Browser/profile summaries continue to use public() without secrets.
            return {'accounts': [dict(self.public(r), password=r.get('password', '')) for r in self.entries.values()]}

    def bound(self, pid):
        account_id = self.worker.profile(pid).get('loginAccountId', '')
        return self.entry(account_id) if account_id else None

    def _active(self, account_id):
        return any(j.get('loginAccountId') == account_id and j['state'] in self.worker.google_login.ACTIVE
                   for j in self.worker.google_login.jobs.values())

    def _enterprise_busy(self, record):
        workspaces = getattr(self.worker, 'workspaces', None)
        return bool(workspaces and ((record.get('workspaceId') and workspaces._busy(record['workspaceId'])) or workspaces.account_busy(record['email'])))

    def upsert(self, payload):
        with self.worker.lock:
            if any(k not in ('id', 'name', 'email', 'password', 'group', 'notes') for k in payload):
                raise self.error(400, '账号表单包含不支持的字段。')
            old = self.entry(payload['id']) if 'id' in payload else None
            if old and self._enterprise_busy(old):
                raise self.error(409, '此账号所属企业正在执行任务，请等待完成后编辑。')
            record = copy.deepcopy(old) if old else {'id': uuid.uuid4().hex[:12], 'createdAt': now()}
            if old and self._active(old['id']):
                raise self.error(409, '请先停止此账号正在执行的登录任务。')
            for key, limit, label in (('name', 120, '账号名称'), ('email', 254, '邮箱'), ('group', 60, '分组'), ('notes', 1000, '备注')):
                value = payload.get(key, record.get(key, ''))
                if not isinstance(value, str) or len(value) > limit or '\x00' in value:
                    raise self.error(400, f'{label}格式或长度不正确。')
                record[key] = value.strip()
            email = record['email']
            if not re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,120}\.[^\s@]{2,30}', email):
                raise self.error(400, '请填写完整的 Google 账号邮箱。')
            if self._enterprise_busy(dict(old or {}, email=email)):
                raise self.error(409, '此邮箱正在执行企业任务，请等待任务结束再编辑账号。')
            if any(r['email'].casefold() == email.casefold() and r['id'] != record['id'] for r in self.entries.values()):
                raise self.error(409, '此邮箱已在账号列表中。')
            if not record['name']:
                record['name'] = email
            password = payload.get('password', '')
            if not isinstance(password, str) or len(password) > 4096 or '\x00' in password:
                raise self.error(400, '密码格式或长度不正确。')
            changed_identity = old and old['email'].casefold() != email.casefold()
            if changed_identity and old.get('workspaceId'):
                raise self.error(409, '企业员工邮箱由企业目录维护，不能在本机单独更名。')
            if changed_identity and self.public(old)['bindings']:
                raise self.error(409, '修改邮箱前请先解除浏览器绑定，避免账号身份混淆。')
            if (not old or changed_identity) and not password:
                raise self.error(400, '新账号或更换邮箱时需要填写密码。')
            if password:
                record['password'] = password
            record['updatedAt'] = now()
            records = dict(self.entries, **{record['id']: record})
            try:
                self._write(records)
            except Exception:
                raise self.error(500, '账号保存失败，原数据保持不变。') from None
            return self.public(record)

    def credentials(self, account_id):
        record = self.entry(account_id)
        if not isinstance(record.get('password'), str) or not record['password']:
            raise self.error(409, '此账号未保存有效密码，请重新填写。')
        if record.get('workspaceSuspended'):
            raise self.error(409, '此企业员工已暂停，请先在企业目录恢复并同步状态。')
        if record.get('workspaceMissing'):
            raise self.error(409, '最近同步的企业目录中已无此员工，请先核实企业账号。')
        if record.get('workspaceId') and record.get('workspaceUserId'):
            workspace = self.worker.workspaces.records.get(record['workspaceId'])
            if workspace and not any(u['id'] == record['workspaceUserId'] for u in workspace.get('users', [])):
                raise self.error(409, '企业目录中已无此员工，请先同步核实企业账号。')
        if self._enterprise_busy(record):
            raise self.error(409, '所属企业任务尚未完成，请稍后执行自动登录。')
        return {'email': record['email'], 'password': record['password']}

    def validate_binding(self, pid, account_id):
        if not isinstance(account_id, str):
            raise self.error(400, '绑定账号格式不正确。')
        if account_id:
            self.entry(account_id)
            if any(p['id'] != pid and p.get('loginAccountId') == account_id for p in self.worker.profiles.values()):
                raise self.error(409, '此账号已绑定其他浏览器，请先解除原绑定。')

    def bind(self, pid, payload):
        with self.worker.lock:
            if set(payload) != {'accountId'}:
                raise self.error(400, '请选择需要绑定的账号。')
            old = self.worker.profile(pid)
            account_id = payload['accountId']
            self.validate_binding(pid, account_id)
            if old.get('loginAccountId', '') == account_id:
                return self.worker.state(pid)
            if self.worker.state(pid)['state'] in ('running', 'starting'):
                raise self.error(409, '请先关闭浏览器再修改账号绑定；关闭不会退出网站账号。')
            old_job = self.worker.google_login.jobs.get(pid)
            if old_job and old_job['state'] in self.worker.google_login.ACTIVE:
                raise self.error(409, '请先停止此浏览器的登录任务。')
            self.worker.profiles[pid] = dict(old, loginAccountId=account_id)
            try:
                self.worker.save()
            except Exception:
                self.worker.profiles[pid] = old
                raise self.error(500, '绑定保存失败。') from None
            return self.worker.state(pid)

    def delete(self, account_id):
        with self.worker.lock:
            record = self.entry(account_id)
            if self._enterprise_busy(record):
                raise self.error(409, '所属企业正在执行任务，请稍后删除本机记录。')
            if self.public(record)['bindings'] or self._active(account_id):
                raise self.error(409, '请先解除浏览器绑定并停止登录，再删除账号。')
            records = dict(self.entries)
            del records[account_id]
            try:
                self._write(records)
            except Exception:
                raise self.error(500, '删除失败，原数据保持不变。') from None
            return {'deleted': account_id}

    def migrate(self):
        """Import per-environment credentials without modifying their source."""
        with self.worker.lock:
            imported = skipped = 0
            for pid, profile in self.worker.profiles.items():
                try:
                    credential = self.worker.google_login.vault.load(pid)
                    if not credential:
                        continue
                    existing = next((r for r in self.entries.values() if r['email'].casefold() == credential['email'].casefold()), None)
                    if existing or profile.get('loginAccountId') or self.worker.state(pid)['state'] in ('running', 'starting'):
                        skipped += 1
                        continue
                    record = self.upsert({'email': credential['email'], 'password': credential['password'],
                                          'name': profile['name'], 'group': profile.get('accountGroup', ''),
                                          'notes': profile.get('accountNotes', '')})
                    self.bind(pid, {'accountId': record['id']})
                    imported += 1
                except Exception:
                    skipped += 1
            return {'imported': imported, 'skipped': skipped}

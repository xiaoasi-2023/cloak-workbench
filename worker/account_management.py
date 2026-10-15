"""Bounded account inventory operations and read-only health observations."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import csv
import hashlib
from datetime import datetime, timezone
from dataclasses import asdict
import io
import json
import re
import secrets
import threading
import uuid

from proxy_support import parse_proxy
from proxy_diagnostics import diagnose_proxy


def now():
    return datetime.now(timezone.utc).isoformat()


class AccountManagement:
    MAX_ROWS = 200
    FIELDS = {'id', 'name', 'accountEmail', 'accountGroup', 'accountNotes', 'proxyId'}
    REGIONS = {
        'CN': ('zh-CN', 'Asia/Shanghai'), 'HK': ('zh-HK', 'Asia/Hong_Kong'), 'TW': ('zh-TW', 'Asia/Taipei'),
        'JP': ('ja-JP', 'Asia/Tokyo'), 'KR': ('ko-KR', 'Asia/Seoul'), 'SG': ('en-SG', 'Asia/Singapore'),
        'US': ('en-US', 'America/New_York'), 'CA': ('en-CA', 'America/Toronto'), 'GB': ('en-GB', 'Europe/London'),
        'DE': ('de-DE', 'Europe/Berlin'), 'FR': ('fr-FR', 'Europe/Paris'), 'ES': ('es-ES', 'Europe/Madrid'),
        'IT': ('it-IT', 'Europe/Rome'), 'NL': ('nl-NL', 'Europe/Amsterdam'), 'SE': ('sv-SE', 'Europe/Stockholm'),
        'PL': ('pl-PL', 'Europe/Warsaw'), 'RU': ('ru-RU', 'Europe/Moscow'), 'AU': ('en-AU', 'Australia/Sydney'),
        'NZ': ('en-NZ', 'Pacific/Auckland'), 'IN': ('en-IN', 'Asia/Kolkata'), 'ID': ('id-ID', 'Asia/Jakarta'),
        'TH': ('th-TH', 'Asia/Bangkok'), 'VN': ('vi-VN', 'Asia/Ho_Chi_Minh'), 'MY': ('ms-MY', 'Asia/Kuala_Lumpur'),
        'PH': ('en-PH', 'Asia/Manila'), 'AE': ('ar-AE', 'Asia/Dubai'), 'TR': ('tr-TR', 'Europe/Istanbul'),
        'BR': ('pt-BR', 'America/Sao_Paulo'), 'MX': ('es-MX', 'America/Mexico_City'),
        'AR': ('es-AR', 'America/Argentina/Buenos_Aires'), 'ZA': ('en-ZA', 'Africa/Johannesburg')}

    def __init__(self, worker, error, validate):
        self.worker, self.error, self.validate = worker, error, validate
        self.path = worker.data_dir / 'account-observations.json'
        self.observations = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(self.observations, dict):
            raise ValueError('Invalid saved account observations')
        self.jobs = {}
        self.generations = {}
        # Runtime identities are intentionally never persisted: a saved page
        # observation is useful history, not proof about a newly opened browser.
        self.login_runtimes = {}
        self.closed = False
        for pid, record in self.observations.items():
            if pid in worker.profiles and isinstance(record, dict) and isinstance(record.get('accountCheck'), dict):
                worker.account_checks[pid] = copy.deepcopy(record['accountCheck'])

    def persist(self, pid, key, value):
        return self.persist_many(pid, {key: value})

    def persist_many(self, pid, values):
        with self.worker.lock:
            old = copy.deepcopy(self.observations)
            self.observations.setdefault(pid, {}).update(copy.deepcopy(values))
            try:
                temp = self.path.with_suffix('.json.tmp')
                temp.write_text(json.dumps(self.observations, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(self.path)
            except Exception:
                self.observations = old
                raise

    def invalidate_proxy(self, pid):
        """A changed route cannot inherit positive login or network evidence."""
        with self.worker.lock:
            self.generations[pid] = self.generations.get(pid, 0) + 1
            self.login_runtimes.pop(pid, None)
            result = {'status': 'unknown', 'reason': 'proxy_changed', 'source': 'dola_visible_page'}
            values = {'accountCheck': result, 'networkCheck': None}
            record = self.observations.get(pid, {})
            if not record.get('lastAccountCheck') and self.page_evidence(record.get('accountCheck')):
                values.update(lastAccountCheck=record['accountCheck'], lastAccountRoute=record.get('accountRoute'))
            self.persist_many(pid, values)
            self.worker.account_checks[pid] = result

    def route_key(self, config):
        return hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode('utf-8')).hexdigest()

    def import_region(self, row):
        row['regionMode'] = 'manual'
        if not isinstance(row.get('proxyId'), str) or not row.get('proxyId'):
            return 'not_checked'
        entry = self.worker.catalog.entry(row['proxyId'])
        check = entry.get('check') or {}
        if entry.get('sessionMode') == 'per_profile' or check.get('reachable') is not True:
            return 'not_checked'
        code = check.get('countryCode') or check.get('country')
        if not isinstance(code, str) or code.upper() not in self.REGIONS:
            return 'not_checked'
        code = code.upper()
        locale, fallback_zone = self.REGIONS[code]
        zone = check.get('timezone')
        if zone:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(zone)
            except (ValueError, TypeError, KeyError):
                zone = None
        if not zone and code in ('US', 'CA', 'AU', 'RU', 'BR', 'MX', 'ID'):
            return 'missing_timezone'
        row.update(locale=locale, timezone=zone or fallback_zone, regionMode='proxy')
        return 'matched_proxy'

    def record_check(self, pid, kind, result, config=None):
        config = config or parse_proxy(self.worker.effective_proxy(self.worker.profile(pid)))
        values = {kind + 'Check': result, kind + 'Route': self.route_key(config)}
        if kind == 'account':
            runtime = self.worker.runtime.get(pid)
            if runtime and self.worker.is_running(pid) and result.get('reason') not in ('browser_closed', 'session_changed', 'check_cancelled'):
                values.update(lastAccountCheck=result, lastAccountRoute=self.route_key(config))
                self.persist_many(pid, values)
                self.login_runtimes[pid] = (runtime, runtime['process'], result)
                return
            self.login_runtimes.pop(pid, None)
            # Upgrade older observations before a closed-browser check replaces
            # their latest-attempt field. Merely closing is not signing out.
            record = self.observations.get(pid, {})
            previous = record.get('accountCheck')
            if not record.get('lastAccountCheck') and self.page_evidence(previous):
                values.update(lastAccountCheck=previous, lastAccountRoute=record.get('accountRoute'))
        self.persist_many(pid, values)

    @staticmethod
    def page_evidence(result):
        return (isinstance(result, dict) and bool(result.get('checkedAt'))
                and result.get('reason') not in ('browser_closed', 'session_changed', 'check_cancelled', 'not_checked', 'proxy_changed'))

    def import_rows(self, payload):
        choices = sum(key in payload for key in ('rows', 'text', 'count'))
        if choices != 1:
            raise self.error(400, 'Provide exactly one of rows, text or count')
        if 'rows' in payload:
            rows = payload['rows']
        elif 'count' in payload:
            count, prefix = payload['count'], payload.get('prefix', 'Account')
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= self.MAX_ROWS:
                raise self.error(400, 'Create between 1 and 200 accounts')
            if not isinstance(prefix, str) or not prefix.strip() or len(prefix) > 100:
                raise self.error(400, 'Invalid account name prefix')
            rows = [{'name': f'{prefix.strip()} {i + 1:03d}'} for i in range(count)]
        else:
            text = payload['text']
            if not isinstance(text, str) or len(text.encode('utf-8')) > 60000:
                raise self.error(400, 'Import text must be smaller than 60 KiB')
            text = text.lstrip('\ufeff').strip()
            try:
                if text.startswith(('[', '{')):
                    rows = json.loads(text)
                    if isinstance(rows, dict):
                        rows = rows.get('accounts')
                else:
                    rows = list(csv.DictReader(io.StringIO(text)))
            except (ValueError, csv.Error):
                raise self.error(400, 'Expected valid CSV with a header or JSON account rows') from None
        if not isinstance(rows, list) or not 1 <= len(rows) <= self.MAX_ROWS:
            raise self.error(400, 'Import between 1 and 200 account rows')
        preview = payload.get('preview', True)
        if not isinstance(preview, bool):
            raise self.error(400, 'preview must be a boolean')
        with self.worker.lock:
            emails = {p.get('accountEmail', '').casefold() for p in self.worker.profiles.values() if p.get('accountEmail')}
            ids = set(self.worker.profiles)
            seeds = {p['seed'] for p in self.worker.profiles.values()}
            prepared, results = [], []
            for index, raw in enumerate(rows, 1):
                result = {'row': index, 'status': 'invalid'}
                try:
                    if not isinstance(raw, dict) or set(raw) - self.FIELDS:
                        raise self.error(400, 'Only account metadata fields are accepted; cookies and credentials are not imported')
                    row = {k: payload[k] for k in ('accountGroup', 'proxyId') if k in payload}
                    row.update(raw)
                    row.setdefault('name', row.get('accountEmail') or f'Account {index:03d}')
                    result['regionStatus'] = self.import_region(row)
                    row['seed'] = secrets.randbelow(2147483647) + 1
                    while row['seed'] in seeds:
                        row['seed'] = secrets.randbelow(2147483647) + 1
                    seeds.add(row['seed'])
                    profile = self.validate(row)
                    email = profile['accountEmail']
                    if email and not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', email):
                        raise self.error(400, 'Invalid account email')
                    if profile['proxyId']:
                        self.worker.catalog.entry(profile['proxyId'])
                    result.update(name=profile['name'], accountEmail=email, accountGroup=profile['accountGroup'],
                                  locale=profile['locale'], timezone=profile['timezone'], regionMode=profile['regionMode'])
                    if profile['id'] in ids or email and email.casefold() in emails:
                        result.update(status='duplicate', error='An account with this id or email already exists')
                    else:
                        ids.add(profile['id'])
                        if email:
                            emails.add(email.casefold())
                        profile['createdAt'] = now()
                        prepared.append((profile, result))
                        result['status'] = 'ready'
                except self.error as exc:
                    result['error'] = str(exc)
                results.append(result)
            if not preview and prepared:
                old = dict(self.worker.profiles)
                for profile, _ in prepared:
                    self.worker.profiles[profile['id']] = profile
                try:
                    self.worker.save()
                except Exception:
                    self.worker.profiles = old
                    raise
                for profile, result in prepared:
                    result.update(status='created', id=profile['id'])
            return {'preview': preview, 'total': len(rows), 'valid': len(prepared),
                    'invalid': len(rows) - len(prepared), 'created': 0 if preview else len(prepared), 'results': results}

    def ids(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= self.MAX_ROWS or any(not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', pid) for pid in ids):
            raise self.error(400, 'Select between 1 and 200 account ids')
        return list(dict.fromkeys(ids))

    def batch(self, payload):
        ids = self.ids(payload.get('ids'))
        action = payload.get('action')
        if action not in ('group', 'proxy'):
            raise self.error(400, 'Account batch action must be group or proxy')
        if ('accountGroup' if action == 'group' else 'proxyId') not in payload:
            raise self.error(400, 'The selected action requires its value field')
        results = []
        with self.worker.lock:
            for pid in ids:
                try:
                    profile = self.worker.profile(pid)
                    if action == 'group':
                        self.worker.update_account(pid, {'accountGroup': payload.get('accountGroup', '')})
                    else:
                        if self.worker.is_running(pid):
                            raise self.error(409, 'Close this account before changing its proxy')
                        self.worker.create({'id': pid, 'proxyId': payload.get('proxyId', ''),
                                            'proxy': '', 'proxyUsername': '', 'proxyPassword': ''})
                    results.append({'id': pid, 'status': 'updated'})
                except self.error as exc:
                    results.append({'id': pid, 'status': 'failed', 'error': str(exc)})
                except Exception:
                    results.append({'id': pid, 'status': 'failed', 'error': 'Unable to save this account'})
        succeeded = sum(r['status'] == 'updated' for r in results)
        return {'total': len(ids), 'succeeded': succeeded, 'failed': len(ids) - succeeded, 'results': results}

    def summary(self, pid):
        record = self.observations.get(pid, {})
        observed = self.worker.account_checks.get(pid, {'status': 'unknown', 'reason': 'not_checked'})
        network = record.get('networkCheck')
        key = self.route_key(parse_proxy(self.worker.effective_proxy(self.worker.profile(pid))))
        route_matches = not record.get('accountRoute') or record['accountRoute'] == key
        runtime = self.worker.runtime.get(pid)
        recorded_runtime = self.login_runtimes.get(pid)
        current = bool(recorded_runtime and runtime is recorded_runtime[0]
                       and runtime.get('process') is recorded_runtime[1]
                       and observed is recorded_runtime[2] and self.worker.is_running(pid)
                       and route_matches)
        if current:
            login = copy.deepcopy(observed)
        else:
            reason = ('proxy_changed' if not route_matches or observed.get('reason') == 'proxy_changed'
                      else 'browser_closed' if not self.worker.is_running(pid)
                      else 'session_not_checked')
            login = {'status': 'unknown', 'reason': reason, 'checkedAt': None, 'source': 'dola_visible_page'}
        login.update(isCurrent=current, evidenceScope='current' if current else 'none',
                     identityStatus='unverified' if current and login.get('status') == 'logged_in' else 'unknown')
        previous = record.get('lastAccountCheck')
        previous_route = record.get('lastAccountRoute')
        if not self.page_evidence(previous) and self.page_evidence(record.get('accountCheck')):
            previous, previous_route = record['accountCheck'], record.get('accountRoute')
        history = copy.deepcopy(previous) if self.page_evidence(previous) else None
        if history:
            same_observation = current and previous == observed
            history.update(isCurrent=same_observation, evidenceScope='current' if same_observation else 'history',
                           routeMatches=not previous_route or previous_route == key,
                           identityStatus='unverified' if history.get('status') == 'logged_in' else 'unknown')
        if record.get('networkRoute') and record['networkRoute'] != key:
            network = None
        if login['status'] in ('login_required', 'region_blocked'):
            health = login['status']
        elif network and network.get('reachable') is False:
            health = 'network_error'
        elif login['status'] == 'logged_in':
            health = 'logged_in'
        else:
            health = 'unknown'
        bound = bool(self.worker.profile(pid).get('loginAccountId'))
        identity = {'status': 'unverified' if current and login['status'] == 'logged_in' else 'unknown',
                    'reason': ('bound_account_not_verified' if bound else 'site_identity_not_observed')
                    if current and login['status'] == 'logged_in' else 'login_not_confirmed'}
        return {'accountHealth': health, 'accountCheck': login, 'lastAccountCheck': history,
                'identityCheck': identity, 'networkCheck': copy.deepcopy(network)}

    def details(self, pid):
        with self.worker.lock:
            result = self.worker.state(pid)
            result.update(self.summary(pid), quota={'status': 'unknown', 'remaining': None, 'source': 'not_observed'})
            result.setdefault('createdAt', None)
            result.setdefault('lastUsedAt', None)
            return result

    def snapshot(self, job_id):
        with self.worker.lock:
            if job_id not in self.jobs:
                raise self.error(404, 'Unknown account check job')
            return copy.deepcopy({k: v for k, v in self.jobs[job_id].items() if k not in ('cancel', 'inputs', 'generations')})

    def start(self, payload):
        ids = self.ids(payload.get('ids'))
        kind = payload.get('kind')
        if kind not in ('login', 'network'):
            raise self.error(400, 'Account check kind must be login or network')
        with self.worker.lock:
            if self.closed or any(j['state'] in ('running', 'cancelling') for j in self.jobs.values()):
                raise self.error(409, 'An account check is already running or shutting down')
            inputs = {}
            for pid in ids:
                profile = self.worker.profile(pid)
                inputs[pid] = parse_proxy(self.worker.effective_proxy(profile, refresh=True)) if kind == 'network' else self.worker.runtime.get(pid)
            while len(self.jobs) >= 30:
                self.jobs.pop(next(iter(self.jobs)))
            jid = uuid.uuid4().hex[:12]
            job = {'id': jid, 'kind': kind, 'state': 'running', 'total': len(ids), 'completed': 0,
                   'startedAt': now(), 'results': [], 'items': {pid: 'queued' for pid in ids},
                   'cancel': threading.Event(), 'inputs': inputs,
                   'generations': {pid: self.generations.get(pid, 0) for pid in ids}}
            self.jobs[jid] = job
            try:
                threading.Thread(target=self.run, args=(jid,), daemon=True).start()
            except Exception:
                self.jobs.pop(jid, None)
                raise self.error(503, 'Unable to start account checks') from None
            return self.snapshot(jid)

    def cancel(self, job_id):
        with self.worker.lock:
            self.snapshot(job_id)
            job = self.jobs[job_id]
            if job['state'] == 'running':
                job['state'] = 'cancelling'
                job['cancel'].set()
            return self.snapshot(job_id)

    def close(self):
        with self.worker.lock:
            self.closed = True
            for job in self.jobs.values():
                if job['state'] in ('running', 'cancelling'):
                    job['state'] = 'cancelling'
                    job['cancel'].set()

    def run(self, jid):
        job = self.jobs[jid]
        failed = False
        def check(pid):
            with self.worker.lock:
                if job['cancel'].is_set():
                    return {'cancelled': True}, False
                if self.generations.get(pid, 0) != job['generations'][pid]:
                    return {'status': 'unknown', 'reason': 'proxy_changed', 'checkedAt': now()}, False
                job['items'][pid] = 'running'
            try:
                if job['kind'] == 'login':
                    with self.worker.lock:
                        if self.worker.runtime.get(pid) is not job['inputs'][pid]:
                            return {'status': 'unknown', 'reason': 'session_changed', 'checkedAt': now()}, False
                    generation = self.generations.get(pid, 0)
                    result = self.worker.check_account(pid, cancel=job['cancel'])
                    return result, not job['cancel'].is_set() and generation == self.generations.get(pid, 0) and self.worker.runtime.get(pid) is job['inputs'][pid]
                result = diagnose_proxy(job['inputs'][pid])
                result['source'] = 'profile_proxy_diagnostics'
                with self.worker.lock:
                    profile = self.worker.profile(pid)
                    applied = (not self.closed and not job['cancel'].is_set()
                               and self.generations.get(pid, 0) == job['generations'][pid]
                               and parse_proxy(self.worker.effective_proxy(profile)) == job['inputs'][pid])
                    if applied:
                        self.record_check(pid, 'network', result, job['inputs'][pid])
                return result, applied
            except Exception:
                return {'status': 'unknown', 'reason': 'check_failed', 'checkedAt': now(), 'error': 'Account check failed'}, False
        try:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix='account-check') as pool:
                futures = {pool.submit(check, pid): pid for pid in job['inputs']}
                for future in as_completed(futures):
                    pid, (result, applied) = futures[future], future.result()
                    with self.worker.lock:
                        job['results'].append({'id': pid, 'check': result, 'applied': applied})
                        job['items'][pid] = 'cancelled' if result.get('cancelled') else 'completed' if not result.get('error') else 'failed'
                        job['completed'] += 1
        except Exception:
            failed = True
        finally:
            with self.worker.lock:
                job['state'] = 'failed' if failed else 'cancelled' if job['cancel'].is_set() else 'completed'
                job['finishedAt'] = now()
                for pid, state in job['items'].items():
                    if state in ('queued', 'running'):
                        job['items'][pid] = 'cancelled' if job['cancel'].is_set() else 'failed'

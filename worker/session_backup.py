"""Dola-only, encrypted login snapshots and non-destructive session imports."""
import base64
import copy
import json
import math
import threading
import time
import uuid
from urllib.parse import urlsplit

from websockets.sync.client import connect
from account_status import OBSERVE_PAGE, classify_observation
from google_credentials import windows_protect

MAX_BYTES = 8 * 1024 * 1024
ENV_FIELDS = ('locale', 'timezone', 'seed', 'screenWidth', 'screenHeight', 'regionMode',
              'fingerprintPlatform', 'fingerprintNoise', 'hardwareConcurrency', 'deviceMemory',
              'gpuVendor', 'gpuRenderer', 'fingerprintScreenWidth', 'fingerprintScreenHeight', 'taskbarHeight')


def dola_domain(value):
    host = value.lstrip('.').lower() if isinstance(value, str) else ''
    return host == 'dola.com' or host.endswith('.dola.com')


def dola_origin(value):
    try:
        p = urlsplit(value)
        if p.scheme == 'https' and dola_domain(p.hostname) and not p.username and not p.password and p.port in (None, 443):
            return 'https://' + p.hostname.lower()
    except (ValueError, TypeError):
        pass
    return None


CAPTURE_STORAGE = r'''(async () => {
 const result={origin:location.origin,localStorage:[],sessionStorage:[],indexedDB:[],warnings:[]};
 for(const kind of ['localStorage','sessionStorage']){
   const storage=window[kind];
   for(let i=0;i<storage.length;i++){const name=storage.key(i);result[kind].push({name,value:storage.getItem(name)});}
 }
 const req=r=>new Promise((ok,no)=>{r.onsuccess=()=>ok(r.result);r.onerror=()=>no(new Error('storage'));});
 const plain=(v,seen=new WeakSet())=>{
   if(v===null||['string','boolean'].includes(typeof v))return true;
   if(typeof v==='number')return Number.isFinite(v);
   if(typeof v!=='object'||seen.has(v))return false;
   if(!Array.isArray(v)&&Object.getPrototypeOf(v)!==Object.prototype)return false;
   seen.add(v);const ok=Object.values(v).every(x=>plain(x,seen));seen.delete(v);return ok;
 };
 if(!indexedDB.databases){result.warnings.push('indexeddb_unavailable');return result;}
 for(const info of await indexedDB.databases()){
   let db;
   try{
     db=await req(indexedDB.open(info.name));
     const spec={name:db.name,version:db.version,stores:[]};
     for(const name of db.objectStoreNames){
       const store=db.transaction(name,'readonly').objectStore(name);
       const keysRequest=req(store.getAllKeys()), valuesRequest=req(store.getAll());
       const [keys,values]=await Promise.all([keysRequest,valuesRequest]);
       if(!plain(keys)||!plain(values))throw new Error('non_json_records');
       spec.stores.push({name,keyPath:store.keyPath,autoIncrement:store.autoIncrement,
         indexes:[...store.indexNames].map(n=>{const i=store.index(n);return {name:n,keyPath:i.keyPath,unique:i.unique,multiEntry:i.multiEntry};}),
         records:values.map((value,i)=>({key:keys[i],value}))});
     }
     result.indexedDB.push(spec);
   }catch{result.warnings.push('indexeddb_not_exported');}finally{db?.close();}
 }
 return result;
})()'''

RESTORE_STORAGE = r'''(async () => {
 const data=SESSION_DATA;
 if(location.origin!==data.origin)throw new Error('origin_changed');
 for(const kind of ['localStorage','sessionStorage'])for(const entry of data[kind])window[kind].setItem(entry.name,entry.value);
 const req=r=>new Promise((ok,no)=>{r.onsuccess=()=>ok(r.result);r.onerror=()=>no(new Error('storage'));r.onblocked=()=>no(new Error('blocked'));});
 for(const spec of data.indexedDB){
   const opening=indexedDB.open(spec.name,spec.version);
   opening.onupgradeneeded=()=>{
     const db=opening.result;
     for(const s of spec.stores){
       const options={autoIncrement:s.autoIncrement};if(s.keyPath!==null)options.keyPath=s.keyPath;
       const store=db.createObjectStore(s.name,options);
       for(const i of s.indexes)store.createIndex(i.name,i.keyPath,{unique:i.unique,multiEntry:i.multiEntry});
     }
   };
   const db=await req(opening);
   try{
     for(const s of spec.stores){
       await new Promise((ok,no)=>{
         const tx=db.transaction(s.name,'readwrite'),store=tx.objectStore(s.name);
         tx.oncomplete=ok;tx.onerror=()=>no(new Error('storage'));tx.onabort=()=>no(new Error('storage'));
         for(const r of s.records){if(s.keyPath!==null)store.put(r.value);else store.put(r.value,r.key);}
       });
     }
   }finally{db.close();}
 }
 return true;
})()'''


class SessionBackups:
    def __init__(self, worker, error):
        self.worker, self.error = worker, error
        self.directory = worker.data_dir / 'session-backups'
        self.lock = threading.RLock()
        self.busy = set()
        self.drafts = {}
        self.messages = {}
        self.closed = False

    def _fail(self, message='会话操作未完成，请检查浏览器和备份文件。', status=400):
        return self.error(status, message)

    def _call(self, endpoint, method, params=None, timeout=15):
        with connect(endpoint['webSocketDebuggerUrl'], proxy=None, open_timeout=3, close_timeout=1, max_size=MAX_BYTES * 2) as ws:
            ws.send(json.dumps({'id': 1, 'method': method, 'params': params or {}}))
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                reply = json.loads(ws.recv(timeout=max(.1, until-time.monotonic())))
                if reply.get('id') == 1:
                    if reply.get('error') or reply.get('result', {}).get('exceptionDetails'):
                        raise self._fail('浏览器会话数据暂时无法读取或恢复。', 502)
                    return reply.get('result', {})
        raise self._fail('会话操作等待超时。', 504)

    def _evaluate(self, page, expression):
        return self._call(page, 'Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True}, timeout=30).get('result', {}).get('value')

    def _path(self, pid):
        self.worker.profile(pid)
        return self.directory / (pid + '.json')

    def _load(self, pid):
        try:
            path = self._path(pid)
            if not path.exists():
                return None
            envelope = json.loads(path.read_text(encoding='utf-8'))
            data = json.loads(windows_protect(base64.b64decode(envelope['blob'], validate=True), decrypt=True))
            if data.get('profileId') != pid:
                raise ValueError()
            return data
        except Exception:
            raise self._fail('本机备份无法解密，请使用原 Windows 用户或重新备份。', 409) from None

    def _metadata(self, data):
        cookies = data.get('cookies', [])
        return {'available': True, 'savedAt': data.get('savedAt', ''), 'cookieCount': len(cookies),
                'expiredCount': sum(c.get('expires', -1) > 0 and c['expires'] <= time.time() for c in cookies),
                'originCount': len(data.get('origins', [])), 'warnings': data.get('warnings', []),
                'hasSessionCookie': any(c['name'] in ('sessionid', 'sessionid_ss', 'sid_tt') and c['value'] for c in cookies),
                'databaseCount': sum(len(o.get('indexedDB', [])) for o in data.get('origins', []))}

    def _save_snapshot(self, pid, data):
        data = dict(data, profileId=pid, savedAt=time.time())
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise self._fail('会话超过 8 MiB，原备份保留。', 413)
        protected = windows_protect(raw)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(pid)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'format':'cloak-dola-encrypted','version':1,'blob':base64.b64encode(protected).decode('ascii')}), encoding='utf-8')
        temp.replace(path)

    def prepare_startup(self, profile):
        return dola_origin(profile['startUrl']) and (self.directory / (profile['id'] + '.json')).exists()

    def restore_startup(self, pid, runtime):
        try:
            with self.lock:
                data = self._load(pid)
            profile = self.worker.profile(pid)
            if not data or any(profile.get(k) != v for k,v in data.get('environment',{}).items()):
                self.messages[pid] = '环境配置已改变，使用浏览器原有数据；备份未自动恢复。'
                return
            endpoint = self.worker.cdp_json(runtime['cdpPort'],'/json/version')
            current = self._call(endpoint,'Storage.getCookies').get('cookies',[])
            current = {(c['name'],c['domain'],c['path']):c for c in current}
            mismatch = any(current.get((c['name'],c['domain'],c['path']),c)['value'] != c['value']
                           for c in data['cookies'] if c['name'] in ('sessionid','sessionid_ss','sid_tt'))
            if mismatch:
                return
            missing = [c for c in data['cookies'] if 'expires' not in c and (c['name'],c['domain'],c['path']) not in current]
            if missing:
                self._call(endpoint,'Storage.setCookies',{'cookies':missing})
            # Browser profile already persists localStorage and IndexedDB. Only
            # restore tab-scoped storage if the identity cookies still agree.
            if not mismatch:
                origin = dola_origin(profile['startUrl'])
                storage = next((o for o in data['origins'] if o['origin']==origin),None)
                if storage and storage['sessionStorage']:
                    self._restore_origin(self.worker.target(runtime),dict(storage,localStorage=[],indexedDB=[]))
        except Exception:
            self.messages[pid] = '启动时备份恢复未完成，仍保留浏览器原有数据。'

    def info(self, pid):
        with self.worker.lock:
            self.worker.profile(pid)
        with self.lock:
            data = self._load(pid)
            return dict(self._metadata(data) if data else {'available': False}, busy=pid in self.busy,
                        message=self.messages.get(pid, ''))

    def backup(self, pid, automatic=False):
        with self.lock:
            if self.closed or pid in self.busy:
                if automatic:
                    return
                raise self._fail('此环境的会话操作正在进行。', 409)
            self.busy.add(pid)
        try:
            with self.worker.lock:
                profile = copy.deepcopy(self.worker.profile(pid))
                runtime = self.worker.runtime.get(pid)
                generation = self.worker.accounts.generations.get(pid, 0)
            if not runtime or runtime['process'].poll() is not None:
                raise self._fail('请先打开此账号的 Dola 浏览器。', 409)
            page = self.worker.target(runtime)
            origin = dola_origin(page.get('url', ''))
            if not origin or classify_observation(self._evaluate(page, OBSERVE_PAGE))[0] != 'logged_in':
                raise self._fail('当前页面尚未确认登录，保留原备份。请登录 Dola 后重试。', 409)
            endpoint = self.worker.cdp_json(runtime['cdpPort'], '/json/version')
            cookies = self._call(endpoint, 'Storage.getCookies').get('cookies', [])
            cookies = [c for c in cookies if dola_domain(c.get('domain'))]
            storage = self._evaluate(page, CAPTURE_STORAGE)
            if not isinstance(storage, dict) or storage.get('origin') != origin:
                raise self._fail('页面已切换，保留原备份。', 409)
            identity = lambda values: {(c['name'], c['domain'], c.get('path','/')):c['value'] for c in values
                                       if c['name'] in ('sessionid','sessionid_ss','sid_tt','sid_guard') and dola_domain(c.get('domain'))}
            latest = self._call(endpoint,'Storage.getCookies').get('cookies',[])
            if identity(latest) != identity(cookies):
                raise self._fail('登录会话在备份过程中变化，保留原备份，请重试。',409)
            # Preserve captured entries of other Dola origins from the preceding
            # encrypted snapshot. No navigation or third-party page inspection.
            with self.lock:
                try:
                    previous = self._load(pid)
                except self.error:
                    previous = None  # A fresh confirmed login can repair a corrupt backup.
            same_identity = previous and identity(previous.get('cookies',[])) == identity(cookies)
            origins = [o for o in (previous or {}).get('origins', []) if o['origin'] != origin] if same_identity else []
            origins.append(storage)
            data = self.normalize({'cookies': cookies, 'origins': origins})
            if not data['cookies']:
                raise self._fail('未读取到 Dola Cookie，保留原备份。', 409)
            data.update(profileId=pid, savedAt=time.time(), environment={k: profile[k] for k in ENV_FIELDS},
                        warnings=list(dict.fromkeys(storage.get('warnings', []))), sourceName=profile['name'])
            with self.worker.lock:
                if (self.closed or self.worker.runtime.get(pid) is not runtime or runtime['process'].poll() is not None
                        or self.worker.accounts.generations.get(pid, 0) != generation
                        or self.worker.target(runtime)['id'] != page['id']):
                    raise self._fail('环境已变化，保留原备份。', 409)
                with self.lock:
                    self._save_snapshot(pid, data)
                    self.messages[pid] = '已保存本机加密备份。'
            return self.info(pid)
        except self.error:
            if automatic:
                with self.lock:
                    self.messages[pid] = '自动备份未完成，原备份保留；可在会话备份页重试。'
                return
            raise
        except Exception:
            if automatic:
                with self.lock:
                    self.messages[pid] = '自动备份未完成，原备份保留；可在会话备份页重试。'
                return
            raise self._fail('备份未完成，原备份保留。', 502) from None
        finally:
            with self.lock:
                self.busy.discard(pid)

    def after_login(self, pid):
        with self.lock:
            if self.closed or pid in self.busy:
                return
            # Avoid repeatedly exporting private storage on one-second status
            # polling. Explicit backup always takes a fresh snapshot.
            path = self.directory / (pid + '.json')
            if path.exists() and time.time() - path.stat().st_mtime < 300:
                return
        threading.Thread(target=self.backup, args=(pid, True), daemon=True, name='session-backup').start()

    def normalize(self, value):
        try:
            if isinstance(value, str):
                if len(value.encode('utf-8')) > MAX_BYTES * 2:
                    raise ValueError()
                value = json.loads(value.lstrip('\ufeff'))
            if isinstance(value, dict) and value.get('format') == 'cloak-dola-encrypted':
                value = json.loads(windows_protect(base64.b64decode(value['blob'], validate=True), decrypt=True))
            if len(json.dumps(value, allow_nan=False).encode('utf-8')) > MAX_BYTES:
                raise ValueError()
            raw = value if isinstance(value, list) else value.get('cookies_list', value.get('cookies', []))
            if isinstance(raw, dict):
                raw = [dict(v, name=k) if isinstance(v, dict) else {'name': k, 'value': v} for k,v in raw.items()]
            if not isinstance(raw, list) or len(raw) > 3000:
                raise ValueError()
            cookies, skipped = [], 0
            for c in raw:
                domain = c.get('domain', '.dola.com')
                if not dola_domain(domain):
                    skipped += 1; continue
                item = {'name': c['name'], 'value': c['value'], 'domain': domain.lower(), 'path': c.get('path', '/')}
                if not all(isinstance(item[k], str) for k in item) or not item['name'] or not item['path'].startswith('/') or any('\x00' in v or '\r' in v or '\n' in v for v in item.values()):
                    raise ValueError()
                for key, alias in (('secure', 'isSecure'), ('httpOnly', 'isHttpOnly')):
                    flag = c.get(key, c.get(alias, False))
                    if not isinstance(flag, bool):
                        raise ValueError()
                    item[key] = flag
                same = c.get('sameSite')
                if same:
                    item['sameSite'] = {'strict':'Strict', 'lax':'Lax', 'none':'None', 'no_restriction':'None', 'unspecified':'Lax'}[str(same).lower()]
                if c.get('partitionKey'):
                    partition = c['partitionKey']
                    if not isinstance(partition,dict) or not dola_origin(partition.get('topLevelSite')) or not isinstance(partition.get('hasCrossSiteAncestor'),bool):
                        raise ValueError()
                    item['partitionKey']=partition
                expires = c.get('expires', c.get('expirationDate', c.get('expiresUtc')))
                if expires is not None and not c.get('isSession', c.get('session', False)):
                    expires = float(expires)
                    if not math.isfinite(expires):
                        raise ValueError()
                    if expires > 1e12:
                        expires /= 1000
                    if expires > 0:
                        item['expires'] = expires
                cookies.append(item)
            if not cookies:
                raise ValueError()
            origins = value.get('origins') if isinstance(value, dict) else None
            if origins is None:
                storage = value.get('storage', value) if isinstance(value, dict) else {}
                origins = [dict(storage, origin=dola_origin(storage.get('url', '')) or 'https://www.dola.com')]
            if not isinstance(origins, list) or len(origins) > 8:
                raise ValueError()
            normalized = []
            for o in origins:
                origin = dola_origin(o.get('origin', ''))
                if not origin or o['origin'].rstrip('/') != origin:
                    raise ValueError()
                entry = {'origin': origin}
                for kind in ('localStorage', 'sessionStorage'):
                    values = o.get(kind, [])
                    if isinstance(values, dict):
                        values = [{'name':k, 'value':v} for k,v in values.items()]
                    if not isinstance(values, list) or len(values) > 10000 or any(not isinstance(v.get('name'),str) or not isinstance(v.get('value'),str) for v in values):
                        raise ValueError()
                    entry[kind] = values
                databases = o.get('indexedDB', [])
                if isinstance(databases, dict):
                    databases = databases.get('databases', [])
                if not isinstance(databases, list) or len(databases) > 100:
                    raise ValueError()
                for db in databases:
                    if not isinstance(db.get('name'),str) or not db['name'] or isinstance(db.get('version'),bool) or not isinstance(db.get('version', 1),int) or db.get('version',1) < 1 or not isinstance(db.get('stores',[]),list):
                        raise ValueError()
                    db.setdefault('version',1)
                    for s in db.get('stores', []):
                        if not isinstance(s.get('name'),str) or not s['name']:
                            raise ValueError()
                        s.setdefault('keyPath',None); s.setdefault('autoIncrement',False); s.setdefault('indexes',[])
                        def key_path(v):
                            return isinstance(v,str) or isinstance(v,list) and all(isinstance(k,str) for k in v)
                        if (s['keyPath'] is not None and not key_path(s['keyPath']) or not isinstance(s['autoIncrement'],bool)
                                or not isinstance(s['indexes'],list) or not isinstance(s.get('records',[]),list)):
                            raise ValueError()
                        for index in s['indexes']:
                            if not isinstance(index.get('name'),str) or not key_path(index.get('keyPath')) or not isinstance(index.get('unique',False),bool) or not isinstance(index.get('multiEntry',False),bool):
                                raise ValueError()
                            index.setdefault('unique',False);index.setdefault('multiEntry',False)
                        records=[]
                        for r in s.get('records',[]):
                            if isinstance(r,list) and len(r)==2: r={'key':r[0],'value':r[1]}
                            if not isinstance(r,dict) or 'value' not in r or s['keyPath'] is None and 'key' not in r:
                                raise ValueError()
                            records.append(r)
                        s['records']=records
                    if len({s['name'] for s in db.get('stores',[])}) != len(db.get('stores',[])):
                        raise ValueError()
                if len({db['name'] for db in databases}) != len(databases):
                    raise ValueError()
                entry['indexedDB'] = databases
                normalized.append(entry)
            if len({o['origin'] for o in normalized}) != len(normalized):
                raise ValueError()
            return {'format':'cloak-dola-session', 'version':1, 'cookies':cookies, 'origins':normalized,
                    'environment':{k:v for k,v in (value.get('environment',{}) if isinstance(value,dict) else {}).items() if k in ENV_FIELDS},
                    'skippedCount':skipped, 'warnings':[]}
        except Exception:
            raise self._fail('CK 文件格式无效、过大或无法解密；支持 Dola Cookie JSON 与完整会话备份。') from None

    def preview(self, raw):
        data = self.normalize(raw)
        with self.lock:
            self.drafts = {k:v for k,v in self.drafts.items() if v['expires'] > time.time()}
            if len(self.drafts) >= 8:
                raise self._fail('会话预览过多，请稍后再试。', 409)
            token = uuid.uuid4().hex
            self.drafts[token] = {'data':data, 'expires':time.time()+600}
        return dict(self._metadata(data), draftId=token, skippedCount=data['skippedCount'], environment=data['environment'])

    def preview_backup(self, pid):
        with self.lock:
            data = self._load(pid)
        if not data:
            raise self._fail('此环境还没有本机备份。', 404)
        return self.preview(data)

    def export(self, pid, format):
        if format not in ('encrypted','json'):
            raise self._fail('请选择加密备份或 CK JSON。')
        with self.lock:
            data = self._load(pid)
            if not data:
                raise self._fail('请先备份已登录会话。', 404)
            text = self._path(pid).read_text(encoding='utf-8') if format == 'encrypted' else json.dumps({k:v for k,v in data.items() if k not in ('profileId','sourceName')}, ensure_ascii=False, indent=2)
        return {'text':text, 'fileName':pid + ('.ckbackup' if format=='encrypted' else '.json')}

    def _restore_origin(self, page, data):
        # Use an offline document at the exact origin. Restore storage before
        # loading any Dola scripts; no arbitrary HTML or code from the file runs.
        with connect(page['webSocketDebuggerUrl'],proxy=None,open_timeout=3,close_timeout=1,max_size=MAX_BYTES*2) as ws:
            serial=0
            def call(method, params=None):
                nonlocal serial
                serial+=1; request=serial
                ws.send(json.dumps({'id':request,'method':method,'params':params or {}}))
                until=time.monotonic()+30
                while time.monotonic()<until:
                    reply=json.loads(ws.recv(timeout=max(.1,until-time.monotonic())))
                    if reply.get('method')=='Fetch.requestPaused':
                        serial+=1
                        ws.send(json.dumps({'id':serial,'method':'Fetch.fulfillRequest','params':{
                            'requestId':reply['params']['requestId'],'responseCode':200,
                            'responseHeaders':[{'name':'Content-Type','value':'text/html; charset=utf-8'},
                                {'name':'Content-Security-Policy','value':"default-src 'none'"}],
                            'body':base64.b64encode(b'<!doctype html><title>Session restore</title>').decode('ascii')}}))
                    elif reply.get('id')==request:
                        if reply.get('error') or reply.get('result',{}).get('exceptionDetails'):
                            raise self._fail('网站存储恢复未完成。',502)
                        return reply.get('result',{})
                raise self._fail('网站存储恢复超时。',504)
            call('Fetch.enable',{'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
            try:
                call('Page.navigate',{'url':data['origin']+'/__cloak_session_restore__'})
                for _ in range(100):
                    reply=call('Runtime.evaluate',{'expression':'location.origin','returnByValue':True})
                    if reply.get('result',{}).get('value')==data['origin']:
                        break
                    time.sleep(.02)
                else:
                    raise self._fail('恢复页面未准备好。',504)
                expression=RESTORE_STORAGE.replace('SESSION_DATA',json.dumps(data,ensure_ascii=True),1)
                result=call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
                if result.get('result',{}).get('value') is not True:
                    raise self._fail('网站存储恢复未完成。',502)
            finally:
                call('Fetch.disable')

    def import_new(self, payload):
        if any(k not in ('draftId','name','proxyId') for k in payload):
            raise self._fail('导入只接受预览、账号名称与代理，不会覆盖现有环境。')
        with self.lock:
            if self.closed:
                raise self._fail('软件正在关闭。',409)
            draft=self.drafts.get(payload.get('draftId'))
            if not draft or draft['expires']<=time.time():
                raise self._fail('预览已过期，请重新读取 CK 文件。',409)
            if draft.get('used'):
                raise self._fail('此预览已导入，请打开新建环境查看。',409)
            draft['used']=True; data=copy.deepcopy(draft['data'])
        pid=None
        try:
            name=payload.get('name','导入的 Dola 账号')
            proxy_id=payload.get('proxyId','')
            with self.worker.lock:
                if self.closed or self.worker.accounts.closed:
                    raise self._fail('软件正在关闭。',409)
                profile=self.worker.create(dict(data['environment'],name=name,proxyId=proxy_id,startUrl='about:blank',regionMode='manual'))
                pid=profile['id']
                with self.lock:
                    self.busy.add(pid)
                self.worker.launch(pid,embedded=True)
            runtime=self.worker.runtime[pid]
            endpoint=self.worker.cdp_json(runtime['cdpPort'],'/json/version')
            live=[c for c in data['cookies'] if c.get('expires',-1)<=0 or c['expires']>time.time()]
            self._call(endpoint,'Storage.setCookies',{'cookies':live})
            page=self.worker.target(runtime)
            for origin in data['origins']:
                self._restore_origin(page,origin)
            with self.worker.lock:
                p=dict(self.worker.profile(pid));p['startUrl']='https://www.dola.com/chat/'
                self.worker.profiles[pid]=p;self.worker.save()
                data['environment'] = {k:p[k] for k in ENV_FIELDS}
                with self.lock:
                    self._save_snapshot(pid,data)
            self.worker.navigate(pid,{'url':p['startUrl']})
            result={'status':'unknown','reason':'insufficient_evidence'}
            for _ in range(20):
                if self.closed:
                    break
                result=self.worker.check_account(pid)
                if result['status'] in ('logged_in','login_required','region_blocked'):
                    break
                time.sleep(.5)
            with self.lock:
                self.messages[pid]='会话已导入；登录结果以页面检测为准。'
            return {'id':pid,'restored':True,'check':result,'expiredSkipped':len(data['cookies'])-len(live)}
        except Exception:
            if pid:
                try:self.worker.close(pid)
                except Exception:pass
                with self.lock:self.messages[pid]='会话恢复未完成；原账号未修改，可打开新环境手动登录。'
                return {'id':pid,'restored':False,'check':{'status':'unknown','reason':'restore_failed'}}
            with self.lock:draft['used']=False
            raise self._fail('新环境创建失败，请检查账号名称和所选代理。',409) from None
        finally:
            with self.lock:
                if pid:self.busy.discard(pid)

    def close(self):
        with self.lock:
            self.closed=True;self.drafts.clear()

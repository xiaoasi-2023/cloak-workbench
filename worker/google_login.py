"""User-started Google OAuth helper for existing owned browser environments.

Only bounded entry-button retries; no credential replay, OTP or CAPTCHA handling.
Public job records contain stage codes only, never credentials or page content.
"""
import copy
import json
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
from account_status import OBSERVE_PAGE, checked_result, classify_observation
from google_credentials import GoogleCredentialStore


class NavigationInterrupted(RuntimeError):
    """A CDP target or execution context changed during navigation."""


TRANSIENT_BROWSER_ERRORS = (NavigationInterrupted, ConnectionClosed, OSError, TimeoutError)


def trusted_google(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme == 'https' and parsed.hostname == 'accounts.google.com' and parsed.port in (None, 443)
    except ValueError:
        return False


def trusted_dola(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return parsed.scheme == 'https' and (host == 'dola.com' or host.endswith('.dola.com')) and parsed.port in (None, 443)
    except ValueError:
        return False


# All selectors are scoped to visible controls. Text is inspected in the page
# and only booleans are returned, not identity, Google errors, OTPs or chat text.
GOOGLE_OBSERVE = r'''(() => {
  if(location.origin !== 'https://accounts.google.com')return {trusted:false};
  const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const has=s=>[...document.querySelectorAll(s)].some(visible);
  const text=(document.body?.innerText||'').slice(0,16000);
  const path=location.pathname.toLowerCase();
  const email=has('#identifierId'), password=has('input[name="Passwd"]');
  const challenge=has('input[type="tel"],input[name="totpPin"],input[name="Pin"],input[autocomplete="one-time-code"],iframe[src*="recaptcha"],input[name="ca"]')
    || /\/challenge\/(?!pwd(?:\/|$))/.test(path)
    || /two.step verification|2.step verification|验证码|两步验证|確認コード|2 段階認証|check your phone|查看手机/i.test(text)
    || (!email&&!password&&/verify it.s you/i.test(text));
  const welcome=path==='/v3/signin/speedbump/workspacetermsofservice';
  const consent=/\/consent|\/oauth.*\/approval|\/signin\/oauth\/id(?:\/|$)/.test(path)||has('#submit_button')
    || /wants access to|wants to access|アクセスを求め|请求访问|权限授权/i.test(text);
  const error=has('input[aria-invalid="true"], [data-error-code]')
    || /wrong password|couldn.t sign you in|couldn.t find your google account|this browser or app may not be secure|密码错误|找不到.*Google.*账号|パスワードが正しくありません|ブラウザ.*安全/i.test(text);
  return {trusted:true,ready:document.readyState,challenge,consent,welcome,error,
          email,password,
          chooser:/accountchooser/i.test(path)};
})()'''

DOLA_OBSERVE = r'''(() => {
  if(location.protocol!=='https:'||!(location.hostname==='dola.com'||location.hostname.endsWith('.dola.com')))return {trusted:false};
  const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const text=(document.body?.innerText||'').slice(0,16000);
  const age=/age verification|confirm your age|verify your age|年齢(?:を)?確認|年龄确认|确认.*年龄/i.test(text);
  const security=[...document.querySelectorAll('iframe[src*="captcha"],input[autocomplete="one-time-code"]')].some(visible)
    || /verify you are human|人机验证/i.test(text);
  return {trusted:true,ready:document.readyState,age,security,manual:age||security};
})()'''


def onboarding_expression(kind, email):
    """Recheck origin, page kind and identity immediately before a single click."""
    template = r'''(() => {
      const kind=KIND, email=EMAIL.toLowerCase();
      const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
      const text=(document.body?.innerText||'').slice(0,20000);
      const buttons=[...document.querySelectorAll('button,[role="button"],input[type="submit"]')].filter(e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
      const label=e=>(e.innerText||e.value||e.getAttribute('aria-label')||'').trim();
      let matches=[];
      if(kind==='age') {
        const gate=DOLA_GATE;
        if(!gate.trusted||gate.security||!gate.age)return {clicked:false,reason:'page_changed'};
        const dialogs=[...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[aria-modal="true"],[class*="modal-content"],.arco-modal')].filter(visible);
        const dialog=dialogs.find(e=>/18/.test(e.innerText||'')&&/age|年齢|年龄/i.test(e.innerText||''));
        if(!dialog)return {clicked:false,reason:'age_confirmation_required'};
        matches=buttons.filter(e=>dialog.contains(e)&&/^(OK|確認|确认|确定|はい|Yes|I am 18 or older)$/i.test(label(e)));
      } else {
        const gate=GOOGLE_GATE;
        if(!gate.trusted||gate.challenge||gate.error||gate.email||gate.password||gate.chooser)return {clicked:false,reason:'page_changed'};
        const identities=(text.match(/[A-Z0-9.!#$%&'*+\/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi)||[]).map(e=>e.toLowerCase());
        if(!identities.length)return {clicked:false,reason:'not_ready'};
        if(!identities.includes(email)||identities.some(e=>e!==email))return {clicked:false,reason:'account_mismatch'};
        if(kind==='welcome') {
          if(!gate.welcome||!/Google Workspace/i.test(text))return {clicked:false,reason:'workspace_welcome_required'};
          matches=buttons.filter(e=>/^(I understand|Got it|Accept|I agree|我知道了|我已了解|了解|知道了|同意|理解しました|同意する)$/i.test(label(e)));
        } else if(kind==='consent') {
          const path=location.pathname.toLowerCase();
          const scope=new URL(location.href).searchParams.get('scope');
          const basic=new Set(['openid','email','profile','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/userinfo.profile']);
          const heading=[...document.querySelectorAll('h1,[role="heading"]')].filter(visible).map(e=>e.innerText||'').join(' ');
          // This known Google identity screen grants basic sign-in information.
          // Additional permission screens, checkboxes or unknown scopes stay manual.
          if(!gate.consent||!/^\/signin\/oauth\/id\/?$/.test(path)||!/(^|[^\w.-])dola\.com(?![\w.-])/i.test(heading)
            ||(scope&&scope.split(/\s+/).some(s=>!basic.has(s)))
            ||document.querySelector('input[type="checkbox"],[role="checkbox"]')
            ||!/name and profile (?:picture|photo)|名称和个人资料照片|姓名和个人资料照片|名前とプロフィール写真/i.test(text)
            ||!/email|邮箱|电子邮件|メール/i.test(text)
            ||/Google Drive|Gmail|contacts|calendar|連絡先|カレンダー|云端硬盘|通讯录|日历|read and|edit and|delete|削除|删除/i.test(text))return {clicked:false,reason:'consent_required'};
          matches=buttons.filter(e=>/^(Continue|继续|繼續|続行|次へ)$/i.test(label(e)));
        }
      }
      if(matches.length!==1)return {clicked:false,reason:kind==='age'?'age_confirmation_required':'consent_required'};
      matches[0].click();return {clicked:true};
    })()'''
    expression = template.replace('const kind=KIND, email=EMAIL.toLowerCase();',
                                  'const kind='+json.dumps(kind)+', email='+json.dumps(email)+'.toLowerCase();', 1)
    return expression.replace('const gate=DOLA_GATE;', 'const gate='+DOLA_OBSERVE+';', 1).replace('const gate=GOOGLE_GATE;', 'const gate='+GOOGLE_OBSERVE+';', 1)


def google_submit_expression(stage, value, email):
    # JSON values are script literals only, never URL parameters or logs.
    selector, button = ('#identifierId', '#identifierNext') if stage == 'email' else ('input[name="Passwd"]', '#passwordNext')
    template = r'''(() => {
      if(location.origin!=='https://accounts.google.com')return {submitted:false,reason:'untrusted'};
      const gate=OBSERVATION;
      if(gate.challenge)return {submitted:false,reason:'verification_required'};
      if(gate.welcome)return {submitted:false,reason:'workspace_welcome_required'};
      if(gate.consent)return {submitted:false,reason:'consent_required'};
      if(gate.chooser)return {submitted:false,reason:'choose_account'};
      if(gate.error)return {submitted:false,reason:'google_rejected'};
      const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
      const input=[...document.querySelectorAll(SELECTOR)].find(visible);
      const next=document.querySelector(BUTTON);
      if(!input||!visible(next)||input.disabled)return {submitted:false,reason:'not_ready'};
      if(STAGE==='password') {
        const selected=document.querySelector('[data-email],#profileIdentifier');
        const identity=(selected?.getAttribute('data-email')||selected?.textContent||'').trim().toLowerCase();
        if(identity!==EMAIL.toLowerCase())return {submitted:false,reason:'account_mismatch'};
      }
      input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,VALUE);
      input.dispatchEvent(new Event('input',{bubbles:true})); input.dispatchEvent(new Event('change',{bubbles:true}));
      next.click(); return {submitted:true};
    })()'''
    values = {'SELECTOR': selector, 'BUTTON': button, 'STAGE': stage, 'EMAIL': email, 'VALUE': value}
    expression = re.sub(r'\b(SELECTOR|BUTTON|STAGE|EMAIL|VALUE)\b', lambda m: json.dumps(values[m[0]]), template)
    return expression.replace('const gate=OBSERVATION;', 'const gate=' + GOOGLE_OBSERVE + ';', 1)


def dola_click_expression(kind):
    matcher = r'/^(?:continue with google|sign in with google|log in with google|Google\s*(?:sign\s?in|log\s?in|登录|登入|登錄|繼續|继续)|Google\s*で\s*(?:続ける|ログイン)|(?:使用|通过|透過|通過)\s*Google\s*(?:继续|繼續|登录|登入|登錄)|(?:继续|繼續)使用\s*Google|Google로 계속하기|Google)$/i' if kind == 'google' else r'/^(log\s?in|sign\s?in|登录|登入|登錄|ログイン|로그인)$/i'
    return r'''(() => {
      if(location.protocol!=='https:'||!(location.hostname==='dola.com'||location.hostname.endsWith('.dola.com')))return false;
      const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).opacity!=='0');
      const nodes=[...document.querySelectorAll('button,a,[role="button"],[class*="login-btn-header"],div,span')];
      const label=e=>(e.innerText||e.getAttribute('aria-label')||'').replace(/[\u200b-\u200f\ufeff]/g,'').replace(/\s+/g,' ').trim();
      const matcher=MATCHER;
      let matches=nodes.filter(e=>{
        if(!visible(e)||e.closest('[disabled],[aria-disabled="true"],[inert]')||!matcher.test(label(e)))return false;
        const owner=e.closest('button,a,[role="button"]');
        if(owner&&!matcher.test(label(owner)))return false;
        // A bare logo label inside a download card is not a sign-in option.
        return !/^Google$/i.test(label(e))||!!owner;
      });
      // Header shortcuts can remain in the DOM beneath the sign-in dialog.
      // Prefer dialog controls and never dispatch through a covering overlay.
      const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"],.arco-modal,[class*="modal-content"],[class*="login-modal"]')].filter(visible);
      const inDialog=matches.filter(e=>dialogs.some(d=>d.contains(e)));
      if(inDialog.length)matches=inDialog;
      matches=matches.filter(e=>{
        const rect=e.getBoundingClientRect(), x=rect.left+rect.width/2, y=rect.top+rect.height/2;
        const top=document.elementFromPoint(x,y);
        return top&&(e===top||e.contains(top)||top.contains(e));
      });
      // Dola also renders its provider choice as nested div/span controls.
      // Click the unique innermost match so its event can bubble to the owner.
      const leaves=matches.filter(e=>!matches.some(other=>other!==e&&e.contains(other)));
      if(leaves.length!==1)return false; leaves[0].click(); return true;
    })()'''.replace('MATCHER', matcher)


class GoogleLoginManager:
    ACTIVE = ('running', 'cancelling')
    def __init__(self, worker, error):
        self.worker, self.error = worker, error
        self.vault = GoogleCredentialStore(worker.data_dir)
        self.jobs = {}
        self.closed = False

    def _credential_error(self):
        return self.error(409, '本机保存的凭据无法读取，请重新填写密码；不会使用其他环境的密码。')

    def credential_info(self, pid):
        self.worker.profile(pid)
        bound = self.worker.login_accounts.bound(pid)
        if bound:
            return {'email': bound['email'], 'passwordSet': bool(bound.get('password')),
                    'source': 'account', 'accountId': bound['id']}
        try:
            record = self.vault.load(pid)
        except Exception:
            raise self._credential_error() from None
        return {'email': record['email'] if record else '', 'passwordSet': bool(record), 'source': 'legacy'}

    def forget(self, pid):
        with self.worker.lock:
            self.worker.profile(pid)
            if self.worker.login_accounts.bound(pid):
                raise self.error(409, '绑定账号的密码请到账号列表中管理。')
            if pid in self.jobs and self.jobs[pid]['state'] in self.ACTIVE:
                raise self.error(409, '请先停止当前登录任务。')
            try:
                self.vault.forget(pid)
            except Exception:
                raise self._credential_error() from None
        return {'passwordSet': False}

    def snapshot(self, pid):
        with self.worker.lock:
            self.worker.profile(pid)
            job = self.jobs.get(pid)
            return copy.deepcopy({k: job[k] for k in ('id', 'state', 'stage', 'startedAt', 'updatedAt')} if job else {'state': 'idle', 'stage': 'not_started'})

    def _update(self, pid, job, state, stage):
        with self.worker.lock:
            if self.jobs.get(pid) is not job:
                return
            job.update(state=state, stage=stage, updatedAt=time.time())

    def start(self, pid, payload):
        with self.worker.lock:
            self.worker.profile(pid)
            if self.closed or self.worker.accounts.closed:
                raise self.error(409, '软件正在关闭，无法启动登录。')
            old = self.jobs.get(pid)
            if old and old['state'] in self.ACTIVE:
                raise self.error(409, '此账号的登录正在进行，请勿重复提交。')
            if getattr(self.worker, 'videos', None) and self.worker.videos.busy(pid):
                raise self.error(409, '此环境有视频任务，请先停止视频任务再登录。')
            if sum(j['state'] in self.ACTIVE for j in self.jobs.values()) >= 3:
                raise self.error(409, '最多同时处理三个登录，请等待当前任务完成。')
            runtime = self.worker.runtime.get(pid)
            if not runtime or runtime['process'].poll() is not None:
                raise self.error(409, '请先打开此账号的浏览器。')
            if any(k not in ('email', 'password', 'remember', 'useSaved', 'allowOnboarding', 'ageConfirmed') for k in payload):
                raise self.error(400, '登录请求包含不支持的字段。')
            remember, saved = payload.get('remember', False), payload.get('useSaved', False)
            if not isinstance(remember, bool) or not isinstance(saved, bool):
                raise self.error(400, '记住密码选项必须为布尔值。')
            options = self._options(payload)
            email, password = payload.get('email', ''), payload.get('password', '')
            if not isinstance(email, str) or not isinstance(password, str):
                raise self.error(400, '请填写 Google 邮箱与密码。')
            email = email.strip()
            if getattr(self.worker, 'workspaces', None) and self.worker.workspaces.account_busy(email):
                raise self.error(409, '此邮箱正在执行企业任务，请等待任务结束后登录。')
            inventory = getattr(self.worker, 'login_accounts', None)
            saved_account = next((a for a in inventory.entries.values() if a['email'].casefold() == email.casefold()), None) if inventory else None
            if saved_account and (inventory._enterprise_busy(saved_account) or saved_account.get('workspaceSuspended') or saved_account.get('workspaceMissing')):
                raise self.error(409, '此企业正在执行任务、员工已暂停或目录已无此员工，请核实企业目录后登录。')
            bound = self.worker.login_accounts.bound(pid)
            if bound and email.casefold() != bound['email'].casefold():
                raise self.error(400, '登录邮箱与绑定账号不一致，请先修改绑定。')
            if bound and remember:
                raise self.error(400, '绑定账号的密码请在账号列表中修改。')
            if saved:
                if password:
                    raise self.error(400, '已存密码和新密码不能同时使用。')
                try:
                    record = self.worker.login_accounts.credentials(bound['id']) if bound else self.vault.load(pid)
                except Exception:
                    raise self._credential_error() from None
                if not record or email.casefold() != record['email'].casefold():
                    raise self.error(400, '已存密码仅能用于此环境保存的邮箱。')
                password = record['password']
            if not re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,120}\.[^\s@]{2,30}', email) or len(email) > 254:
                raise self.error(400, '请填写完整的 Google 账号邮箱。')
            if not password or len(password) > 4096 or '\x00' in password:
                raise self.error(400, '请填写密码，或选择使用此环境已保存的密码。')
            if remember:
                try:
                    self.vault.save(pid, email, password)
                except Exception:
                    raise self.error(409, '密码保存失败，未启动登录；可取消记住密码后再试。') from None
            while len(self.jobs) >= 32 and any(j['state'] not in self.ACTIVE for j in self.jobs.values()):
                self.jobs.pop(next(k for k,j in self.jobs.items() if j['state'] not in self.ACTIVE))
            job = {'id': uuid.uuid4().hex[:12], 'state': 'running', 'stage': 'opening_dola',
                   'startedAt': time.time(), 'updatedAt': time.time(), 'cancel': threading.Event(),
                   'email': email, 'loginAccountId': bound['id'] if bound else '', 'options': options, 'runtime': runtime,
                   'generation': self.worker.accounts.generations.get(pid, 0)}
            self.jobs[pid] = job
            generation = self.worker.accounts.generations.get(pid, 0)
            resume_target = old.get('targetId') if old and old['state'] == 'needs_attention' else None
            threading.Thread(target=self._run, args=(pid, job, runtime, generation, email, password, resume_target), daemon=True).start()
            return self.snapshot(pid)

    def _options(self, payload):
        options = {key: payload.get(key, False) for key in ('allowOnboarding', 'ageConfirmed')}
        if any(not isinstance(value, bool) for value in options.values()):
            raise self.error(400, '新账号处理与年龄确认选项必须为布尔值。')
        return options

    def resume(self, pid, payload):
        with self.worker.lock:
            self.worker.profile(pid)
            if getattr(self.worker, 'videos', None) and self.worker.videos.busy(pid):
                raise self.error(409, '此环境有视频任务，请先停止视频任务再继续登录。')
            if any(k not in ('allowOnboarding', 'ageConfirmed') for k in payload):
                raise self.error(400, '继续请求包含不支持的字段。')
            options = self._options(payload)
            old = self.jobs.get(pid)
            if not old or old['state'] != 'needs_attention' or not old.get('email'):
                raise self.error(409, '当前没有可继续的登录任务，请重新开始。')
            if not self._valid(pid, old['runtime'], old['generation']):
                raise self.error(409, '原浏览器环境已改变，请重新开始登录。')
            if sum(j['state'] in self.ACTIVE for j in self.jobs.values()) >= 3:
                raise self.error(409, '最多同时处理三个登录，请稍后继续。')
            job = dict(old, id=uuid.uuid4().hex[:12], state='running', stage='resuming',
                       updatedAt=time.time(), cancel=threading.Event(), options=options)
            self.jobs[pid] = job
            threading.Thread(target=self._run, args=(pid, job, old['runtime'], old['generation'],
                             old['email'], None, old.get('targetId')), daemon=True).start()
            return self.snapshot(pid)

    def cancel(self, pid):
        with self.worker.lock:
            self.worker.profile(pid)
            job = self.jobs.get(pid)
            if job and job['state'] in self.ACTIVE:
                job['cancel'].set()
                self._update(pid, job, 'cancelling', 'stopping')
            return self.snapshot(pid)

    def _valid(self, pid, runtime, generation):
        return (not self.closed and not self.worker.accounts.closed and self.worker.runtime.get(pid) is runtime
                and runtime['process'].poll() is None and self.worker.accounts.generations.get(pid, 0) == generation)

    def _call(self, runtime, page, method, params=None):
        # A dedicated connection addresses the exact target; the worker's main
        # browser target must not be switched to an OAuth popup.
        with connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=3, close_timeout=1) as ws:
            ws.send(json.dumps({'id': 1, 'method': method, 'params': params or {}}))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = json.loads(ws.recv(timeout=max(.1, deadline - time.monotonic())))
                if result.get('id') == 1:
                    if 'error' in result:
                        message = str(result['error'].get('message', '')).lower()
                        if any(text in message for text in ('execution context', 'cannot find context', 'target closed', 'session closed', 'inspected target navigated')):
                            raise NavigationInterrupted('Browser page is navigating')
                        raise RuntimeError('Browser command failed')
                    return result.get('result', {})
        raise TimeoutError('Browser command timed out')

    def _evaluate(self, runtime, page, expression):
        result = self._call(runtime, page, 'Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        if result.get('exceptionDetails'):
            raise RuntimeError('Page observation failed')
        return result.get('result', {}).get('value')

    def _pages(self, runtime):
        return [p for p in self.worker.cdp_json(runtime['cdpPort'], '/json/list') if p.get('type') == 'page']

    def _observe(self, runtime, page, expression):
        # Reads are safe to repeat on the next fresh target snapshot. A closing
        # OAuth popup or replaced execution context is not a failed login.
        try:
            value = self._evaluate(runtime, page, expression)
            # Origin can change between /json/list and Runtime.evaluate. Wait
            # for a fresh trusted target snapshot instead of flagging a normal
            # Google -> Dola redirect as a page error; no actions run here.
            return None if isinstance(value, dict) and value.get('trusted') is False else value
        except TRANSIENT_BROWSER_ERRORS:
            return None

    def _act(self, runtime, page, expression, uncertain):
        try:
            return self._evaluate(runtime, page, expression)
        except TRANSIENT_BROWSER_ERRORS:
            # The click may have reached Chromium before the target vanished.
            # Treat it as attempted and observe the next page; never replay a
            # password/consent click merely because its reply was lost.
            return uncertain

    def _confirm(self, pid, runtime, generation, pages, job=None):
        eligible = []
        for page in pages:
            if not trusted_dola(page.get('url', '')):
                continue
            gate = self._observe(runtime, page, DOLA_OBSERVE)
            if gate is None:
                return False
            if not isinstance(gate, dict) or not gate.get('trusted'):
                continue
            if gate.get('manual'):
                return False
            eligible.append(page)
        for page in eligible:
            observation = self._observe(runtime, page, OBSERVE_PAGE)
            if not isinstance(observation, dict):
                continue
            status, reason = classify_observation(observation)
            if status == 'logged_in':
                with self.worker.lock:
                    if not self._valid(pid, runtime, generation) or job and job['cancel'].is_set():
                        return False
                    result = dict(checked_result(status, reason), source='dola_visible_page')
                    self.worker.accounts.record_check(pid, 'account', result)
                    self.worker.account_checks[pid] = result
                    self.worker.session_backups.after_login(pid)
                return True
        return False

    def verify(self, pid):
        with self.worker.lock:
            self.worker.profile(pid)
            runtime = self.worker.runtime.get(pid)
            if not runtime or runtime['process'].poll() is not None:
                raise self.error(409, '请先打开浏览器，再验证登录结果。')
            job = self.jobs.get(pid)
            if job and job['state'] in self.ACTIVE:
                raise self.error(409, '自动登录仍在运行，请等待或先停止。')
            generation = self.worker.accounts.generations.get(pid, 0)
        try:
            target = self.worker.target(runtime)
            allowed = {target['id'], job.get('targetId') if job else None}
            confirmed = self._confirm(pid, runtime, generation, [p for p in self._pages(runtime) if p['id'] in allowed])
        except Exception:
            raise self.error(502, '登录结果暂时无法读取，请等待页面加载后重试。') from None
        if confirmed and job:
            self._update(pid, job, 'completed', 'logged_in')
        return {'confirmed': confirmed, 'login': self.snapshot(pid)}

    def _run(self, pid, job, runtime, generation, email, password, resume_target=None):
        submitted = set()
        onboarding_clicked = set()
        options = job.get('options', {})
        clicked_login = clicked_google = False
        entry_attempts = {'login': 0, 'google': 0}
        entry_last = {'login': float('-inf'), 'google': float('-inf')}
        google_target = resume_target
        try:
            if job['cancel'].is_set() or not self._valid(pid, runtime, generation):
                self._update(pid, job, 'cancelled', 'cancelled'); return
            pages = self._pages(runtime)
            initial = self.worker.target(runtime)
            if job.get('initialTargetId'):
                bound = next((p for p in pages if p['id'] == job['initialTargetId']), None)
                bound = bound or next((p for p in pages if p['id'] == resume_target), None)
                if not bound:
                    self._update(pid, job, 'needs_attention', 'page_changed'); return
                initial = bound
            job['initialTargetId'] = initial['id']
            initial_ids = {p['id'] for p in pages}
            if google_target and not any(p['id'] == google_target and (trusted_google(p.get('url', '')) or trusted_dola(p.get('url', ''))) for p in pages):
                google_target = None
            if google_target:
                job['targetId'] = google_target
            if self._confirm(pid, runtime, generation, [p for p in pages if p['id'] in (initial['id'], google_target)], job):
                # A configured binding is not proof of the website's existing
                # identity. Never present a skipped login as an account switch.
                if job.get('loginAccountId') and not job.get('oauthStarted'):
                    self._update(pid, job, 'needs_attention', 'existing_session_unverified')
                else:
                    self._update(pid, job, 'completed', 'logged_in' if job.get('oauthStarted') else 'already_logged_in')
                return
            if not trusted_dola(initial.get('url', '')) and not trusted_google(initial.get('url', '')):
                # Do not navigate an unrelated document or discard an unsaved draft.
                self._update(pid, job, 'needs_attention', 'open_dola')
                return
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if job['cancel'].is_set():
                    self._update(pid, job, 'cancelled', 'cancelled'); return
                if not self._valid(pid, runtime, generation):
                    self._update(pid, job, 'cancelled', 'session_changed'); return
                try:
                    pages = self._pages(runtime)
                except TRANSIENT_BROWSER_ERRORS:
                    self._update(pid, job, 'running', 'waiting_dola' if google_target else 'loading_dola')
                    job['cancel'].wait(.5); continue
                if self._confirm(pid, runtime, generation, [p for p in pages if p['id'] in (initial['id'], google_target)], job):
                    self._update(pid, job, 'completed', 'logged_in'); return
                candidates = [p for p in pages if trusted_google(p.get('url', ''))]
                if google_target is None:
                    # Only act on a popup opened by this Dola login, or on the
                    # main target when it navigated to Google's official origin.
                    candidates = [p for p in candidates if p['id'] == initial['id'] or clicked_google and p['id'] not in initial_ids]
                    if len(candidates) > 1:
                        self._update(pid, job, 'needs_attention', 'multiple_google_windows'); return
                    if candidates:
                        google_target = candidates[0]['id']
                        job['targetId'] = google_target
                google = next((p for p in candidates if p['id'] == google_target), None)
                if google:
                    observation = self._observe(runtime, google, GOOGLE_OBSERVE)
                    if observation is None:
                        self._update(pid, job, 'running', 'waiting_google'); job['cancel'].wait(.5); continue
                    if not isinstance(observation, dict) or not observation.get('trusted'):
                        self._update(pid, job, 'needs_attention', 'page_changed'); return
                    if observation.get('error'):
                        self._update(pid, job, 'needs_attention', 'google_rejected'); return
                    if observation.get('challenge'):
                        self._update(pid, job, 'needs_attention', 'verification_required'); return
                    onboarding = 'welcome' if observation.get('welcome') else 'consent' if observation.get('consent') else None
                    if onboarding:
                        if observation.get('ready') not in (None, 'complete'):
                            self._update(pid, job, 'running', 'waiting_google'); job['cancel'].wait(1); continue
                        if not options.get('allowOnboarding'):
                            self._update(pid, job, 'needs_attention', 'workspace_welcome_required' if onboarding == 'welcome' else 'consent_required'); return
                        self._update(pid, job, 'running', 'confirming_workspace' if onboarding == 'welcome' else 'authorizing_dola')
                        if onboarding not in onboarding_clicked:
                            if job['cancel'].is_set() or not self._valid(pid, runtime, generation): continue
                            result = self._act(runtime, google, onboarding_expression(onboarding, email), {'clicked': True})
                            if isinstance(result, dict) and result.get('reason') == 'not_ready':
                                self._update(pid, job, 'running', 'waiting_google'); job['cancel'].wait(1); continue
                            if not isinstance(result, dict) or not result.get('clicked'):
                                self._update(pid, job, 'needs_attention', (result or {}).get('reason', 'page_changed')); return
                            onboarding_clicked.add(onboarding)
                        job['cancel'].wait(1); continue
                    if observation.get('chooser'):
                        self._update(pid, job, 'needs_attention', 'choose_account'); return
                    stage = 'email' if observation.get('email') else 'password' if observation.get('password') else None
                    if stage and password is None:
                        self._update(pid, job, 'needs_attention', 'credentials_required'); return
                    if stage and stage not in submitted:
                        if job['cancel'].is_set() or not self._valid(pid, runtime, generation):
                            continue
                        self._update(pid, job, 'running', 'submitting_' + stage)
                        response = self._act(runtime, google, google_submit_expression(stage, email if stage == 'email' else password, email), {'submitted': True})
                        if response and response.get('submitted'):
                            submitted.add(stage)
                            job['oauthStarted'] = True
                        elif response and response.get('reason') in ('account_mismatch', 'verification_required', 'consent_required', 'workspace_welcome_required', 'choose_account', 'google_rejected'):
                            self._update(pid, job, 'needs_attention', response['reason']); return
                    elif stage and stage in submitted:
                        # No repeated password submissions; allow animation and
                        # then wait for a new page, rejection or deadline.
                        self._update(pid, job, 'running', 'waiting_google')
                    else:
                        self._update(pid, job, 'running', 'waiting_google')
                else:
                    dola = next((p for p in pages if p['id'] == google_target and trusted_dola(p.get('url', ''))), None)
                    dola = dola or next((p for p in pages if p['id'] == initial['id'] and trusted_dola(p.get('url', ''))), None)
                    if not dola:
                        if google_target is not None:
                            self._update(pid, job, 'running', 'waiting_dola'); job['cancel'].wait(1); continue
                        self._update(pid, job, 'needs_attention', 'page_changed'); return
                    account = self._observe(runtime, dola, OBSERVE_PAGE)
                    if isinstance(account, dict) and classify_observation(account)[0] == 'region_blocked':
                        self._update(pid, job, 'needs_attention', 'region_blocked'); return
                    view = self._observe(runtime, dola, DOLA_OBSERVE)
                    if view is None:
                        self._update(pid, job, 'running', 'waiting_dola' if google_target else 'loading_dola'); job['cancel'].wait(.5); continue
                    if isinstance(view, dict) and view.get('trusted') and view.get('age') and not view.get('security'):
                        if not options.get('ageConfirmed'):
                            self._update(pid, job, 'needs_attention', 'age_confirmation_required'); return
                        self._update(pid, job, 'running', 'confirming_age')
                        if 'age' not in onboarding_clicked:
                            if job['cancel'].is_set() or not self._valid(pid, runtime, generation): continue
                            result = self._act(runtime, dola, onboarding_expression('age', email), {'clicked': True})
                            if not isinstance(result, dict) or not result.get('clicked'):
                                self._update(pid, job, 'needs_attention', (result or {}).get('reason', 'age_confirmation_required')); return
                            onboarding_clicked.add('age')
                        job['cancel'].wait(1); continue
                    if not isinstance(view, dict) or not view.get('trusted') or view.get('manual'):
                        self._update(pid, job, 'needs_attention', 'dola_confirmation'); return
                    if google_target is not None:
                        self._update(pid, job, 'running', 'waiting_dola'); job['cancel'].wait(1); continue
                    now = time.monotonic()
                    # A DOM click is not proof that React handled it. Recheck the
                    # same exposed entry after a grace period, at most 3 times.
                    # New blank popups may still be navigating to Google: wait
                    # rather than opening a second provider window.
                    pending_popup = any(p['id'] not in initial_ids and p.get('url', '') in ('', 'about:blank') for p in pages)
                    for kind in ('google', 'login'):
                        if kind == 'login' and clicked_google:
                            break
                        if pending_popup or entry_attempts[kind] >= 3 or now - entry_last[kind] < 4:
                            continue
                        if job['cancel'].is_set() or not self._valid(pid, runtime, generation):
                            break
                        # Snapshot before the first provider click, after any
                        # unrelated windows opened while Dola was still loading.
                        # Never absorb our own popup into this baseline on retry.
                        before_click_ids = {p['id'] for p in pages} if kind == 'google' and not clicked_google else None
                        attempted = self._act(runtime, dola, dola_click_expression(kind), True) is True
                        if attempted:
                            entry_attempts[kind] += 1
                            entry_last[kind] = now
                            if kind == 'google':
                                if before_click_ids is not None:
                                    initial_ids = before_click_ids
                                clicked_google = True
                                job['oauthStarted'] = True
                            else:
                                clicked_login = True
                            break
                    if entry_attempts['google'] >= 3 and now - entry_last['google'] >= 4:
                        # Stop clicking but keep observing within this job's
                        # deadline, so a manual provider click can still finish
                        # with the credentials held only by this running call.
                        self._update(pid, job, 'running', 'google_entry_waiting_manual')
                    else:
                        self._update(pid, job, 'running', 'waiting_google' if clicked_google else 'locating_google' if clicked_login else 'loading_dola')
                job['cancel'].wait(.5)
            self._update(pid, job, 'needs_attention', 'google_entry_unresponsive' if google_target is None and entry_attempts['google'] >= 3 else 'timeout')
        except Exception:
            # Exception strings may contain script source/passwords; never log
            # or return them. The user can inspect the visible browser instead.
            self._update(pid, job, 'cancelled' if job['cancel'].is_set() else 'failed', 'cancelled' if job['cancel'].is_set() else 'connection_failed')
        finally:
            email = password = None

    def close(self):
        self.closed = True
        for job in list(self.jobs.values()):
            job['cancel'].set()

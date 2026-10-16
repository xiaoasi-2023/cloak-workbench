"""Owned-tab Dola video jobs. Frontend signs requests; never replay submissions."""
import base64
import copy
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from websockets.sync.client import connect
from google_login import trusted_dola

MODELS = ('seedance_v2.0', 'seedance_v2.5')
RATIOS = ('16:9', '9:16', '1:1', '4:3', '3:4', '21:9')
ACTIVE = ('preparing', 'running', 'needs_attention', 'cancelling')


def validate_video(data):
    model, duration, ratio = data.get('model'), data.get('duration'), data.get('ratio')
    prompt, pid = data.get('prompt'), data.get('profileId')
    if model not in MODELS or type(duration) is not int or duration not in (5, 10, 15, 30) or ratio not in RATIOS:
        raise ValueError('请选择有效的模型、时长和比例。')
    if duration == 30 and model != 'seedance_v2.5':
        raise ValueError('30 秒请明确选择 Seedance 2.5；不会自动替换模型。')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError('请输入 1～4000 字的提示词。')
    if not isinstance(pid, str) or not pid:
        raise ValueError('请选择已登录 Dola 的浏览器环境。')
    return dict(profileId=pid, model=model, duration=duration, ratio=ratio, prompt=prompt.strip().replace('\r\n', '\n').replace('\r', '\n'))


def patch_config(value, config, depth=0):
    """Extend only video selectors, preserving live server config and other skills.

    Handles both skill-pack options and nested action-bar JSON strings. Based on
    the two response shapes documented by dola-pool/extensions/dola30.
    """
    if depth > 24:
        return value
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
            patched = patch_config(parsed, config, depth + 1)
            return json.dumps(patched, ensure_ascii=False, separators=(',', ':')) if patched != parsed else value
        except (ValueError, TypeError):
            return value
    if isinstance(value, list):
        return [patch_config(v, config, depth + 1) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: patch_config(v, config, depth + 1) for k, v in value.items()}
    key = str(result.get('key') or result.get('value') or '')
    for option_key in ('options', 'option_list'):
        options = result.get(option_key)
        if not isinstance(options, list) or not options or not all(isinstance(o, dict) for o in options):
            continue
        wanted = None
        if key in ('video-duration', 'duration'):
            wanted = str(config['duration'])
        elif key in ('model', 'video-model'):
            # Do not invent a model entry in unrelated selectors.
            if not any(str(o.get('value', o.get('option_key', ''))).startswith('seedance_') for o in options):
                continue
            wanted = config['model']
        elif key in ('ratio', 'video-ratio'):
            wanted = config['ratio']
        if wanted is None:
            continue
        field = 'option_key' if any('option_key' in o for o in options) else 'value'
        if not any(str(o.get(field)) == wanted for o in options):
            added = copy.deepcopy(options[-1])
            added.update({field: wanted, 'is_default': False})
            title = wanted + 's' if 'duration' in key else 'Dreamina Seedance 2.5' if wanted == MODELS[1] else wanted
            for label in ('show_name', 'display_text'):
                if label in added:
                    added[label] = title
            if 'id' in added:
                added['id'] = max((o.get('id', 0) for o in options if type(o.get('id', 0)) is int), default=0) + 1
            if 'sub_display' in added:
                added['sub_display'] = title
            options.append(added)
        for option in options:
            if 'is_default' in option:
                option['is_default'] = str(option.get(field)) == wanted
    capabilities = result.get('model_capability')
    if isinstance(capabilities, dict) and 'seedance_v2.0' in capabilities:
        selected = capabilities.setdefault(config['model'], copy.deepcopy(capabilities['seedance_v2.0']))
        durations = selected.get('supported_durations')
        if isinstance(durations, list) and str(config['duration']) not in durations:
            durations.append(str(config['duration']))
    return result


def check_submission(body, config):
    """Fail closed before transmission; never rewrite the signed request body."""
    data = json.loads(body)
    ability = data.get('chat_ability', {})
    params = ability.get('ability_param', {})
    if isinstance(params, str):
        params = json.loads(params)
    if ability.get('ability_type') != 17 or params.get('model') != config['model'] or str(params.get('duration')) != str(config['duration']) or params.get('ratio') != config['ratio']:
        raise ValueError('网页实际生成参数与面板不一致，已拦截提交。请检查模型、时长和比例。')
    def strings(node, depth=0):
        if depth > 24:
            return
        if isinstance(node, str):
            yield node
            if node.lstrip().startswith(('{', '[')):
                try:
                    yield from strings(json.loads(node), depth + 1)
                except ValueError:
                    pass
        elif isinstance(node, list):
            for child in node:
                yield from strings(child, depth + 1)
        elif isinstance(node, dict):
            for child in node.values():
                yield from strings(child, depth + 1)
    if not any(config['prompt'] in s for s in strings(data.get('messages', []))):
        raise ValueError('网页提示词与当前任务不一致，已拦截提交。')


def duration_result(actual, requested):
    if type(actual) not in (float, int) or not math.isfinite(actual) or actual <= 0:
        return None
    return ('completed', '生成完成，实际时长已核验。') if abs(actual - requested) <= 1 else ('duration_mismatch', '已生成，但实际时长与请求不一致；没有重试或拼接。')


def media_urls(value, depth=0):
    """Only ordinary playable video URLs; encrypted reference formats stay manual."""
    if depth > 24:
        return []
    urls = []
    if isinstance(value, dict):
        for key in ('main_url', 'download_url', 'play_url'):
            url = value.get(key)
            if not isinstance(url, str):
                continue
            if not url.startswith('https://'):
                try:
                    url = base64.b64decode(url, validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    continue
            try:
                parsed = urlsplit(url)
                if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
                    urls.append(url)
            except ValueError:
                pass
        for child in value.values():
            urls.extend(media_urls(child, depth + 1))
    elif isinstance(value, list):
        for child in value:
            urls.extend(media_urls(child, depth + 1))
    elif isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            urls.extend(media_urls(json.loads(value), depth + 1))
        except ValueError:
            pass
    return list(dict.fromkeys(urls))


class PageCDP:
    def __init__(self, url, event):
        self.ws = connect(url, proxy=None, open_timeout=4, close_timeout=1, max_size=16 * 1024 * 1024)
        self.event, self.counter, self.replies = event, 0, {}

    def read(self, timeout):
        msg = json.loads(self.ws.recv(timeout=timeout))
        if 'id' in msg:
            self.replies[msg['id']] = msg
        elif 'method' in msg:
            self.event(self, msg['method'], msg.get('params', {}))

    def call(self, method, params=None):
        self.counter += 1
        request = self.counter
        self.ws.send(json.dumps(dict(id=request, method=method, params=params or {})))
        deadline = time.monotonic() + 10
        while request not in self.replies:
            self.read(max(.01, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise TimeoutError('浏览器响应超时。')
        result = self.replies.pop(request)
        if result.get('error'):
            raise RuntimeError('浏览器命令未完成：' + method)
        return result.get('result', {})

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', dict(expression=expression, returnByValue=True))
        if result.get('exceptionDetails'):
            raise RuntimeError('网页状态发生变化，请打开任务页面检查。')
        return result.get('result', {}).get('value')


class VideoTasks:
    def __init__(self, worker, error):
        self.worker, self.error = worker, error
        self.path = worker.data_dir / 'video-tasks.json'
        self.jobs, self.stops, self.threads = {}, {}, []
        self.script = Path(__file__).with_name('video_page.js').read_text(encoding='utf-8')
        if self.path.is_file():
            for job in json.loads(self.path.read_text(encoding='utf-8')):
                if job['state'] in ACTIVE:
                    job.update(state='interrupted', stage='软件已退出；任务未自动重试，请打开原会话检查。')
                self.jobs[job['id']] = job

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(list(self.jobs.values()), ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def busy(self, pid):
        return any(j['profileId'] == pid and j['state'] in ACTIVE for j in self.jobs.values())

    def list(self):
        with self.worker.lock:
            return {'tasks': copy.deepcopy(list(reversed(self.jobs.values())))}

    def entry(self, tid):
        if tid not in self.jobs:
            raise self.error(404, '视频任务不存在。')
        return self.jobs[tid]

    def update(self, job, **values):
        with self.worker.lock:
            if job['state'] == 'cancelled' or all(job.get(k) == v for k, v in values.items()):
                return
            job.update(values, updatedAt=time.time())
            self.save()

    def start(self, payload):
        try:
            config = validate_video(payload)
        except ValueError as ex:
            raise self.error(400, str(ex)) from None
        with self.worker.lock:
            pid = config['profileId']
            profile = self.worker.profile(pid)
            rt = self.worker.runtime.get(pid)
            if not rt or rt['process'].poll() is not None:
                raise self.error(409, '请先打开此浏览器环境并登录 Dola。')
            login = self.worker.google_login.jobs.get(pid, {})
            if self.busy(pid) or login.get('state') in (*self.worker.google_login.ACTIVE, 'needs_attention'):
                raise self.error(409, '此环境已有视频或登录任务，请先完成或停止。')
            if sum(j['state'] in ACTIVE for j in self.jobs.values()) >= 3:
                raise self.error(409, '最多同时处理三个视频任务。')
            if len(self.jobs) >= 200:
                raise self.error(409, '视频任务记录已达 200 条，请先删除不需要的历史记录。')
            tid = uuid.uuid4().hex[:12]
            job = dict(config, id=tid, profileName=profile['name'], state='preparing', stage='正在打开专用视频会话…',
                       createdAt=time.time(), updatedAt=time.time(), submitted=False, actualDuration=None, pageUrl='', targetId='')
            self.jobs[tid], self.stops[tid] = job, threading.Event()
            self.save()
            thread = threading.Thread(target=self.run, args=(job, rt), daemon=True)
            self.threads.append(thread)
            thread.start()
            return copy.deepcopy(job)

    def cancel(self, tid):
        with self.worker.lock:
            job = self.entry(tid)
            if job['state'] in ACTIVE:
                self.stops[tid].set()
                job.update(state='cancelled', stage='已停止本地监测；已经提交的生成可能仍在 Dola 继续，不代表取消或退还额度。', updatedAt=time.time())
                self.save()
            return copy.deepcopy(job)

    def delete(self, tid):
        with self.worker.lock:
            job = self.entry(tid)
            if job['state'] in ACTIVE:
                raise self.error(409, '请先停止此视频任务。')
            del self.jobs[tid]
            self.save()
            return {'deleted': True}

    def open(self, tid):
        with self.worker.lock:
            job = self.entry(tid)
            rt = self.worker.runtime.get(job['profileId'])
            if not rt or rt['process'].poll() is not None:
                raise self.error(409, '请先打开任务所属环境。')
            pages = self.worker.cdp_json(rt['cdpPort'], '/json/list')
            page = next((p for p in pages if p['id'] == job.get('targetId')), None)
            if not page:
                if job['state'] in ACTIVE or not trusted_dola(job.get('pageUrl', '')):
                    raise self.error(409, '任务标签页已关闭，且没有可恢复的生成会话。')
                if job.get('submitted') and urlsplit(job['pageUrl']).path.rstrip('/') == '/chat':
                    raise self.error(409, '未取得原生成会话地址；请在 Dola 历史记录确认，避免重复生成。')
                created = self.worker.command(rt, 'Target.createTarget', {'url': job['pageUrl']}, browser=True)
                job['targetId'] = created['targetId']
                self.save()
            self.worker.command(rt, 'Target.activateTarget', {'targetId': job['targetId']}, browser=True)
            rt['targetId'] = job['targetId']
            return {'profileId': job['profileId']}

    def run(self, job, rt):
        page = None
        stop = self.stops[job['id']]
        submitted = False
        blocked = ''
        candidate_urls = []
        conversation = ''
        def response_body(cdp, rid):
            reply = cdp.call('Fetch.getResponseBody', {'requestId': rid})
            return base64.b64decode(reply['body']).decode('utf-8') if reply.get('base64Encoded') else reply['body']
        def event(cdp, method, p):
            nonlocal submitted, blocked
            if method != 'Fetch.requestPaused':
                return
            request, rid = p.get('request', {}), p['requestId']
            url = request.get('url', '')
            path = urlsplit(url).path
            if trusted_dola(url) and path.rstrip('/') == '/chat/completion' and p.get('responseStatusCode', 0) >= 400:
                blocked = '网页实际生成请求返回 HTTP ' + str(p['responseStatusCode']) + '；没有自动重试，请打开原会话确认。'
                cdp.call('Fetch.continueRequest', {'requestId': rid})
                return
            if trusted_dola(url) and path == '/im/chain/single' and p.get('responseStatusCode') == 200 and submitted and conversation:
                try:
                    query = json.loads(request.get('postData', '{}'))
                    if str(query.get('conversation_id', '')) == conversation:
                        candidate_urls.extend(u for u in media_urls(json.loads(response_body(cdp, rid))) if u not in candidate_urls)
                except (ValueError, UnicodeError, TypeError):
                    pass
                cdp.call('Fetch.continueRequest', {'requestId': rid})
                return
            if trusted_dola(url) and path.rstrip('/') == '/chat/completion' and 'responseStatusCode' not in p:
                try:
                    if stop.is_set() or submitted or job['state'] not in ACTIVE:
                        raise ValueError('重复或已停止的提交已拦截；没有自动重试。')
                    check_submission(request.get('postData', ''), job)
                except (ValueError, TypeError, AttributeError):
                    if not submitted:
                        blocked = '网页实际参数未通过核对，已拦截请求；没有提交生成。'
                    cdp.call('Fetch.failRequest', {'requestId': rid, 'errorReason': 'Aborted'})
                    return
                # Persist intent BEFORE transmission: a lost response is never retried.
                submitted = True
                self.update(job, state='running', stage='请求参数已核对，正在生成视频…', submitted=True)
                cdp.call('Fetch.continueRequest', {'requestId': rid})
                return
            if trusted_dola(url) and path in ('/samantha/skill/pack', '/alice/slot/action_bar_v3/get_item_conf') and p.get('responseStatusCode') == 200:
                try:
                    raw = response_body(cdp, rid)
                    config = json.loads(raw)
                    if path == '/samantha/skill/pack':
                        video = config.get('data', {}).get('video_generation')
                        if isinstance(video, dict):
                            config['data']['video_generation'] = patch_config(video, job)
                    else:
                        config = patch_config(config, job)
                    patched = json.dumps(config, ensure_ascii=False).encode('utf-8')
                    headers = [h for h in p.get('responseHeaders', []) if h['name'].lower() not in ('content-length', 'content-encoding')]
                    cdp.call('Fetch.fulfillRequest', dict(requestId=rid, responseCode=200, responseHeaders=headers, body=base64.b64encode(patched).decode('ascii')))
                    return
                except (ValueError, UnicodeError, KeyError):
                    pass
            cdp.call('Fetch.continueRequest', {'requestId': rid})
        try:
            with self.worker.lock:
                if stop.is_set():
                    return
                target = self.worker.command(rt, 'Target.createTarget', {'url': 'about:blank'}, browser=True)['targetId']
                self.update(job, targetId=target)
            targets = self.worker.cdp_json(rt['cdpPort'], '/json/list')
            ws = next(p['webSocketDebuggerUrl'] for p in targets if p['id'] == target)
            page = PageCDP(ws, event)
            # Native embedding hides the Chromium window on management pages.
            # Keep only the task tab active so metadata loads without UI switching.
            page.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
            page.call('Fetch.enable', {'patterns': [
                {'urlPattern': '*://*.dola.com/samantha/skill/pack*', 'requestStage': 'Response'},
                {'urlPattern': '*://*.dola.com/alice/slot/action_bar_v3/get_item_conf*', 'requestStage': 'Response'},
                {'urlPattern': '*://*.dola.com/chat/completion*', 'requestStage': 'Request'},
                {'urlPattern': '*://*.dola.com/chat/completion*', 'requestStage': 'Response'},
                {'urlPattern': '*://*.dola.com/im/chain/single*', 'requestStage': 'Response'},
                {'urlPattern': '*://dola.com/samantha/skill/pack*', 'requestStage': 'Response'},
                {'urlPattern': '*://dola.com/alice/slot/action_bar_v3/get_item_conf*', 'requestStage': 'Response'},
                {'urlPattern': '*://dola.com/chat/completion*', 'requestStage': 'Request'},
                {'urlPattern': '*://dola.com/chat/completion*', 'requestStage': 'Response'},
                {'urlPattern': '*://dola.com/im/chain/single*', 'requestStage': 'Response'}]})
            page.call('Page.navigate', {'url': 'https://www.dola.com/chat/'})
            deadline, prepare_deadline = time.monotonic() + 2100, time.monotonic() + 65
            entered, filled, pressed = False, False, False
            chosen = set()
            while not stop.is_set() and time.monotonic() < deadline:
                if rt['process'].poll() is not None:
                    raise RuntimeError('环境浏览器已关闭；没有自动重新提交。')
                if not submitted and time.monotonic() > prepare_deadline:
                    raise RuntimeError('未能完成网页配置或提交；没有自动重试，请打开任务页面检查。')
                try:
                    state = page.evaluate(self.expression('observe', job)) or {}
                except RuntimeError:
                    stop.wait(.5)
                    continue
                if not state.get('trusted'):
                    if state.get('ready') == 'complete' and state.get('url', '').startswith('https://accounts.google.com'):
                        raise RuntimeError('需要先登录 Dola，请完成登录后重新开始任务。')
                    if submitted and state.get('ready') == 'complete':
                        raise RuntimeError('环境浏览器已离开 Dola；已停止监测，没有重新提交。')
                    stop.wait(.5)
                    continue
                if state.get('pageUrl') and state['pageUrl'] != job['pageUrl']:
                    self.update(job, pageUrl=state['pageUrl'])
                if blocked:
                    raise RuntimeError(blocked)
                reason = state.get('problem')
                if reason:
                    if reason == '需要手动完成安全验证。':
                        self.update(job, state='needs_attention', stage=reason + ' 打开任务页面处理后会继续监测；不会自动重发请求。')
                        if not submitted:
                            prepare_deadline = time.monotonic() + 65
                        stop.wait(1)
                        continue
                    raise RuntimeError(reason)
                if job['state'] == 'needs_attention' and not submitted:
                    self.update(job, state='preparing', stage='验证已结束，继续准备视频表单…')
                if submitted:
                    match = re.fullmatch(r'/chat/(\d+)/?', urlsplit(state.get('pageUrl', '')).path)
                    if match:
                        if conversation and match[1] != conversation:
                            raise RuntimeError('环境浏览器切换了生成会话；已停止监测，原任务没有重新提交。')
                        conversation = match[1]
                    if job['state'] == 'needs_attention':
                        self.update(job, state='running', stage='验证已结束，继续等待结果；没有重发请求。')
                    actual = state.get('duration')
                    outcome = duration_result(actual, job['duration'])
                    if outcome:
                        self.update(job, state=outcome[0], stage=outcome[1], actualDuration=round(actual, 3))
                        return
                    if candidate_urls:
                        url = candidate_urls.pop(0)
                        probe = r'''(() => {
                          if(location.protocol!=='https:'||!(location.hostname==='dola.com'||location.hostname.endsWith('.dola.com'))||location.pathname!==PATH)return;
                          const id=ID; let v=document.getElementById(id);
                          if(v&&v.readyState>0)return;
                          if(v)v.remove();v=document.createElement('video');v.id=id;v.hidden=true;v.preload='metadata';v.src=URL;document.body.appendChild(v);v.load();
                        })()'''.replace('PATH', json.dumps(urlsplit(state.get('pageUrl', '')).path)).replace('ID', json.dumps('cloak-video-' + job['id'])).replace('URL', json.dumps(url))
                        page.evaluate(probe)
                        self.update(job, stage='已收到视频，正在读取实际成片时长…')
                elif not pressed:
                    if time.monotonic() > prepare_deadline:
                        raise RuntimeError('未能安全识别网页视频表单；请打开任务页面检查登录、地区或模型可用性。没有提交生成。')
                    if not entered:
                        entered = bool(page.evaluate(self.expression('enter', job)))
                        if entered:
                            self.update(job, stage='正在配置模型、时长和画面比例…')
                    elif len(chosen) < 3:
                        for name in ('model', 'duration', 'ratio'):
                            if name in chosen:
                                continue
                            if page.evaluate(self.expression('choose_' + name, job)):
                                chosen.add(name)
                            break
                        # Live config defaults can already be correct even when a
                        # localized selector is unknown. The request guard is final.
                        if time.monotonic() > prepare_deadline - 20:
                            chosen.update(('model', 'duration', 'ratio'))
                    elif not filled:
                        filled = bool(page.evaluate(self.expression('fill', job)))
                    else:
                        # Never issue a second Enter if no response arrives.
                        pressed = True
                        self.update(job, stage='正在提交；实际参数将再次核对…')
                        page.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
                        page.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
                        prepare_deadline = time.monotonic() + 35
                elif not submitted and time.monotonic() > prepare_deadline:
                    raise RuntimeError('未观察到符合参数的生成请求；未自动重试，请打开任务页面确认。')
                stop.wait(.7)
            if not stop.is_set():
                self.update(job, state='timed_out', stage='等待结果超时；上游可能仍在生成，请打开原会话查看，不会自动重试。')
        except Exception as ex:
            # Never persist a raw browser/network exception (URLs can hold tokens).
            message = str(ex) if isinstance(ex, RuntimeError) and str(ex).startswith(('需要', '未能', '环境浏览器', '网页实际', '未观察')) else '浏览器连接或页面发生变化；没有自动重试，请打开原会话确认结果。'
            self.update(job, state='failed', stage=message)
        finally:
            if page:
                # Before detaching interception, close an unfinished task tab so
                # frontend timers cannot retry an aborted request unguarded.
                if stop.is_set() or job['state'] not in ('completed', 'duration_mismatch'):
                    try:
                        with self.worker.lock:
                            self.worker.command(rt, 'Target.closeTarget', {'targetId': job['targetId']}, browser=True)
                    except Exception:
                        pass
                try:
                    page.call('Fetch.disable')
                    page.call('Emulation.setFocusEmulationEnabled', {'enabled': False})
                except Exception:
                    pass
                page.ws.close()

    def expression(self, action, job):
        return '(' + self.script + ')(' + json.dumps(action) + ',' + json.dumps({k: job[k] for k in ('model', 'duration', 'ratio', 'prompt')}) + ')'

    def close(self):
        for stop in self.stops.values():
            stop.set()
        # Never join here: shutdown holds worker.lock; update/save takes that lock.

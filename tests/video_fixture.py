"""Offline transport fixture: real Chromium, CDP, DOM and video metadata.

Every HTTP request is intercepted. Never connects to Dola or consumes credits.
"""
import base64
import json
from pathlib import Path
import subprocess

from video_tasks import PageCDP


def make_clip(directory, seconds):
    ffmpeg = Path.home() / 'AppData/Local/ms-playwright/ffmpeg-1011/ffmpeg-win64.exe'
    if not ffmpeg.is_file():
        raise RuntimeError('The installed Playwright ffmpeg is required for offline media verification')
    frame = Path(__file__).with_name('video-frame.jpg').read_bytes()
    dest = Path(directory) / f'fixture-{seconds}.webm'
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-f', 'image2pipe', '-c:v', 'mjpeg', '-framerate', '1', '-i', 'pipe:0',
                             '-c:v', 'libvpx', '-y', str(dest)], input=frame * seconds, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace'))
    return 'data:video/webm;base64,' + base64.b64encode(dest.read_bytes()).decode('ascii')


class OfflineVideoPage(PageCDP):
    clips = {}
    accepted = []
    denied = []
    documents = []
    samples = []
    def __init__(self, url, event):
        self.production_event = event
        self.synthetic = {}
        self.pending = {}
        self.canned = {}
        super().__init__(url, self.offline_event)

    def fulfill(self, rid, body, content='application/json'):
        return super().call('Fetch.fulfillRequest', {'requestId': rid, 'responseCode': 200,
            'responseHeaders': [{'name': 'Content-Type', 'value': content}, {'name': 'Cache-Control', 'value': 'no-store'}],
            'body': base64.b64encode(body.encode('utf-8')).decode('ascii')})

    def offline_event(self, cdp, method, params):
        if method != 'Fetch.requestPaused':
            return self.production_event(cdp, method, params)
        request, rid = params['request'], params['requestId']
        url = request['url']
        if '/chat/completion' in url:
            self.pending[rid] = request['postData']
            return self.production_event(cdp, method, params)
        if '/samantha/skill/pack' in url:
            config = {'data': {'video_generation': {'meta': {'option_list': [
                {'value': 'duration', 'options': [{'value': '10', 'show_name': '10s', 'is_default': True}]},
                {'value': 'model', 'options': [{'value': 'seedance_v2.0', 'show_name': 'Seedance2.0Fast', 'is_default': True}]},
                {'value': 'ratio', 'options': [{'value': '16:9', 'show_name': '16:9', 'is_default': True}]}],
                'model_capability': {'seedance_v2.0': {'supported_durations': ['5', '10']}}}}}}
            self.synthetic[rid] = {'body': json.dumps(config), 'base64Encoded': False}
            return self.production_event(cdp, method, dict(params, responseStatusCode=200, responseHeaders=[{'name': 'Content-Type', 'value': 'application/json'}]))
        if '/im/chain/single' in url:
            body = json.dumps({'data': {'content': json.dumps({'video': {'main_url': base64.b64encode(b'https://media.example/v30.webm').decode('ascii')}})}})
            self.synthetic[rid] = {'body': body, 'base64Encoded': False}
            self.canned[rid] = body
            return self.production_event(cdp, method, dict(params, responseStatusCode=200))
        if url == 'https://media.example/v30.webm':
            return super().call('Fetch.fulfillRequest', {'requestId': rid, 'responseCode': 200,
                'responseHeaders': [{'name': 'Content-Type', 'value': 'video/webm'}], 'body': self.clips[30].split(',', 1)[1]})
        if url == 'https://www.dola.com/chat/':
            self.documents.append(url)
            html = r'''<!doctype html><meta charset="utf-8"><style>body{background:#18211c;color:white;padding:30px}button,textarea{margin:12px;padding:10px}textarea{display:block}</style>
              <button id="enter">Create video</button><div id="form" hidden></div><main id="result"></main>
              <script>
              let params={};
              fetch('/samantha/skill/pack').then(r=>r.json()).then(j=>{
                for(const selector of j.data.video_generation.meta.option_list) params[selector.value]=selector.options.find(o=>o.is_default).value;
                document.querySelector('#enter').onclick=()=>{
                  const f=document.querySelector('#form');f.hidden=false;
                  f.innerHTML='<button>Dreamina Seedance '+params.model.slice(-3)+'</button><button>'+params.duration+'s</button><button>'+params.ratio+'</button><textarea></textarea>';
                  document.querySelector('textarea').onkeydown=async e=>{
                    if(e.key!=='Enter')return;e.preventDefault(); const prompt=e.target.value;
                    const sent={...params,duration:prompt.includes('wrong')?10:Number(params.duration)};
                    try {
                      await fetch('/chat/completion',{method:'POST',body:JSON.stringify({chat_ability:{ability_type:17,ability_param:JSON.stringify(sent)},messages:[{content_block:[{content:{text:prompt}}]}]})});
                      history.pushState({},'', '/chat/123456789');
                      document.querySelector('#result').textContent=prompt;
                      if(prompt.includes('wait'))return;
                      if(prompt.includes('chain')){setTimeout(()=>fetch('/im/chain/single',{method:'POST',body:JSON.stringify({conversation_id:'123456789'})}),1200);return;}
                      const v=document.createElement('video');v.preload='metadata';v.controls=true;v.src=prompt.includes('short')?CLIP15:CLIP30;document.querySelector('#result').appendChild(v);
                    }catch(e){document.querySelector('#result').textContent='Request blocked by test guard';}
                  };
                };
              });
              </script>'''.replace('CLIP15', json.dumps(self.clips[15])).replace('CLIP30', json.dumps(self.clips[30]))
            return self.fulfill(rid, html, 'text/html; charset=utf-8')
        return super().call('Fetch.failRequest', {'requestId': rid, 'errorReason': 'Aborted'})

    def call(self, method, params=None):
        params = params or {}
        if method == 'Fetch.enable':
            return super().call(method, {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})
        if method == 'Fetch.getResponseBody' and params['requestId'] in self.synthetic:
            return self.synthetic.pop(params['requestId'])
        if method == 'Fetch.failRequest' and params['requestId'] in self.pending:
            self.denied.append(self.pending.pop(params['requestId']))
        if method == 'Fetch.continueRequest' and params['requestId'] in self.pending:
            self.accepted.append(json.loads(self.pending.pop(params['requestId'])))
            return self.fulfill(params['requestId'], '{"code":0}')
        if method == 'Fetch.continueRequest' and params['requestId'] in self.canned:
            return self.fulfill(params['requestId'], self.canned.pop(params['requestId']))
        return super().call(method, params)

    def evaluate(self, expression):
        result = super().evaluate(expression)
        if isinstance(result, dict) and 'duration' in result and result.get('trusted'):
            info = super().evaluate("({visibility:document.visibilityState,videos:[...document.querySelectorAll('video')].map(v=>({duration:Number.isFinite(v.duration)?v.duration:null,ready:v.readyState,network:v.networkState,error:v.error?.message,source:v.src.slice(0,25)}))})")
            self.samples.append(info)
            self.samples[:] = self.samples[-8:]
        return result

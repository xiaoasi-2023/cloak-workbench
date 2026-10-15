"""Offline Dola response adapter for real Chromium session tests."""
import base64
import json
import time
from websockets.sync.client import connect


def offline_document(worker, pid, url, html):
    page = worker.target(worker.runtime[pid])
    with connect(page['webSocketDebuggerUrl'], proxy=None, open_timeout=3, close_timeout=1) as ws:
        serial = 0
        def call(method, params=None):
            nonlocal serial
            serial += 1
            request = serial
            ws.send(json.dumps({'id':request,'method':method,'params':params or {}}))
            deadline = time.monotonic()+10
            while time.monotonic()<deadline:
                reply=json.loads(ws.recv(timeout=max(.1,deadline-time.monotonic())))
                if reply.get('method')=='Fetch.requestPaused':
                    serial+=1
                    ws.send(json.dumps({'id':serial,'method':'Fetch.fulfillRequest','params':{
                        'requestId':reply['params']['requestId'],'responseCode':200,
                        'responseHeaders':[{'name':'Content-Type','value':'text/html; charset=utf-8'},
                            {'name':'Content-Security-Policy','value':"default-src 'none'; style-src 'unsafe-inline'"}],
                        'body':base64.b64encode(('<!doctype html><meta charset="utf-8">'+html).encode('utf-8')).decode('ascii')}}))
                elif reply.get('id')==request:
                    if reply.get('error') or reply.get('result',{}).get('exceptionDetails'):
                        raise RuntimeError('Offline session fixture command failed')
                    return reply.get('result',{})
            raise TimeoutError('Offline session fixture timed out')
        call('Fetch.enable',{'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
        call('Page.navigate',{'url':url})
        for _ in range(100):
            result=call('Runtime.evaluate',{'expression':'({url:location.href,ready:document.readyState})','returnByValue':True}).get('result',{}).get('value',{})
            if result.get('url')==url and result.get('ready')=='complete':
                call('Fetch.disable')
                return worker.state(pid)
            time.sleep(.02)
        raise RuntimeError('Offline session fixture did not load')

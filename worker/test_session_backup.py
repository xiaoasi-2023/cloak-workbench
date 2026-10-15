"""Offline format, encryption, rollback and API contract checks."""
import copy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

from server import BrowserWorker, ApiError, make_handler
from session_backup import SessionBackups, CAPTURE_STORAGE
from account_status import OBSERVE_PAGE

SECRET = 'offline-session-secret-中文-"-SESSION_DATA'


def fixture():
    return {'cookies':[{'name':'sessionid','value':SECRET,'domain':'.dola.com','path':'/',
                         'httpOnly':True,'secure':True,'sameSite':'Lax'},
                        {'name':'expired','value':'old','domain':'.dola.com','expires':1}],
            'storage':{'localStorage':{'fixture':SECRET},'sessionStorage':{'tab':'tab-value'},
                       'indexedDB':{'databases':[{'name':'fixture-db','version':2,'stores':[{'name':'records','keyPath':None,
                            'records':[{'key':'entry','value':{'token':SECRET}}]}]}]}}}


class SessionBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = BrowserWorker(Path(self.temp.name),Path('missing.exe'))
        self.worker.create({'id':'one','name':'Original'})
        self.manager = self.worker.session_backups

    def tearDown(self):
        self.manager.close(); self.worker.runtime.clear(); self.temp.cleanup()

    def prepare_browser(self):
        runtime={'process':SimpleNamespace(poll=lambda:None),'cdpPort':1}
        self.worker.runtime['one']=runtime
        self.worker.target=lambda _: {'id':'one-page','url':'https://www.dola.com/chat/'}
        self.worker.cdp_json=lambda *_: {}
        self.manager._call=lambda *_: {'cookies':fixture()['cookies']}
        storage=self.manager.normalize(fixture())['origins'][0]
        self.manager._evaluate=lambda _,ex: {'url':'https://www.dola.com/chat/','ready':'complete','logout':True} if ex==OBSERVE_PAGE else storage
        return runtime

    def test_formats_preserve_security_attributes_and_scope(self):
        for raw in (fixture(),fixture()['cookies'],{'cookies_list':fixture()['cookies']},
                    {'cookies':{'sessionid':{'value':SECRET,'domain':'.dola.com'}}}):
            self.assertEqual(self.manager.normalize(raw)['cookies'][0]['value'],SECRET)
        data=self.manager.normalize(fixture());cookie=data['cookies'][0]
        self.assertTrue(cookie['httpOnly']);self.assertTrue(cookie['secure']);self.assertEqual(cookie['sameSite'],'Lax')
        self.assertNotIn('expires',cookie)
        self.assertEqual(data['origins'][0]['sessionStorage'][0]['value'],'tab-value')

    def test_extension_expiry_and_cookie_domains(self):
        raw=fixture(); raw['cookies'] += [{'name':'google','value':'unrelated','domain':'.google.com'},
                                         {'name':'lookalike','value':'unrelated','domain':'dola.com.evil.invalid'}]
        data=self.manager.normalize(raw);self.assertEqual(len(data['cookies']),2);self.assertEqual(data['skippedCount'],2)
        raw={'cookies':[{'name':'sessionid','value':SECRET,'domain':'.dola.com','expirationDate':2000000000,'isHttpOnly':True,'isSecure':True}]}
        self.assertEqual(self.manager.normalize(raw)['cookies'][0]['expires'],2000000000)

    def test_preview_does_not_return_secrets_or_mark_logged_in(self):
        result=self.manager.preview(json.dumps(fixture()))
        self.assertNotIn(SECRET,json.dumps(result));self.assertNotIn('logged_in',json.dumps(result))
        self.assertEqual(result['expiredCount'],1);self.assertTrue(result['hasSessionCookie']);self.assertEqual(len(self.worker.profiles),1)

    def test_rejects_bad_origin_and_nonfinite_and_oversize(self):
        for raw in ('not-json','[]',{'cookies':[{'name':'x','value':'x','domain':'.google.com'}]},
                    {'cookies':fixture()['cookies'],'origins':[{'origin':'https://dola.com.evil.invalid'}]},
                    {'cookies':[dict(fixture()['cookies'][0],expires=float('nan'))]},
                    {'cookies':[dict(fixture()['cookies'][0],value='x'*(8*1024*1024))]}):
            with self.assertRaises(ApiError):self.manager.preview(raw)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_backup_is_encrypted_and_survives_manager_restart(self):
        self.prepare_browser();self.manager.backup('one')
        path=self.manager._path('one');raw=path.read_text(encoding='utf-8')
        self.assertNotIn(SECRET,raw);self.assertNotIn('fixture-db',raw)
        recreated=SessionBackups(self.worker,ApiError)
        self.assertEqual(recreated.info('one')['cookieCount'],2)
        self.assertEqual(recreated.normalize(recreated.export('one','encrypted')['text'])['cookies'][0]['value'],SECRET)
        self.assertEqual(json.loads(recreated.export('one','json')['text'])['cookies'][0]['value'],SECRET)
        self.assertNotIn(SECRET,json.dumps(recreated.info('one')))

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_failed_or_unconfirmed_capture_keeps_previous_backup(self):
        self.prepare_browser();self.manager.backup('one');path=self.manager._path('one');original=path.read_bytes()
        self.manager._evaluate=lambda *_: {'url':'https://www.dola.com','login':True}
        with self.assertRaises(ApiError):self.manager.backup('one')
        self.assertEqual(path.read_bytes(),original)
        self.manager.backup('one',automatic=True);self.assertEqual(path.read_bytes(),original)
        self.assertIn('原备份',self.manager.info('one')['message'])

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_backup_binding_and_corruption_do_not_leak_values(self):
        self.prepare_browser();self.manager.backup('one');self.worker.create({'id':'two'})
        self.manager._path('two').write_bytes(self.manager._path('one').read_bytes())
        with self.assertRaises(ApiError):self.manager.info('two')
        self.manager._path('one').write_text('{"blob":"bad"}',encoding='utf-8')
        with self.assertRaises(ApiError):self.manager.export('one','json')
        self.manager.backup('one');self.assertTrue(self.manager.info('one')['available'])

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_session_change_during_capture_keeps_old_snapshot(self):
        self.prepare_browser();self.manager.backup('one');path=self.manager._path('one');before=path.read_bytes()
        calls={'count':0}
        def changing(*_):
            calls['count']+=1
            return {'cookies':[dict(fixture()['cookies'][0],value='changed' if calls['count']>1 else SECRET)]}
        self.manager._call=changing
        with self.assertRaises(ApiError):self.manager.backup('one')
        self.assertEqual(path.read_bytes(),before)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_auto_backup_is_triggered_and_throttled_after_login(self):
        self.prepare_browser()
        with patch('session_backup.threading.Thread') as thread:
            self.manager.after_login('one');self.assertTrue(thread.called)
        self.manager.backup('one')
        with patch('session_backup.threading.Thread') as thread:
            self.manager.after_login('one');self.assertFalse(thread.called)

    def test_invalid_proxy_and_reused_draft_preserve_original(self):
        draft=self.manager.preview(fixture());before=copy.deepcopy(self.worker.profiles)
        with self.assertRaises(ApiError):self.manager.import_new({'draftId':draft['draftId'],'name':'New','proxyId':'missing'})
        self.assertEqual(self.worker.profiles,before)
        with self.assertRaises(ApiError):self.manager.import_new({'draftId':draft['draftId'],'id':'one'})
        self.manager.drafts[draft['draftId']]['used']=True
        with self.assertRaises(ApiError):self.manager.import_new({'draftId':draft['draftId']})

    def test_restore_failure_keeps_new_environment_and_original_untouched(self):
        draft=self.manager.preview(fixture());original=copy.deepcopy(self.worker.profile('one'))
        with patch.object(self.worker,'launch',side_effect=RuntimeError(SECRET)):
            result=self.manager.import_new({'draftId':draft['draftId'],'name':'Recovered'})
        self.assertFalse(result['restored']);self.assertNotEqual(result['id'],'one')
        self.assertEqual(self.worker.profile('one'),original);self.assertNotIn(SECRET,json.dumps(result))
        self.assertEqual(self.worker.profile(result['id'])['startUrl'],'about:blank')

    def test_authenticated_preview_accepts_large_json_but_rejects_web_origins(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.worker,'fixture-token'))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        opener=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{server.server_port}/sessions/preview'
        data=fixture();data['storage']['localStorage']['large']='x'*70000
        body=json.dumps({'text':json.dumps(data)}).encode('utf-8')
        def request(headers):return opener.open(Request(url,data=body,headers={'Content-Type':'application/json',**headers}),timeout=5)
        try:
            for headers in ({},{'Authorization':'Bearer fixture-token','Origin':'https://www.dola.com'}):
                with self.assertRaises(HTTPError):request(headers)
            with request({'Authorization':'Bearer fixture-token'}) as response:
                result=json.load(response);self.assertEqual(response.headers['Cache-Control'],'no-store')
                self.assertNotIn(SECRET,json.dumps(result));self.assertIn('draftId',result)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':unittest.main()

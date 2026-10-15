"""Actual browser backup/import/restart with all website responses offline."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from server import BrowserWorker
from session_backup import RESTORE_STORAGE
from session_browser_fixture import offline_document
from test_session_backup import fixture, SECRET


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True);checks=[]
    def check(label,condition):
        if not condition:raise RuntimeError(label)
        checks.append(label)
    with tempfile.TemporaryDirectory(prefix='cloak-session-offline-') as directory:
        worker=BrowserWorker(Path(directory),Path.home()/'.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe')
        manager=worker.session_backups
        mode={'logged':True}
        worker.navigate=lambda pid,payload:offline_document(worker,pid,payload['url'],
            '<body><button>Log out</button><textarea></textarea>Offline signed-in fixture</body>' if mode['logged'] else '<body><button>Log in</button>Offline signed-out fixture</body>')
        try:
            worker.create({'id':'source','name':'Source','startUrl':'about:blank','seed':43210})
            worker.launch('source',embedded=True)
            worker.navigate('source',{'url':'https://www.dola.com/chat/'})
            rt=worker.runtime['source'];page=worker.target(rt)
            original_profile=copy.deepcopy(worker.profile('source'));original_pid=rt['process'].pid
            data=manager.normalize(fixture())
            manager._call(worker.cdp_json(rt['cdpPort'],'/json/version'),'Storage.setCookies',{'cookies':[data['cookies'][0],{'name':'unrelated','value':'other-site','domain':'.google.com','path':'/'}]})
            manager._evaluate(page,RESTORE_STORAGE.replace('SESSION_DATA',json.dumps(data['origins'][0]),1))
            manager.backup('source')
            meta=manager.info('source')
            check('Actual Chromium captures HttpOnly cookie and JSON IndexedDB',meta['cookieCount']==1 and meta['databaseCount']==1)
            check('Windows encrypted snapshot contains no cookie or storage plaintext',SECRET not in manager._path('source').read_text(encoding='utf-8'))
            exported=json.loads(manager.export('source','json')['text'])
            check('Export retains HttpOnly Secure SameSite without changing session expiry',exported['cookies'][0]['httpOnly'] and exported['cookies'][0]['secure'] and exported['cookies'][0]['sameSite']=='Lax' and 'expires' not in exported['cookies'][0])
            exported['cookies'].append(data['cookies'][1])
            preview=manager.preview(exported)
            check('Preview reports expired cookie without exposing cookie or storage values',preview['expiredCount']==1 and SECRET not in json.dumps(preview))
            imported=manager.import_new({'draftId':preview['draftId'],'name':'Imported fixture'})
            pid=imported['id'];newrt=worker.runtime[pid]
            check('Browser restoration creates separate profile while preserving original PID and config',pid!='source' and worker.profile('source')==original_profile and rt['process'].pid==original_pid)
            check('Restore validates visible Dola login and skips expired cookies',imported['restored'] and imported['check']['status']=='logged_in' and imported['expiredSkipped']==1)
            check('Session backup preserves original fingerprint seed for new environment',worker.profile(pid)['seed']==43210)
            observed=manager._evaluate(worker.target(newrt),"({local:localStorage.getItem('fixture'),tab:sessionStorage.getItem('tab')})")
            check('Web storage restored before loading offline Dola document',observed=={'local':SECRET,'tab':'tab-value'})
            rec=manager._evaluate(worker.target(newrt),"(async()=>{const db=await new Promise(ok=>{const r=indexedDB.open('fixture-db');r.onsuccess=()=>ok(r.result)});const v=await new Promise(ok=>{const r=db.transaction('records').objectStore('records').get('entry');r.onsuccess=()=>ok(r.result)});db.close();return v})()")
            check('IndexedDB key/value record survives actual browser restore',rec=={'token':SECRET})
            first_pid=newrt['process'].pid;worker.close(pid);worker.launch(pid,embedded=True)
            newrt=worker.runtime[pid]
            observed=manager._evaluate(worker.target(newrt),"({local:localStorage.getItem('fixture'),tab:sessionStorage.getItem('tab')})")
            cookies=manager._call(worker.cdp_json(newrt['cdpPort'],'/json/version'),'Storage.getCookies')['cookies']
            check('Closing and reopening retains imported session cookie and tab storage',newrt['process'].pid!=first_pid and any(c['name']=='sessionid' and c['value']==SECRET for c in cookies) and observed=={'local':SECRET,'tab':'tab-value'})
            # A newer login in the same profile must never be overwritten by an
            # older automatic snapshot on startup.
            manager._call(worker.cdp_json(newrt['cdpPort'],'/json/version'),'Storage.setCookies',{'cookies':[dict(data['cookies'][0],value='newer-fixture',expires=2000000000)]})
            worker.close(pid);worker.launch(pid,embedded=True)
            cookies=manager._call(worker.cdp_json(worker.runtime[pid]['cdpPort'],'/json/version'),'Storage.getCookies')['cookies']
            check('Startup keeps newer identity cookie instead of replaying old backup',any(c['name']=='sessionid' and c['value']=='newer-fixture' for c in cookies))
            mode['logged']=False
            preview=manager.preview(exported)
            rejected=manager.import_new({'draftId':preview['draftId'],'name':'Signed out fixture'})
            check('Presence of imported sessionid does not fabricate signed-in status',rejected['restored'] and rejected['check']['status']=='login_required')
            check('Fixture session secrets stay out of public account inventory',SECRET not in json.dumps([worker.state(p) for p in worker.profiles]))
            (args.out/'session-browser-checks.json').write_text(json.dumps({'ok':True,'offline':True,'checks':checks},indent=2),encoding='utf-8')
            print(f'{len(checks)} real Chromium offline session checks passed')
        finally:worker.shutdown()


if __name__=='__main__':main()

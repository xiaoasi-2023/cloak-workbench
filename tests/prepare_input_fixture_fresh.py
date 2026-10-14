"""Create the explicitly authorized isolated input-regression-2 fixture."""
from pathlib import Path
import json
from urllib.request import Request, ProxyHandler, build_opener

root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
opener = build_opener(ProxyHandler({}))
fixture = {'id': 'input-regression-2', 'name': 'Input Regression 2',
           'startUrl': 'http://127.0.0.1:8766/probe?profile=input-regression-2',
           'locale': 'en-US', 'timezone': 'UTC', 'seed': 54322, 'proxy': '',
           'screenWidth': 1280, 'screenHeight': 800}
headers = {'Authorization': 'Bearer ' + session['token'], 'Content-Type': 'application/json'}
with opener.open(Request('http://127.0.0.1:8766/profiles', headers=headers), timeout=10) as response:
    profiles = json.load(response)['profiles']
if any(p['id'] == fixture['id'] for p in profiles):
    raise RuntimeError('Fresh fixture id already exists; preserve it without changes')
with opener.open(Request('http://127.0.0.1:8766/profiles', headers=headers,
                        data=json.dumps(fixture).encode('utf-8')), timeout=10) as response:
    json.load(response)
(root / '.local/input-fixture-fresh.json').write_text(json.dumps(fixture), encoding='utf-8')
print(json.dumps({'createdFixture': fixture['id'], 'existingProfilesUnchanged': True}))

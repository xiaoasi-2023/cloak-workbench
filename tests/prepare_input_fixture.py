"""Create an isolated offline input fixture without changing existing profiles."""
from pathlib import Path
import json
from urllib.request import Request, ProxyHandler, build_opener

root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
opener = build_opener(ProxyHandler({}))

def api(path, body=None):
    req = Request('http://127.0.0.1:8766/' + path,
        data=None if body is None else json.dumps(body).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + session['token'], 'Content-Type': 'application/json'})
    with opener.open(req, timeout=10) as response:
        return json.load(response)

fixture = {'id': 'input-regression', 'name': '输入回归验证（专用）',
           'startUrl': 'http://127.0.0.1:8766/probe?profile=input-regression',
           'locale': 'en-US', 'timezone': 'UTC', 'seed': 54321, 'proxy': '',
           'screenWidth': 1280, 'screenHeight': 800}
profiles = api('profiles')['profiles']
existing = next((p for p in profiles if p['id'] == fixture['id']), None)
marker = root / '.local/input-fixture.json'
if existing:
    if not marker.exists() or any(existing.get(k) != v for k, v in fixture.items()):
        raise RuntimeError('Existing profile is not this test fixture; preserved without change')
else:
    api('profiles', fixture)
marker.write_text(json.dumps({'id': fixture['id'], 'name': fixture['name']}, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'createdFixture': fixture['id'], 'userProfilesUnchanged': True}))

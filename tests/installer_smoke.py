"""Run packaged desktop + two local browsers with no developer tools on PATH.

Uses a dedicated empty data directory. Never opens a real account website.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args()
    app = args.app.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qihang-install-qa-') as directory:
        state = args.data_root.resolve() if args.data_root else Path(directory) / '安装验收 空白资料'
        if state.exists() and any(state.iterdir()):
            raise RuntimeError('Smoke test requires an empty dedicated data directory')
        state.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, QIHANG_DATA_ROOT=str(state),
                   PATH=str(Path(os.environ['SystemRoot']) / 'System32'),
                   DOTNET_ROOT=str(state / 'no-global-dotnet'),
                   DOTNET_ROOT_X64=str(state / 'no-global-dotnet'), DOTNET_MULTILEVEL_LOOKUP='0')
        for name in ('PYTHONHOME', 'PYTHONPATH', 'CLOAK_WORKBENCH_ROOT', 'CLOAK_WORKBENCH_TOKEN', 'CLOAK_WORKBENCH_BROWSER', 'CLOAK_WORKBENCH_API_URL'):
            env.pop(name, None)
        process = subprocess.Popen([str(app), '--smoke'], cwd=state.parent, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        session = None
        checks = []
        opener = build_opener(ProxyHandler({}))
        def api(path, body=None):
            request = Request(session['baseUrl'] + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization': 'Bearer ' + session['token'], 'Content-Type': 'application/json'})
            with opener.open(request, timeout=15) as response:
                return json.load(response)
        def check(label, condition):
            if not condition:
                raise AssertionError(label)
            checks.append(label)
        try:
            deadline = time.monotonic() + 90
            result = state / '.local/smoke-result.json'
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Packaged launcher exited before smoke completed')
                session_path = state / '.local/session.json'
                if session_path.exists():
                    session = json.loads(session_path.read_text(encoding='utf-8'))
                if result.exists():
                    break
                time.sleep(.25)
            check('Packaged startup and desktop smoke completed', result.exists())
            check('Packaged launcher creates user data directories on first run', (state / 'data').is_dir())
            smoke = json.loads(result.read_text(encoding='utf-8-sig'))
            check('Two real bundled browsers embedded and switched', smoke.get('ok') is True and smoke.get('profiles') == 2)
            health = api('health')
            check('Worker uses bundled engine rather than user cache', health['engineAvailable'] and Path(health['enginePath']).is_relative_to(app.parent / 'engine'))
            check('Random service port avoids development instance', session['baseUrl'].startswith('http://127.0.0.1:') and session['baseUrl'] != 'http://127.0.0.1:8766/')
            profiles = api('profiles')['profiles']
            check('Only isolated local-probe profiles exist', {p['id'] for p in profiles} == {'demo-a', 'demo-b'} and all(p['startUrl'].startswith(session['baseUrl'] + 'probe') for p in profiles))
            for identifier, locale, zone in (('demo-a', 'ja-JP', 'Asia/Tokyo'), ('demo-b', 'ko-KR', 'Asia/Seoul')):
                observed = api('profiles/' + identifier + '/environment')['actual']
                check(identifier + ' bundled language/timezone work', observed['language'] == locale and observed['timezone'] == zone)
            check('No developer accounts or enterprise authorization shipped', not api('login-accounts')['accounts'] and not api('workspaces')['workspaces'])
            check('No project data saved into application directory', not (app.parent / 'data').exists() and not (app.parent / '.local').exists())
            # Exercise real frozen cryptography imports, not only the Python
            # development environment. All records below are isolated fixtures.
            for profile in profiles:
                api('profiles/' + profile['id'] + '/close', {})
            api('proxies', {'name': 'Offline migration fixture', 'proxy': 'http://127.0.0.1:7890', 'proxyUsername': 'fixture', 'proxyPassword': 'fixture-secret'})
            password = 'offline-frozen-migration-password'
            exported = api('config-transfer/export', {'password': password})
            check('Frozen encrypted export contains no plaintext proxy password', 'fixture-secret' not in exported['text'])
            preview = api('config-transfer/preview', {'text': exported['text'], 'password': password, 'mode': 'replace'})
            check('Frozen backup decrypts and previews correctly', preview['counts']['replace'] == 1)
            restored = api('config-transfer/import', {'planId': preview['planId'], 'password': password})
            check('Frozen configuration import writes recoverable backup', restored['imported'] == 1 and Path(restored['backupPath']).is_file())
            # Copy only non-sensitive test result; session tokens never leave temp.
            (args.out / 'checks.json').write_text(json.dumps({'ok': True, 'checks': checks, 'desktopPid': session['desktopPid'], 'workerPid': session['workerPid']}, indent=2), encoding='utf-8')
            print(f'{len(checks)} packaged application checks passed')
        except Exception:
            for name in ('worker.log', 'launcher-error.log', 'smoke-result.json'):
                source = state / '.local' / name
                if source.exists():
                    (args.out / name).write_bytes(source.read_bytes())
            raise
        finally:
            if session:
                try:
                    api('shutdown', {})
                except Exception:
                    pass
                subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'), '/PID', str(session['desktopPid'])], capture_output=True)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()

"""Own the local worker and desktop lifetime; credentials never enter command lines."""
from pathlib import Path
import ctypes
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request

FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
STATE_ROOT = Path(os.environ.get('QIHANG_DATA_ROOT', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'QihangWorkbench'))) if FROZEN else ROOT
LOCAL = STATE_ROOT / '.local'


def packaged_worker():
    # The frozen launcher also contains the backend. It never requires a
    # machine-wide Python installation or runs the launcher recursively.
    sys.argv.remove('--worker')
    sys.path.insert(0, str(ROOT / 'worker'))
    from server import main as worker_main
    worker_main()


def wait_for_worker(worker, ready_file, opener, token, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError('后台服务启动失败，请查看日志目录。')
        try:
            endpoint = json.loads(ready_file.read_text(encoding='utf-8'))
            # A Windows virtualenv executable may hand off to a child Python
            # process. The frozen worker has no such handoff. Both modes verify
            # the fresh per-launch bearer token before trusting the endpoint.
            if (FROZEN and endpoint['pid'] != worker.pid) or not 1 <= endpoint['port'] <= 65535:
                raise ValueError('Unexpected worker endpoint')
            base = f'http://127.0.0.1:{endpoint["port"]}/'
            with opener.open(urllib.request.Request(base + 'health', headers={'Authorization': 'Bearer ' + token}), timeout=2) as response:
                if json.load(response).get('status') == 'ok':
                    return base
        except (OSError, ValueError, KeyError):
            time.sleep(.1)
    raise RuntimeError('后台服务启动超时，请查看日志目录。')

def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lease = (LOCAL / 'launcher.lock').open('a+b')
    lease.seek(0)
    if not lease.read(1):
        lease.write(b'0'); lease.flush()
    lease.seek(0)
    try:
        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        ctypes.windll.user32.MessageBoxW(0, 'Cloak Workbench is already running.', 'Cloak Workbench', 0)
        return 1
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, CLOAK_WORKBENCH_TOKEN=token, CLOAK_WORKBENCH_ROOT=str(STATE_ROOT), PYTHONIOENCODING='utf-8',
               DOTNET_CLI_TELEMETRY_OPTOUT='1')
    sdk = ROOT / '.tools/dotnet/dotnet.exe'
    if sdk.exists():
        env['DOTNET_ROOT'] = str(sdk.parent)
    exe = ROOT / ('app/CloakWorkbench.exe' if FROZEN else 'desktop/bin/Release/net8.0-windows/CloakWorkbench.exe')
    if not exe.exists():
        if FROZEN:
            raise RuntimeError('程序文件不完整，请重新运行安装包修复。')
        subprocess.run([str(sdk) if sdk.exists() else 'dotnet', 'build', 'desktop/CloakWorkbench.csproj', '-c', 'Release', '--nologo'], cwd=ROOT, env=env, check=True)
    python = ROOT / '.venv/Scripts/python.exe'
    if not FROZEN and not python.exists():
        raise RuntimeError('Project Python environment is missing. See README.md for setup.')
    if FROZEN:
        branding = json.loads((ROOT / 'branding.json').read_text(encoding='utf-8'))
        env['CLOAK_WORKBENCH_DISPLAY_NAME'] = branding['displayName']
        env['CLOAK_WORKBENCH_BRAND'] = 'QIHANG'
        browser = ROOT / 'engine' / ('chromium-' + branding['recommendedEngine']['version']) / 'chrome.exe'
        if not browser.is_file():
            raise RuntimeError('浏览器内核文件不完整，请重新运行安装包修复。')
        env['CLOAK_WORKBENCH_BROWSER'] = str(browser)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = 'http://127.0.0.1:8766/'
    def call(path, body=None):
        req = urllib.request.Request(base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    worker = None
    desktop = None
    session = LOCAL / 'session.json'
    ready_file = LOCAL / ('ready-' + secrets.token_hex(8) + '.json')
    log = (LOCAL / 'worker.log').open('ab')
    try:
        worker_command = [sys.executable, '--worker'] if FROZEN else [str(python), str(ROOT / 'worker/server.py')]
        worker_command += ['--data-dir', str(STATE_ROOT / 'data'), '--port', '0' if FROZEN else '8766', '--ready-file', str(ready_file)]
        worker = subprocess.Popen(worker_command, cwd=ROOT, env=env,
            stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        base = wait_for_worker(worker, ready_file, opener, token)
        env['CLOAK_WORKBENCH_API_URL'] = base
        desktop = subprocess.Popen([str(exe)] + [flag for flag in ('--smoke', '--resume') if flag in sys.argv], cwd=ROOT, env=env)
        session.write_text(json.dumps({'workerPid': worker.pid, 'desktopPid': desktop.pid, 'launcherPid': os.getpid(), 'token': token, 'baseUrl': base}), encoding='utf-8')
        return desktop.wait()
    finally:
        if desktop is not None and desktop.poll() is None:
            desktop.terminate()
            desktop.wait(timeout=10)
        if worker is not None and worker.poll() is None:
            try: call('shutdown', {})
            except Exception: pass
            worker.terminate()
            try: worker.wait(timeout=8)
            except subprocess.TimeoutExpired: worker.kill(); worker.wait(timeout=5)
        session.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)
        log.close(); lease.close()

if __name__ == '__main__':
    if '--worker' in sys.argv:
        packaged_worker()
        sys.exit(0)
    try:
        sys.exit(main())
    except Exception as error:
        LOCAL.mkdir(parents=True, exist_ok=True)
        (LOCAL / 'launcher-error.log').write_text(str(error), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(0, str(error), 'Cloak Workbench startup error', 0x10)
        sys.exit(1)

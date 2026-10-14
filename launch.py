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

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'

def main():
    LOCAL.mkdir(exist_ok=True)
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
    env = dict(os.environ, CLOAK_WORKBENCH_TOKEN=token, CLOAK_WORKBENCH_ROOT=str(ROOT), PYTHONIOENCODING='utf-8',
               DOTNET_CLI_TELEMETRY_OPTOUT='1')
    sdk = ROOT / '.tools/dotnet/dotnet.exe'
    if sdk.exists():
        env['DOTNET_ROOT'] = str(sdk.parent)
    exe = ROOT / 'desktop/bin/Release/net8.0-windows/CloakWorkbench.exe'
    if not exe.exists():
        subprocess.run([str(sdk) if sdk.exists() else 'dotnet', 'build', 'desktop/CloakWorkbench.csproj', '-c', 'Release', '--nologo'], cwd=ROOT, env=env, check=True)
    python = ROOT / '.venv/Scripts/python.exe'
    if not python.exists():
        raise RuntimeError('Project Python environment is missing. See README.md for setup.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(path, body=None):
        req = urllib.request.Request('http://127.0.0.1:8766/' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    worker = None
    session = LOCAL / 'session.json'
    log = (LOCAL / 'worker.log').open('ab')
    try:
        worker = subprocess.Popen([str(python), str(ROOT / 'worker/server.py')], cwd=ROOT, env=env,
            stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(100):
            if worker.poll() is not None:
                raise RuntimeError('Browser worker could not start. Check .local/worker.log; port 8766 may be in use.')
            try:
                call('health'); break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError('Browser worker startup timed out.')
        desktop = subprocess.Popen([str(exe)] + [flag for flag in ('--smoke', '--resume') if flag in sys.argv], cwd=ROOT, env=env)
        session.write_text(json.dumps({'workerPid': worker.pid, 'desktopPid': desktop.pid, 'token': token}), encoding='utf-8')
        return desktop.wait()
    finally:
        if worker is not None and worker.poll() is None:
            try: call('shutdown', {})
            except Exception: pass
            worker.terminate()
            try: worker.wait(timeout=8)
            except subprocess.TimeoutExpired: worker.kill(); worker.wait(timeout=5)
        session.unlink(missing_ok=True)
        log.close(); lease.close()

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        LOCAL.mkdir(exist_ok=True)
        (LOCAL / 'launcher-error.log').write_text(str(error), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(0, str(error), 'Cloak Workbench startup error', 0x10)
        sys.exit(1)

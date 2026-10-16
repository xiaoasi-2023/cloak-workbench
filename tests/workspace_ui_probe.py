"""Real WPF/Chromium with isolated data and an offline Google Directory provider."""
import argparse
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from server import BrowserWorker, make_handler
from workspace_fixture import DirectoryFixture, fixture_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cloak-workspace-qa-') as directory:
        temp = Path(directory)
        binary = Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe'
        if not binary.is_file():
            raise RuntimeError('Installed Chromium required')
        worker = BrowserWorker(temp / 'data', binary)
        fixture = DirectoryFixture(latency=.5)
        worker.workspaces.client_factory = fixture.connect
        def offline_exchange(*args):
            time.sleep(.2)
            return {'token': 'offline-access', 'refresh_token': 'offline-refresh',
                    'expiry': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        worker.workspaces.oauth_exchange = offline_exchange
        for name, email in (('Alpha 企业 · 离线验证', 'admin@alpha.invalid'), ('Beta 企业 · 离线验证', 'admin@beta.invalid')):
            worker.workspaces.upsert({'name': name, 'adminEmail': email, 'keyJson': fixture_key()})
        token = 'isolated-workspace-ui-token'
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(worker, token))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True); thread.start()
        worker.create({'id': 'ui-browser', 'name': '企业目录原生切换验证', 'startUrl': f'http://127.0.0.1:{server.server_port}/probe'})
        try:
            env = dict(os.environ, CLOAK_WORKBENCH_ROOT=str(temp), CLOAK_WORKBENCH_TOKEN=token)
            result = subprocess.run([str(ROOT / '.tools/dotnet/dotnet.exe'), str(ROOT / '.local/proxy-ui-build/ProxyUiHarness.dll'),
                                     f'http://127.0.0.1:{server.server_port}/', str(args.out.resolve()), '--workspaces'], cwd=ROOT, env=env, timeout=180)
            if result.returncode:
                raise RuntimeError('Workspace UI checks failed; see ui-error.txt')
            print('Real WPF and Chromium Workspace flow passed (offline Directory fixture)')
        finally:
            worker.shutdown(); server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()

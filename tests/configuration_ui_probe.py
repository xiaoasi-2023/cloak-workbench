"""Run the configuration migration WPF page against isolated local fixtures."""
import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from server import BrowserWorker, make_handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='qihang-config-qa-') as directory:
        worker = BrowserWorker(Path(directory) / 'data', Path('missing.exe'))
        proxy = worker.catalog.upsert({'name': '配置迁移测试线路', 'proxy': 'http://127.0.0.1:7890'})
        worker.workspaces.upsert({'name': '配置迁移测试企业', 'adminEmail': 'admin@example.invalid', 'authType': 'oauth',
            'oauthClientJson': json.dumps({'installed': {'client_id': '1234567890-offline.apps.googleusercontent.com', 'client_secret': 'offline-client-secret'}}), 'proxyId': proxy['id']})
        token = 'offline-config-ui-local-token'
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(worker, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            env = dict(os.environ, CLOAK_WORKBENCH_ROOT=directory, CLOAK_WORKBENCH_TOKEN=token,
                       CLOAK_WORKBENCH_API_URL=f'http://127.0.0.1:{server.server_port}/')
            subprocess.run([str(ROOT / '.tools/dotnet/dotnet.exe'), str(ROOT / '.local/proxy-ui-build/ProxyUiHarness.dll'),
                            f'http://127.0.0.1:{server.server_port}/', str(args.out.resolve()), '--configuration-transfer'],
                           cwd=ROOT, env=env, check=True, timeout=80)
        finally:
            worker.shutdown(); server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()

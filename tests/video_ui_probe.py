"""Real WPF video creator with offline Chromium transport; no user data."""
from http.server import ThreadingHTTPServer
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from server import BrowserWorker, make_handler
from video_fixture import OfflineVideoPage, make_clip


def main():
    output = ROOT / '.local/video-ui-qa'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cloak-video-ui-') as directory:
        OfflineVideoPage.clips = {s: make_clip(directory, s) for s in (15, 30)}
        worker = BrowserWorker(Path(directory) / 'data', Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe')
        worker.create(dict(id='ui-browser', name='Offline video environment', startUrl='about:blank'))
        token = 'offline-video-ui-token'
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(worker, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(os.environ, CLOAK_WORKBENCH_ROOT=directory, CLOAK_WORKBENCH_TOKEN=token)
        try:
            with patch('video_tasks.PageCDP', OfflineVideoPage):
                result = subprocess.run([str(ROOT / '.tools/dotnet/dotnet.exe'), str(ROOT / '.local/proxy-ui-build/ProxyUiHarness.dll'),
                    f'http://127.0.0.1:{server.server_port}/', str(output), '--videos'], cwd=ROOT, env=env, timeout=100)
                if result.returncode:
                    (output / 'failed-tasks.json').write_text(json.dumps(worker.videos.list(), ensure_ascii=False, indent=2), encoding='utf-8')
                    (output / 'failed-media.json').write_text(json.dumps(OfflineVideoPage.samples[-8:], ensure_ascii=False, indent=2), encoding='utf-8')
                    raise RuntimeError('WPF video test failed; see .local/video-ui-qa/ui-error.txt')
                assert len(OfflineVideoPage.accepted) == 1
                print('Real WPF video creator passed; one offline submission, zero real generation requests')
        finally:
            worker.shutdown()
            for job_thread in worker.videos.threads:
                job_thread.join(timeout=12)
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()

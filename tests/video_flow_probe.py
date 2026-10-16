import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from server import BrowserWorker
from video_fixture import OfflineVideoPage, make_clip


def wait(manager, tid, states, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with manager.worker.lock:
            job = dict(manager.entry(tid))
        if job['state'] in states:
            return job
        if job['state'] in ('failed', 'duration_mismatch', 'completed', 'cancelled'):
            raise AssertionError((job['state'], job['stage']))
        time.sleep(.1)
    raise AssertionError(('Timed out', job['stage']))


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix='cloak-video-offline-') as directory:
        OfflineVideoPage.clips = {s: make_clip(directory, s) for s in (15, 30)}
        worker = BrowserWorker(Path(directory) / 'data', Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe')
        worker.create(dict(id='video-test', name='Offline video fixture', startUrl='about:blank'))
        try:
            worker.launch('video-test', embedded=True)
            with patch('video_tasks.PageCDP', OfflineVideoPage):
                def start(prompt):
                    return worker.videos.start(dict(profileId='video-test', model='seedance_v2.5', duration=30, ratio='16:9', prompt=prompt))['id']
                success = wait(worker.videos, start('A green bird'), {'completed'})
                assert abs(success['actualDuration'] - 30) < .2
                assert len(OfflineVideoPage.accepted) == 1
                checks.append('Real CDP form submission and metadata-confirmed 30-second video')
                short = wait(worker.videos, start('A short bird'), {'duration_mismatch'})
                assert abs(short['actualDuration'] - 15) < .2
                checks.append('A 15-second result requested as 30 seconds is explicitly flagged')
                denied = wait(worker.videos, start('A wrong request'), {'failed'})
                assert denied['submitted'] is False and len(OfflineVideoPage.accepted) == 2 and len(OfflineVideoPage.denied) == 1
                checks.append('Mismatched actual duration blocked before transmission without retry')
                tid = start('A wait bird')
                wait(worker.videos, tid, {'running'})
                worker.videos.cancel(tid)
                time.sleep(1)
                assert worker.videos.entry(tid)['state'] == 'cancelled'
                assert len(OfflineVideoPage.accepted) == 3
                checks.append('Cancellation never claims upstream refund or repeats the submission')
                chain = wait(worker.videos, start('A chain bird'), {'completed'})
                assert abs(chain['actualDuration'] - 30) < .2 and len(OfflineVideoPage.accepted) == 4
                checks.append('Current-conversation chain media URL loads metadata through the same browser')
                worker.videos.open(success['id'])
                assert worker.runtime['video-test']['targetId'] == success['targetId']
                checks.append('Open result activates original dedicated tab in the owned environment')
            for thread in worker.videos.threads:
                thread.join(timeout=12)
            assert all(not t.is_alive() for t in worker.videos.threads)
            checks.append('All CDP interception threads terminate and release their tab sessions')
            path = ROOT / '.local/video-flow-qa'
            path.mkdir(exist_ok=True)
            (path / 'checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
            print(json.dumps({'passed': len(checks), 'checks': checks}))
        finally:
            worker.shutdown()


if __name__ == '__main__':
    main()

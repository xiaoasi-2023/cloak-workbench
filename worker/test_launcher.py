"""Launcher readiness handles Windows venv handoff and frozen PID checks."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launch


class LauncherReadinessTests(unittest.TestCase):
    def check_endpoint(self, frozen, reported_pid, timeout=.1):
        with TemporaryDirectory() as directory:
            ready = Path(directory) / 'ready.json'
            ready.write_text(json.dumps({'pid': reported_pid, 'port': 32123}), encoding='utf-8')
            worker = Mock(pid=1234); worker.poll.return_value = None
            opener = Mock(); opener.open.side_effect = lambda *a, **k: io.BytesIO(b'{"status":"ok"}')
            with patch.object(launch, 'FROZEN', frozen):
                return launch.wait_for_worker(worker, ready, opener, 'offline-launch-token', timeout)

    def test_source_venv_handoff_still_authenticates_health(self):
        self.assertEqual(self.check_endpoint(False, 5678), 'http://127.0.0.1:32123/')

    def test_frozen_worker_accepts_only_its_own_pid(self):
        self.assertEqual(self.check_endpoint(True, 1234), 'http://127.0.0.1:32123/')
        with self.assertRaises(RuntimeError):
            self.check_endpoint(True, 5678, .01)


if __name__ == '__main__':
    unittest.main()

"""Restart manifest semantics; no app or browser processes are touched."""
import unittest

from restart_workbench import restart_snapshot


class RestartSnapshotTests(unittest.TestCase):
    def test_preserves_modes_deduplicates_and_excludes_closed(self):
        snapshot = {'selected': 'external', 'width': 1440, 'height': 900, 'browsers': [
            {'id': 'external', 'displayMode': 'external'}, {'id': 'external', 'displayMode': 'embedded'},
            {'id': 'legacy'}, {'id': 'closed'}, {'id': 'missing'}]}
        profiles = {key: {'id': key, 'url': 'https://example.invalid/' + key, 'state': state}
                    for key, state in [('external', 'running'), ('legacy', 'starting'), ('closed', 'closed')]}
        saved = restart_snapshot(snapshot, profiles)
        self.assertEqual(saved['selected'], 'external')
        self.assertEqual(saved['browsers'], [
            {'id': 'external', 'url': 'https://example.invalid/external', 'displayMode': 'external'},
            {'id': 'legacy', 'url': 'https://example.invalid/legacy', 'displayMode': 'embedded'}])

    def test_selects_live_fallback_and_handles_empty_workspace(self):
        snapshot = {'selected': 'closed', 'width': 1440, 'height': 900, 'browsers': [{'id': 'live'}]}
        saved = restart_snapshot(snapshot, {'live': {'id': 'live', 'state': 'running', 'url': 'about:blank'}})
        self.assertEqual(saved['selected'], 'live')
        self.assertIsNone(restart_snapshot(snapshot, {})['selected'])


if __name__ == '__main__':
    unittest.main()

import copy
import base64
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_tasks import VideoTasks, check_submission, duration_result, media_urls, patch_config, validate_video


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(profileId='test', model='seedance_v2.5', duration=30, ratio='16:9', prompt='A cat in sunlight')

    def payload(self, **params):
        return json.dumps({'chat_ability': {'ability_type': 17, 'ability_param': json.dumps(dict(model=self.config['model'], duration=30, ratio='16:9', **params))}, 'messages': [{'content_block': [{'content': {'text': self.config['prompt']}}]}]})

    def test_validation(self):
        self.assertEqual(validate_video(self.config), self.config)
        for values in [dict(model='seedance_v2.0'), dict(duration=True), dict(duration=20), dict(prompt=''), dict(ratio='2:1'), dict(profileId='')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_video(dict(self.config, **values))

    def test_submitted_parameters_checked_without_changing_body(self):
        body = self.payload()
        check_submission(body, self.config)
        for key, val in [('duration', 10), ('model', 'seedance_v2.0'), ('ratio', '9:16')]:
            data = json.loads(body)
            params = json.loads(data['chat_ability']['ability_param'])
            params[key] = val
            data['chat_ability']['ability_param'] = json.dumps(params)
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_submission(json.dumps(data), self.config)
        with self.assertRaises(ValueError):
            check_submission(body.replace(self.config['prompt'], 'another request'), self.config)

    def test_multiline_chinese_prompt_and_nested_json_text(self):
        config = validate_video(dict(self.config, prompt='清晨\r\n一只猫'))
        self.assertEqual(config['prompt'], '清晨\n一只猫')
        data = json.loads(self.payload())
        data['messages'] = [{'content_block': [{'content': json.dumps({'text': config['prompt']})}]}]
        check_submission(json.dumps(data), config)

    def test_live_skill_pack_and_nested_action_bar_preserve_other_fields(self):
        original = {'code': 0, 'data': {'other_skill': {'untouched': True}, 'video_generation': {'meta': {
            'option_list': [dict(value='duration', options=[dict(value='5', show_name='5s', is_default=False), dict(value='10', show_name='10s', is_default=True)]),
                dict(value='model', options=[dict(value='seedance_v2.0', show_name='Seedance2.0Fast', is_default=True)])],
            'model_capability': {'seedance_v2.0': {'supported_durations': ['5', '10'], 'extra': 'keep'}}}}}}
        frozen = copy.deepcopy(original)
        patched = patch_config(original, self.config)
        self.assertEqual(original, frozen)
        meta = patched['data']['video_generation']['meta']
        self.assertEqual(meta['option_list'][0]['options'][-1]['value'], '30')
        self.assertEqual(meta['option_list'][1]['options'][-1]['value'], 'seedance_v2.5')
        self.assertEqual(meta['model_capability']['seedance_v2.5']['extra'], 'keep')
        self.assertEqual(patched['data']['other_skill'], original['data']['other_skill'])
        bar = {'data': json.dumps({'key': 'video-duration', 'option_list': [{'id': 4, 'display_text': '10s', 'option_key': '10'}]})}
        patched_bar = json.loads(patch_config(bar, self.config)['data'])
        self.assertEqual(patched_bar['option_list'][-1], {'id': 5, 'display_text': '30s', 'option_key': '30', 'is_default': True})
        self.assertEqual(patch_config(patched, self.config), patched)

    def test_actual_duration_never_uses_requested_value(self):
        for value in [None, float('nan'), float('inf'), 0, -1]:
            self.assertIsNone(duration_result(value, 30))
        self.assertEqual(duration_result(30.09, 30)[0], 'completed')
        self.assertEqual(duration_result(15.03, 30)[0], 'duration_mismatch')

    def test_media_only_uses_playable_https_urls_without_credentials(self):
        url = 'https://media.example/video.mp4'
        data = {'nested': json.dumps({'main_url': base64.b64encode(url.encode()).decode()}), 'download_url': url,
                'image_url': 'https://media.example/image.png', 'bad': {'main_url': 'https://user:password@media.example/a'},
                'encoded': {'main_url': 'qAABencrypted'}, 'local': {'main_url': 'file:///C:/secret'}}
        self.assertEqual(media_urls(data), [url])

    def test_restart_cancel_delete_and_mutual_exclusion(self):
        class Error(Exception):
            def __init__(self, status, message):
                super().__init__(message)
                self.status = status
        with tempfile.TemporaryDirectory() as directory:
            rt = {'process': SimpleNamespace(poll=lambda: None)}
            worker = SimpleNamespace(data_dir=Path(directory), lock=threading.RLock(), runtime={'test': rt},
                profile=lambda pid: dict(id=pid, name='test'), google_login=SimpleNamespace(jobs={}, ACTIVE=('running', 'cancelling')))
            manager = VideoTasks(worker, Error)
            with patch.object(manager, 'run'):
                job = manager.start(self.config)
                with self.assertRaises(Error) as ex:
                    manager.start(self.config)
                self.assertEqual(ex.exception.status, 409)
                with self.assertRaises(Error):
                    manager.delete(job['id'])
                self.assertEqual(VideoTasks(worker, Error).entry(job['id'])['state'], 'interrupted')
                manager.cancel(job['id'])
                manager.update(manager.entry(job['id']), state='completed')
                self.assertEqual(manager.entry(job['id'])['state'], 'cancelled')
                manager.delete(job['id'])
                self.assertEqual(manager.list()['tasks'], [])
                worker.google_login.jobs['test'] = dict(state='needs_attention')
                with self.assertRaises(Error):
                    manager.start(self.config)


if __name__ == '__main__':
    unittest.main()

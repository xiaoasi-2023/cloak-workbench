"""Validation and opt-in real-kernel overrides; never touches user profiles."""
import os
from pathlib import Path
import tempfile
import unittest

from fingerprint import capabilities, inspect_environment, launch_args, validate_fingerprint


class FingerprintValidationTests(unittest.TestCase):
    def test_window_dimensions_are_not_fingerprint_screen_dimensions(self):
        profile = validate_fingerprint({'screenWidth': 1400, 'screenHeight': 850})
        self.assertIsNone(profile['fingerprintScreenWidth'])
        self.assertNotIn('--fingerprint-screen-width=1400', launch_args(profile))

    def test_explicit_native_flags_and_zero_taskbar_are_preserved(self):
        profile = validate_fingerprint(dict(hardwareConcurrency=12, deviceMemory=4,
            fingerprintScreenWidth=1600, fingerprintScreenHeight=900,
            taskbarHeight=0, fingerprintNoise=False,
            gpuVendor='Google Inc. (Intel)', gpuRenderer='ANGLE (Intel, test GPU, D3D11)'))
        args = launch_args(profile)
        for expected in ('--fingerprint-hardware-concurrency=12', '--fingerprint-device-memory=4',
                         '--fingerprint-screen-width=1600', '--fingerprint-screen-height=900',
                         '--fingerprint-taskbar-height=0', '--fingerprint-noise=false'):
            self.assertIn(expected, args)

    def test_reject_invalid_types_and_incoherent_pairs(self):
        for payload in ({'hardwareConcurrency': True}, {'hardwareConcurrency': 0}, {'deviceMemory': 32},
                        {'deviceMemory': True}, {'fingerprintNoise': 'false'},
                        {'fingerprintScreenWidth': 1600}, {'gpuVendor': 'Intel'},
                        {'gpuRenderer': 'x\n--flag'}, {'fingerprintPlatform': 'random'},
                        {'fingerprintScreenWidth': 1600, 'fingerprintScreenHeight': 900, 'taskbarHeight': 900}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_fingerprint(payload)

    def test_capabilities_dont_claim_missing_engine_support(self):
        result = capabilities(Path('does-not-exist/chrome.exe'))
        self.assertFalse(result['engineAvailable'])
        self.assertFalse(any(result['supportedFields'].values()))


@unittest.skipUnless(os.environ.get('CLOAK_WORKBENCH_REAL_TEST') == '1', 'Opt-in real kernel test')
class RealFingerprintTests(unittest.TestCase):
    def test_cpu_memory_screen_webgl_overrides_reach_web_page(self):
        from server import BrowserWorker
        binary = Path(os.environ.get('CLOAK_WORKBENCH_BROWSER', str(
            Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe')))
        with tempfile.TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), binary)
            try:
                worker.create(dict(id='owned-native-overrides', startUrl='about:blank', seed=24681,
                    locale='en-US', timezone='Europe/London', hardwareConcurrency=12, deviceMemory=4,
                    fingerprintScreenWidth=1600, fingerprintScreenHeight=900, taskbarHeight=40,
                    gpuVendor='Google Inc. (Intel)',
                    gpuRenderer='ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'))
                worker.launch('owned-native-overrides')
                report = inspect_environment(worker, 'owned-native-overrides')
                checked = {c['field']: c for c in report['checks']}
                for field in ('locale', 'timezone', 'fingerprintPlatform', 'hardwareConcurrency',
                              'deviceMemory', 'gpuVendor', 'gpuRenderer', 'fingerprintScreenWidth',
                              'fingerprintScreenHeight', 'taskbarHeight'):
                    with self.subTest(field=field):
                        self.assertEqual(checked[field]['status'], 'match', checked[field])
                self.assertEqual(len(report['actual']['canvasSHA256']), 64)
                self.assertNotIn('canvasData', report['actual'])
                self.assertEqual(checked['fingerprintNoise']['status'], 'unverified')
                worker.close('owned-native-overrides')
                worker.launch('owned-native-overrides')
                reopened = inspect_environment(worker, 'owned-native-overrides')
                self.assertEqual(report['actual']['canvasSHA256'], reopened['actual']['canvasSHA256'])
                worker.close('owned-native-overrides')
                worker.create({'id': 'owned-native-overrides', 'fingerprintNoise': False})
                worker.launch('owned-native-overrides')
                quiet = inspect_environment(worker, 'owned-native-overrides')
                self.assertNotEqual(report['actual']['canvasSHA256'], quiet['actual']['canvasSHA256'])
                worker.close('owned-native-overrides')
                worker.create({'id': 'owned-native-overrides', 'fingerprintPlatform': 'macos'})
                worker.launch('owned-native-overrides')
                mac = inspect_environment(worker, 'owned-native-overrides')
                self.assertEqual(mac['actual']['platform'], 'MacIntel')
            finally:
                worker.shutdown()


if __name__ == '__main__':
    unittest.main(verbosity=2)

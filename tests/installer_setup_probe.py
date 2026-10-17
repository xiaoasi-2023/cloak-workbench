"""Verify the real installer/uninstaller in an isolated project directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import winreg


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '.local' / 'installer-verification'
INSTALL = BASE / '安装目录 With Spaces'
STATE = BASE / '独立用户资料'
KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{0B6F0C77-5B51-485D-8F42-4990B894E766}_is1'


def registration():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return Path(winreg.QueryValueEx(key, 'InstallLocation')[0]).resolve()
    except FileNotFoundError:
        return None


def hashes():
    return {str(p.relative_to(STATE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (STATE / 'data').rglob('*') if p.is_file()}


def main():
    global BASE, INSTALL, STATE
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=BASE)
    args = parser.parse_args()
    BASE = args.out.resolve()
    assert BASE.is_relative_to((ROOT / '.local').resolve()), 'Verification must stay inside project .local'
    INSTALL, STATE = BASE / '安装目录 With Spaces', BASE / '独立用户资料'
    assert registration() is None, 'Existing user installation found; do not replace it'
    assert not INSTALL.exists(), 'Use a fresh dedicated verification directory'
    assert INSTALL.resolve().is_relative_to((ROOT / '.local').resolve())
    brand = json.loads((ROOT / 'release/branding.json').read_text(encoding='utf-8'))
    installer = ROOT / 'dist' / f'Qihang-Setup-{brand["version"]}-win-x64.exe'
    BASE.mkdir(parents=True, exist_ok=True)
    report = {'ok': False, 'checks': []}
    def run(*args):
        subprocess.run([str(a) for a in args], cwd=BASE, check=True, timeout=150,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    def check(label, condition):
        assert condition, label
        report['checks'].append(label)
    try:
        run(installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=',
            '/DIR=' + str(INSTALL), '/LOG=' + str(BASE / 'install.log'))
        check('Installer completed into Chinese and spaced path', (INSTALL / 'Qihang.exe').is_file())
        check('Per-user installation is registered correctly', registration() == INSTALL.resolve())
        run(sys.executable, ROOT / 'tests/installer_smoke.py', '--app', INSTALL / 'Qihang.exe',
            '--out', BASE / 'application', '--data-root', STATE)
        check('Installed application and bundled browsers passed smoke', json.loads((BASE / 'application/checks.json').read_text(encoding='utf-8'))['ok'])
        before = hashes()
        check('Application wrote only isolated test data', bool(before))
        run(INSTALL / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(BASE / 'uninstall.log'))
        check('Uninstaller removed the application', not (INSTALL / 'Qihang.exe').exists())
        check('Uninstaller removed only its registration', registration() is None)
        check('Uninstaller retained user data byte for byte', hashes() == before)
        report['ok'] = True
        print(f'{len(report["checks"])} installer lifecycle checks passed')
    finally:
        (BASE / 'checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

"""Build one offline Windows installer, for the owner's internal use only.

Build tools: project Python environment with PyInstaller, .NET SDK and ISCC.
The target PC needs none of these tools. No project data is copied into payload.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.local' / 'package-build'
PAYLOAD = BUILD / 'payload'
OUT = ROOT / 'dist'


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def fetch(url, destination):
    # Build-time public license/translation files, never user credentials.
    with urllib.request.urlopen(url, timeout=30) as response:
        destination.write_bytes(response.read())


def inno_string(value):
    return str(value).replace('"', '""')


def write_metadata(brand):
    version = tuple(int(part) for part in brand['fileVersion'].split('.'))
    entries = {
        'CompanyName': brand['author'], 'FileDescription': brand['description'],
        'FileVersion': brand['fileVersion'], 'InternalName': 'Qihang',
        'OriginalFilename': 'Qihang.exe', 'ProductName': brand['displayName'],
        'ProductVersion': brand['version'], 'LegalCopyright': brand['copyright'],
    }
    strings = ',\n'.join(f'StringStruct({key!r}, {value!r})' for key, value in entries.items())
    (BUILD / 'version-info.txt').write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={version!r},prodvers={version!r},mask=0x3f,flags=0,OS=0x40004,fileType=0x1,subtype=0,date=(0,0)),"
        f"kids=[StringFileInfo([StringTable('080404B0',[{strings}])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])", encoding='utf-8')


def collect_licenses():
    destination = PAYLOAD / 'licenses'
    destination.mkdir()
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', destination / 'Python.txt')
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        shutil.copy2(ROOT / '.tools/dotnet' / name, destination / ('dotnet-' + name))
    for name in ('google-auth', 'websockets', 'tzdata', 'requests', 'certifi', 'cryptography', 'cffi', 'pyasn1', 'pyasn1_modules', 'urllib3', 'idna', 'charset-normalizer', 'pyinstaller'):
        package = importlib.metadata.distribution(name)
        for resource in package.files or []:
            if resource.name.lower().startswith(('license', 'copying', 'notice', 'copyright')):
                source = package.locate_file(resource)
                if source.is_file():
                    package_dir = destination / name / resource.parent.name
                    package_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, package_dir / source.name)
    fetch('https://raw.githubusercontent.com/CloakHQ/CloakBrowser/main/BINARY-LICENSE.md', destination / 'CloakBrowser-Binary.md')
    shutil.copy2(ROOT / 'release/internal-use.txt', destination / 'internal-use.txt')


def build_payload(brand, browser):
    expected = 'chromium-' + brand['recommendedEngine']['version']
    if browser.name != expected or not (browser / 'chrome.exe').is_file() or not (browser / 'chrome.dll').is_file():
        raise RuntimeError('Provide the complete verified engine directory: ' + expected)
    BUILD.mkdir(parents=True, exist_ok=True)
    write_metadata(brand)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'Qihang', '--icon', ROOT / brand['icon'], '--version-file', BUILD / 'version-info.txt',
        '--paths', ROOT / 'worker', '--hidden-import', 'server',
        '--collect-data', 'tzdata', '--collect-data', 'certifi',
        '--collect-submodules', 'google.auth', '--collect-submodules', 'google.oauth2',
        '--copy-metadata', 'google-auth', '--add-data', str(ROOT / 'worker/video_page.js') + os.pathsep + '.',
        '--distpath', BUILD / 'frozen', '--workpath', BUILD / 'pyinstaller', '--specpath', BUILD,
        ROOT / 'launch.py')
    # Only remove this generated payload, never a user-selected data directory.
    resolved = PAYLOAD.resolve()
    if resolved != (ROOT / '.local/package-build/payload').resolve() or not resolved.is_relative_to(BUILD.resolve()):
        raise RuntimeError('Unexpected build output path')
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    shutil.copytree(BUILD / 'frozen/Qihang', PAYLOAD)
    run(ROOT / '.tools/dotnet/dotnet.exe', 'publish', ROOT / 'desktop/CloakWorkbench.csproj',
        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '-o', PAYLOAD / 'app', '--nologo',
        '--source', 'https://api.nuget.org/v3/index.json',
        '-p:DebugType=None', '-p:DebugSymbols=false', '-p:PublishSingleFile=false',
        '-p:ApplicationIcon=' + str(ROOT / brand['icon']), '-p:Version=' + brand['version'].split('-')[0],
        '-p:FileVersion=' + brand['fileVersion'], '-p:InformationalVersion=' + brand['version'],
        '-p:Product=' + brand['displayName'], '-p:Company=' + brand['author'])
    shutil.copytree(browser, PAYLOAD / 'engine' / browser.name,
                    ignore=shutil.ignore_patterns('*.log', 'First Run'))
    packaged_brand = dict(brand, status='internal-installer')
    (PAYLOAD / 'branding.json').write_text(json.dumps(packaged_brand, ensure_ascii=False, indent=2), encoding='utf-8')
    collect_licenses()
    freeze = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], cwd=ROOT, text=True)
    (PAYLOAD / 'licenses/build-dependencies.txt').write_text(freeze, encoding='utf-8')
    verify_payload()


def verify_payload():
    required = ('Qihang.exe', 'branding.json', '_internal/python313.dll', '_internal/video_page.js',
                'app/CloakWorkbench.exe', 'app/coreclr.dll', 'app/PresentationFramework.dll',
                'engine/chromium-146.0.7680.177.5/chrome.exe')
    for name in required:
        if not (PAYLOAD / name).is_file():
            raise RuntimeError('Incomplete payload: ' + name)
    forbidden = {'data', '.local', '.git', '.venv', '.tools', '__pycache__'}
    for child in PAYLOAD.rglob('*'):
        relative = child.relative_to(PAYLOAD)
        if any(part in forbidden for part in relative.parts):
            raise RuntimeError('Private/development directory in payload: ' + str(relative))
        if child.name.lower() in ('cookies', 'login data', 'session.json', 'workspaces.json', 'profiles.json', 'login-accounts.json'):
            raise RuntimeError('User data in payload: ' + str(relative))
    print('Payload verified: bundled runtimes and engine; no project user-data directories', flush=True)


def build_installer(brand, compiler):
    verify_payload()
    OUT.mkdir(exist_ok=True)
    script_dir = BUILD / 'installer'
    script_dir.mkdir(exist_ok=True)
    for name in ('installer.iss', 'internal-use.txt'):
        shutil.copy2(ROOT / 'release' / name, script_dir / name)
    shutil.copy2(ROOT / 'release/internal-use.txt', PAYLOAD / 'licenses/internal-use.txt')
    language = script_dir / 'ChineseSimplified.isl'
    if not language.exists():
        fetch('https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl', language)
    values = {
        'ProductName': brand['displayName'], 'ProductVersion': brand['version'].split('-')[0],
        'DisplayVersion': brand['version'], 'FileVersion': brand['fileVersion'],
        'Publisher': brand['author'], 'Homepage': brand['homepage'], 'SupportURL': brand['issueTracker'],
        'BrandIcon': ROOT / brand['icon'], 'OutputPath': OUT, 'PayloadPath': PAYLOAD,
    }
    (script_dir / 'build-config.iss').write_text('\n'.join(f'#define {key} "{inno_string(value)}"' for key, value in values.items()), encoding='utf-8-sig')
    run(compiler, script_dir / 'installer.iss')
    installer = OUT / f'Qihang-Setup-{brand["version"]}-win-x64.exe'
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (OUT / (installer.name + '.sha256')).write_text(f'{digest}  {installer.name}\n', encoding='ascii')
    print('Installer: ' + str(installer), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, default=Path.home() / '.cloakbrowser/chromium-146.0.7680.177.5')
    parser.add_argument('--compiler', type=Path, default=ROOT / '.tools/inno-portable/tools/ISCC.exe')
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--installer-only', action='store_true')
    args = parser.parse_args()
    brand = json.loads((ROOT / 'release/branding.json').read_text(encoding='utf-8'))
    if not args.installer_only:
        build_payload(brand, args.browser.resolve())
    if not args.stage_only:
        build_installer(brand, args.compiler.resolve())


if __name__ == '__main__':
    main()

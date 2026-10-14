"""Native kernel fingerprint options and read-only observed environment checks."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import mmap
from pathlib import Path
import re
from datetime import datetime, timezone


FLAG_FIELDS = {
    'fingerprintPlatform': 'fingerprint-platform',
    'fingerprintNoise': 'fingerprint-noise',
    'hardwareConcurrency': 'fingerprint-hardware-concurrency',
    'deviceMemory': 'fingerprint-device-memory',
    'gpuVendor': 'fingerprint-gpu-vendor',
    'gpuRenderer': 'fingerprint-gpu-renderer',
    'fingerprintScreenWidth': 'fingerprint-screen-width',
    'fingerprintScreenHeight': 'fingerprint-screen-height',
    'taskbarHeight': 'fingerprint-taskbar-height',
}


def _integer(payload, key, low, high):
    value = payload.get(key)
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{key} must be an integer from {low} to {high}, or empty for kernel default')
    return value


def validate_fingerprint(payload: dict) -> dict:
    platform = payload.get('fingerprintPlatform', 'windows')
    if platform not in ('windows', 'macos'):
        raise ValueError('fingerprintPlatform must be windows or macos')
    noise = payload.get('fingerprintNoise', True)
    if not isinstance(noise, bool):
        raise ValueError('fingerprintNoise must be a boolean')
    memory = payload.get('deviceMemory')
    if memory == '':
        memory = None
    if memory is not None and (isinstance(memory, bool) or not isinstance(memory, (int, float))
                               or memory not in (.25, .5, 1, 2, 4, 8)):
        raise ValueError('deviceMemory must be 0.25, 0.5, 1, 2, 4 or 8 GB, or empty')
    width = _integer(payload, 'fingerprintScreenWidth', 640, 7680)
    height = _integer(payload, 'fingerprintScreenHeight', 480, 4320)
    if (width is None) != (height is None):
        raise ValueError('Set both fingerprint screen dimensions or leave both empty')
    taskbar = _integer(payload, 'taskbarHeight', 0, 4320)
    if taskbar is not None and height is not None and taskbar >= height:
        raise ValueError('taskbarHeight must be smaller than fingerprint screen height')
    strings = {}
    for key in ('gpuVendor', 'gpuRenderer'):
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError(f'{key} must be text without control characters, up to 512 characters')
        strings[key] = value.strip()
    if bool(strings['gpuVendor']) != bool(strings['gpuRenderer']):
        raise ValueError('Set both WebGL vendor and renderer or leave both empty')
    return dict(fingerprintPlatform=platform, fingerprintNoise=noise,
                hardwareConcurrency=_integer(payload, 'hardwareConcurrency', 1, 64),
                deviceMemory=memory, fingerprintScreenWidth=width, fingerprintScreenHeight=height,
                taskbarHeight=taskbar, **strings)


def launch_args(profile: dict) -> list[str]:
    values = validate_fingerprint(profile)
    result = []
    for key, flag in FLAG_FIELDS.items():
        value = values[key]
        if value is None or value == '':
            continue
        # Noise is already on by default. Explicit false switches it off.
        if key == 'fingerprintNoise' and value:
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        result.append(f'--{flag}={value}')
    return result


@lru_cache(maxsize=8)
def _binary_flags(binary_text: str, modified: int, size: int) -> frozenset[str]:
    binary = Path(binary_text)
    library = binary.with_name('chrome.dll')
    source = library if library.is_file() else binary
    try:
        with source.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return frozenset(value.decode('ascii') for value in re.findall(rb'fingerprint-[a-z][a-z0-9-]{2,70}', data))
    except (OSError, ValueError):
        return frozenset()


def capabilities(binary: Path) -> dict:
    binary = Path(binary)
    source = binary.with_name('chrome.dll')
    if not source.exists():
        source = binary
    try:
        stat = source.stat()
        flags = _binary_flags(str(binary.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        flags = frozenset()
    version_match = re.search(r'chromium-([\d.]+)', str(binary.parent))
    return {
        'engineVersion': version_match.group(1) if version_match else 'unknown',
        'engineAvailable': binary.is_file(),
        'supportedFields': {key: flag in flags for key, flag in FLAG_FIELDS.items()},
        'canvasMode': 'seeded' if 'fingerprint-noise' in flags else 'unknown',
        'notes': [
            '内核参数存在只表示可配置；实际生效值请使用环境检查。',
            'Canvas、WebGL、音频噪声共用种子和噪声开关，不能独立任意指定 Canvas 哈希。',
            '初始窗口大小与网页暴露的屏幕分辨率分别设置。',
            '修改环境参数需要关闭并重新打开浏览器。',
            'macOS 平台特征不安装 macOS 字体，也不改变 Windows 系统。',
        ],
    }


def require_supported(profile: dict, binary: Path):
    supported = capabilities(binary)['supportedFields']
    values = validate_fingerprint(profile)
    for key, value in values.items():
        if value is None or value == '' or key == 'fingerprintNoise' and value is True:
            continue
        if key == 'fingerprintPlatform' and value == 'windows':
            continue
        if not supported[key]:
            raise ValueError(f'Installed kernel does not expose the required flag for {key}')


ENVIRONMENT_EXPRESSION = r"""(async()=>{
const canvas=document.createElement('canvas');canvas.width=320;canvas.height=80;
const ctx=canvas.getContext('2d');ctx.fillStyle='#197b72';ctx.fillRect(0,0,320,80);
ctx.font='20px Arial';ctx.fillStyle='#fff';ctx.fillText('Cloak Workbench 2026',12,44);
const canvasData=canvas.toDataURL();
const gl=document.createElement('canvas').getContext('webgl');
const ext=gl&&gl.getExtension('WEBGL_debug_renderer_info');
let uaData=null;try{uaData=navigator.userAgentData?{platform:navigator.userAgentData.platform,mobile:navigator.userAgentData.mobile,brands:navigator.userAgentData.brands}:null}catch{}
return {language:navigator.language,languages:[...navigator.languages],
timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,platform:navigator.platform,
userAgent:navigator.userAgent,hardwareConcurrency:navigator.hardwareConcurrency,
deviceMemory:navigator.deviceMemory??null,
screen:{width:screen.width,height:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight,colorDepth:screen.colorDepth},
viewport:{width:innerWidth,height:innerHeight,devicePixelRatio},
webgl:ext?{vendor:gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),renderer:gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)}:null,
canvasData,uaData};})()"""


def inspect_environment(worker, profile_id: str) -> dict:
    """Read current page globals and detached canvases without navigation or DOM edits."""
    profile = worker.profile(profile_id)
    runtime = worker.runtime.get(profile_id)
    if not runtime or runtime['process'].poll() is not None:
        raise ValueError('Open the browser environment before checking actual values')
    result = worker.command(runtime, 'Runtime.evaluate', {
        'expression': ENVIRONMENT_EXPRESSION, 'awaitPromise': True, 'returnByValue': True,
    })
    if result.get('exceptionDetails') or not isinstance(result.get('result', {}).get('value'), dict):
        raise ValueError('Current page cannot expose environment values; open the local verification page and retry')
    actual = result['result']['value']
    actual['canvasSHA256'] = hashlib.sha256(actual.pop('canvasData').encode('utf-8')).hexdigest()
    configured = {key: profile.get(key) for key in ('seed', 'locale', 'timezone', *FLAG_FIELDS)}
    checks = []

    def check(field, label, expected, observed, status=None):
        if status is None:
            status = 'observed' if expected is None or expected == '' else 'match' if expected == observed else 'mismatch'
        checks.append(dict(field=field, label=label, expected=expected, actual=observed, status=status))

    check('locale', '语言', profile['locale'], actual['language'])
    check('timezone', '时区', profile['timezone'], actual['timezone'])
    check('fingerprintPlatform', '平台', {'windows': 'Win32', 'macos': 'MacIntel'}[profile.get('fingerprintPlatform', 'windows')], actual['platform'])
    check('hardwareConcurrency', 'CPU 逻辑处理器', profile.get('hardwareConcurrency'), actual['hardwareConcurrency'])
    check('deviceMemory', '内存 GB', profile.get('deviceMemory'), actual['deviceMemory'])
    check('gpuVendor', 'WebGL 厂商', profile.get('gpuVendor'), (actual.get('webgl') or {}).get('vendor'))
    check('gpuRenderer', 'WebGL 渲染器', profile.get('gpuRenderer'), (actual.get('webgl') or {}).get('renderer'))
    check('fingerprintScreenWidth', '屏幕宽度', profile.get('fingerprintScreenWidth'), actual['screen']['width'])
    check('fingerprintScreenHeight', '屏幕高度', profile.get('fingerprintScreenHeight'), actual['screen']['height'])
    taskbar = profile.get('taskbarHeight')
    check('taskbarHeight', '保留区域高度', taskbar, actual['screen']['height'] - actual['screen']['availHeight'])
    check('canvasSHA256', 'Canvas 摘要', None, actual['canvasSHA256'])
    check('fingerprintNoise', '噪声开关', profile.get('fingerprintNoise', True), None, 'unverified')
    return dict(profileId=profile_id, configured=configured, actual=actual, checks=checks,
                verifiedAt=datetime.now(timezone.utc).isoformat(),
                matching=sum(c['status'] == 'match' for c in checks),
                mismatches=sum(c['status'] == 'mismatch' for c in checks),
                notes=['只读取当前网页和脱离页面的 Canvas，不导航、不修改输入或登录数据。',
                       '观测值不是电脑真实硬件清单；噪声开关需要多次渲染对照，单次读数不作为生效证明。'])

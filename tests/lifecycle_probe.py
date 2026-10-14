"""Close only this prototype and verify owned process cleanup before relaunch."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import subprocess
import time
import urllib.request

root = Path(__file__).resolve().parents[1]
local = root / '.local'
session = json.loads((local / 'session.json').read_text(encoding='utf-8'))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request('http://127.0.0.1:8766/profiles', headers={'Authorization': 'Bearer ' + session['token']})
with opener.open(req, timeout=5) as response:
    browser_pids = [p['pid'] for p in json.load(response)['profiles'] if p.get('pid')]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
handles = [(pid, kernel.OpenProcess(0x1000, False, pid)) for pid in browser_pids + [session['workerPid'], session['desktopPid']]]
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
windows = []
@CB
def callback(hwnd, _):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == session['desktopPid']:
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, title, 512)
        if title.value.startswith('Cloak Workbench'):
            windows.append(hwnd)
    return True
user.EnumWindows(callback, 0)
assert len(windows) == 1, 'Could not uniquely identify prototype window'
user.PostMessageW(windows[0], 0x10, 0, 0)
remaining = []
for _ in range(100):
    remaining = []
    for pid, handle in handles:
        code = wintypes.DWORD()
        if handle and kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259:
            remaining.append(pid)
    if not remaining:
        break
    time.sleep(.2)
for _, handle in handles:
    if handle: kernel.CloseHandle(handle)
assert not remaining, f'Owned processes did not exit: {remaining}'
report = {'gracefulCleanup': True, 'ownedBrowserProcessesClosed': len(browser_pids), 'workerClosed': True, 'desktopClosed': True}
(local / 'lifecycle-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
(local / 'smoke-result.json').unlink(missing_ok=True)
python = root / '.venv/Scripts/python.exe'
subprocess.Popen([str(python), str(root / 'launch.py'), '--smoke'], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
for _ in range(200):
    result = local / 'smoke-result.json'
    if result.exists():
        smoke = json.loads(result.read_text(encoding='utf-8-sig'))
        assert smoke['ok'], smoke
        print(json.dumps(dict(report, restarted=True)))
        break
    time.sleep(.2)
else:
    raise AssertionError('Restart smoke did not finish')

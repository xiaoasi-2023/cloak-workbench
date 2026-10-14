"""List titles and handles only for this workbench's WPF process."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
user = ctypes.WinDLL('user32')
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user.IsWindowVisible.argtypes = [wintypes.HWND]
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
windows = []
@callback_type
def callback(hwnd, _):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == session['desktopPid'] and user.IsWindowVisible(hwnd):
        title = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, title, 256)
        windows.append({'hwnd': int(hwnd), 'title': title.value})
    return True
user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user.EnumWindows(callback, 0)
print(json.dumps(windows, ensure_ascii=True))

"""Capture only a known workbench dialog; no user field contents are logged."""
from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import json
from native_probe import capture_window

parser = argparse.ArgumentParser()
parser.add_argument('hwnd', type=int)
parser.add_argument('out', type=Path)
parser.add_argument('--close', action='store_true', help='Request closure of this known reversible test dialog after capture')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
user.GetParent.argtypes = [wintypes.HWND]
user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user.GetAncestor.restype = wintypes.HWND
pid = wintypes.DWORD()
user.GetWindowThreadProcessId(args.hwnd, ctypes.byref(pid))
if pid.value != session['desktopPid']:
    raise RuntimeError('Dialog owner changed')
title = ctypes.create_unicode_buffer(256)
user.GetWindowTextW(args.hwnd, title, 256)
if title.value not in ('新建浏览器环境', '环境验证', '环境下载文件', '浏览器弹窗'):
    raise RuntimeError('Known test dialog required')
print(json.dumps(capture_window(user, args.hwnd, args.out)))
if args.close:
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW(args.hwnd, 0x10, 0, 0)

"""Scoped native mouse/Unicode input smoke against the selected offline probe.

CDP reads geometry and resulting state only. Win32 window messages perform the
input. This deliberately does not claim physical keyboard or IME composition.
No navigation, profile restart, clipboard write, or storage write is performed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from websockets.sync.client import connect


class Rect(ctypes.Structure):
    _fields_ = [(key, ctypes.c_long) for key in ("left", "top", "right", "bottom")]


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / ".local" / "native-input-result.json"
    checks = []
    report = {"ok": False, "checks": checks, "inputMethod": "owned-window Win32 messages",
              "notVerified": ["physical mouse and keyboard", "Chinese IME composition",
                              "input under other DPI scales", "upload/download dialogs"]}

    def check(name, passed):
        checks.append({"check": name, "ok": bool(passed)})
        if not passed:
            raise RuntimeError(name)

    try:
        if os.name != "nt":
            raise RuntimeError("Windows only")
        session = json.loads((root / ".local" / "session.json").read_text(encoding="utf-8-sig"))
        ui = json.loads((root / ".local" / "ui-state.json").read_text(encoding="utf-8-sig"))
        opener = build_opener(ProxyHandler({}))
        request = Request("http://127.0.0.1:8766/profiles", headers={"Authorization": "Bearer " + session["token"]})
        with opener.open(request, timeout=5) as response:
            state = json.load(response)
        selected = ui["selected"]
        browser = next(item for item in ui["browsers"] if item["id"] == selected)
        profile = next(item for item in state["profiles"] if item["id"] == selected)
        hwnd = browser["hwnd"]
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowThreadProcessId.restype = wintypes.DWORD
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(Point)]
        user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(Point)]
        user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW.restype = wintypes.LPARAM

        def owner(window):
            value = wintypes.DWORD()
            user.GetWindowThreadProcessId(window, ctypes.byref(value))
            return value.value

        app = user.GetAncestor(hwnd, 2)
        check("selected HWND belongs to launched profile", owner(hwnd) == profile["pid"])
        check("embedded root belongs to current desktop", owner(app) == session["desktopPid"])
        check("selected browser visible", user.IsWindowVisible(hwnd))
        with opener.open(f"http://127.0.0.1:{profile['cdpPort']}/json/list", timeout=5) as response:
            targets = json.load(response)
        target = next(item for item in targets if item.get("type") == "page"
                      and urlsplit(item.get("url", "")).hostname == "127.0.0.1"
                      and urlsplit(item["url"]).path == "/probe")
        report["profile"] = selected
        children = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(child, _):
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(child, name, 256)
            if name.value == "Chrome_RenderWidgetHostHWND" and user.IsWindowVisible(child):
                children.append(child)
            return True

        user.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        user.EnumChildWindows(hwnd, callback, 0)
        check("one visible Chromium input HWND", len(children) == 1)
        render = children[0]
        check("input HWND belongs to launched profile", owner(render) == profile["pid"])
        check("input HWND embedded in owned desktop", user.GetAncestor(render, 2) == app)
        current_thread = kernel.GetCurrentThreadId()
        own_thread = user.GetWindowThreadProcessId(app, None)
        linked = own_thread != current_thread and user.AttachThreadInput(current_thread, own_thread, True)
        try:
            user.SetForegroundWindow(app)
        finally:
            if linked:
                user.AttachThreadInput(current_thread, own_thread, False)
        report["foregroundActivationSucceeded"] = user.GetForegroundWindow() == app

        with connect(target["webSocketDebuggerUrl"], open_timeout=5, close_timeout=1, proxy=None) as ws:
            sequence = 0

            def read():
                nonlocal sequence
                sequence += 1
                expression = """(() => {const e=document.querySelector('#entry'); if(!e)return null;
                    const r=e.getBoundingClientRect(); return {value:e.value,focused:document.activeElement===e,
                    x:r.x,y:r.y,width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight};})()"""
                ws.send(json.dumps({"id": sequence, "method": "Runtime.evaluate", "params": {
                    "expression": expression, "returnByValue": True}}))
                while True:
                    message = json.loads(ws.recv(timeout=5))
                    if message.get("id") == sequence:
                        return message.get("result", {}).get("result", {}).get("value")

            sample = "Native \u4e2d\u6587 2026"

            def clear_test_text(count):
                for _ in range(count):
                    user.SendMessageW(render, 0x100, 8, 0x000E0001)  # WM_KEYDOWN VK_BACK
                    user.SendMessageW(render, 0x101, 8, 0xC00E0001)  # WM_KEYUP VK_BACK
                time.sleep(.15)

            before = read()
            if before and before["focused"] and before["value"] == sample:
                clear_test_text(len(sample))  # Recover only this test's exact residue.
                before = read()
            check("empty local test field (preserve user data)", before is not None and before["value"] == "")
            bounds = Rect()
            check("input surface geometry available", user.GetClientRect(render, ctypes.byref(bounds)))
            px = int((before["x"] + before["width"] / 2) * bounds.right / before["viewportWidth"])
            py = int((before["y"] + before["height"] / 2) * bounds.bottom / before["viewportHeight"])
            check("click inside browser input surface", 0 <= px < bounds.right and 0 <= py < bounds.bottom)
            position = (py << 16) | (px & 0xffff)
            user.SendMessageW(render, 0x200, 0, position)  # WM_MOUSEMOVE
            user.SendMessageW(render, 0x201, 1, position)  # WM_LBUTTONDOWN
            user.SendMessageW(render, 0x202, 0, position)  # WM_LBUTTONUP
            time.sleep(.2)
            after_click = read()
            check("native mouse messages focus local input", after_click["focused"])
            try:
                for character in sample:
                    user.SendMessageW(render, 0x102, ord(character), 1)  # WM_CHAR
                time.sleep(.2)
                typed = read()
                report["expectedText"] = sample
                report["actualText"] = typed["value"]
                check("native Unicode WM_CHAR reaches input", typed["value"] == sample)
            finally:
                # Backspace messages restore only this previously-empty local field.
                observed = read()
                if observed and observed["focused"] and observed["value"] == sample:
                    clear_test_text(len(sample))
                    report["fieldRestored"] = read()["value"] == ""
            check("temporary field contents restored", report.get("fieldRestored"))
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

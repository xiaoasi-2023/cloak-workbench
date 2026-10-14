"""Real SendInput regression on the owned workbench's local /probe only.

CDP reads target identity, geometry and field state and installs/removes passive
event listeners. It never assigns input values or dispatches input. No posted
keyboard messages or WM_CHAR are used. Clipboard data is read without logging
its text and never changed. The initially empty test field and mouse position
are restored through native input.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener
from websockets.sync.client import connect


class Point(ctypes.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class Rect(ctypes.Structure):
    _fields_ = [(f, w.LONG) for f in ("left", "top", "right", "bottom")]


class Mouse(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class Keyboard(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class Hardware(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class Union(ctypes.Union):
    _fields_ = [("mi", Mouse), ("ki", Keyboard), ("hi", Hardware)]


class Input(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("value", Union)]


class GuiThread(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD)] + [
        (f, w.HWND) for f in ("hwndActive", "hwndFocus", "hwndCapture", "hwndMenuOwner", "hwndMoveSize", "hwndCaret")
    ] + [("rcCaret", Rect)]


class Placement(ctypes.Structure):
    _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
                ("ptMinPosition", Point), ("ptMaxPosition", Point), ("rcNormalPosition", Rect)]


class Monitor(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", w.DWORD)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--out", type=Path, default=Path(".local/physical-input-before.json"))
    parser.add_argument("--extended", action="store_true", help="Test address editor, return to browser, and 2.6-second polling")
    parser.add_argument("--restore-demo", action="store_true", help="Explicitly recover only the verified demo browser from minimized state")
    parser.add_argument("--scan-code", action="store_true", help="Use real keyboard scan-code SendInput instead of virtual-key SendInput")
    parser.add_argument("--english-layout", action="store_true", help="Temporarily request an already installed US layout only on owned focused threads, then restore")
    parser.add_argument("--secondary-monitor", action="store_true", help="Position the owned test window on the existing left monitor to avoid a primary-monitor OS overlay")
    parser.add_argument("--reclick-before-paste", action="store_true", help="Isolate immediate paste with an extra real mouse click; omit for natural end-to-end regression")
    parser.add_argument("--ime-only", action="store_true", help="Measure native NI/Space Chinese composition in an empty owned fixture")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows required")
    session = json.loads((args.root / ".local/session.json").read_text(encoding="utf-8-sig"))
    snapshot = json.loads((args.root / ".local/ui-state.json").read_text(encoding="utf-8-sig"))
    allowlist = {"demo-a", "demo-b", "input-regression"}
    fresh_marker = args.root / ".local/input-fixture-fresh.json"
    fresh = json.loads(fresh_marker.read_text(encoding="utf-8")) if fresh_marker.exists() else None
    if fresh and fresh.get("id") == "input-regression-2":
        allowlist.add(fresh["id"])
    if snapshot["selected"] not in allowlist:
        parser.error("Select an owned regression local /probe first; user profiles are never tested")
    host = next(b for b in snapshot["browsers"] if b["id"] == snapshot["selected"])
    opener = build_opener(ProxyHandler({}))
    req = Request("http://127.0.0.1:8766/profiles", headers={"Authorization": "Bearer " + session["token"]})
    with opener.open(req, timeout=5) as response:
        profile = next(p for p in json.load(response)["profiles"] if p["id"] == snapshot["selected"])
    if profile["id"] == "input-regression-2" and any(profile.get(k) != v for k, v in fresh.items()):
        parser.error("Fresh fixture ownership/configuration changed; no input sent")
    with opener.open(f"http://127.0.0.1:{profile['cdpPort']}/json/list", timeout=5) as response:
        targets = json.load(response)
    target = next((p for p in targets if p.get("type") == "page"
                   and urlsplit(p.get("url", "")).hostname == "127.0.0.1"
                   and urlsplit(p["url"]).path == "/probe"), None)
    if not target:
        parser.error("Local /probe target required")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    imm = ctypes.WinDLL("imm32", use_last_error=True)
    imm.ImmGetContext.argtypes = [w.HWND]
    imm.ImmGetContext.restype = w.HANDLE
    imm.ImmGetOpenStatus.argtypes = [w.HANDLE]
    imm.ImmGetConversionStatus.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]
    imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    user.GetParent.argtypes = [w.HWND]
    user.GetParent.restype = w.HWND
    user.GetAncestor.argtypes = [w.HWND, w.UINT]
    user.GetAncestor.restype = w.HWND
    user.GetForegroundWindow.restype = w.HWND
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThread)]
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(Rect)]
    user.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(Rect)]
    user.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
    user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
    user.MoveWindow.argtypes = [w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL]
    user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    user.WindowFromPoint.argtypes = [Point]
    user.WindowFromPoint.restype = w.HWND
    user.IsChild.argtypes = [w.HWND, w.HWND]
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
    user.GetClipCursor.argtypes = [ctypes.POINTER(Rect)]
    user.GetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(Placement)]
    user.SetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(Placement)]
    user.MonitorFromPoint.argtypes = [Point, w.DWORD]
    user.MonitorFromPoint.restype = w.HANDLE
    user.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(Monitor)]
    user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
    user.GetKeyboardLayout.argtypes = [w.DWORD]
    user.GetKeyboardLayout.restype = w.HANDLE
    user.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(w.HANDLE)]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.ClientToScreen.argtypes = [w.HWND, ctypes.POINTER(Point)]
    user.EnumChildWindows.argtypes = [w.HWND, ctypes.c_void_p, w.LPARAM]
    user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.OpenClipboard.argtypes = [w.HWND]
    user.GetClipboardData.argtypes = [w.UINT]
    user.GetClipboardData.restype = w.HANDLE
    user.GetClipboardSequenceNumber.restype = w.DWORD
    kernel.GlobalLock.argtypes = [w.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [w.HANDLE]
    app = user.GetAncestor(user.GetParent(host["hwnd"]), 2)

    def owner(hwnd):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    if owner(app) != session["desktopPid"] or owner(host["hwnd"]) != profile["pid"]:
        parser.error("Ownership changed; no input sent")
    allowed = {session["desktopPid"], profile["pid"]}
    checks, focus_samples, notes = [], [], []
    def read_clipboard():
        for _ in range(2):
            if user.OpenClipboard(None):
                try:
                    handle = user.GetClipboardData(13)
                    if handle:
                        ptr = kernel.GlobalLock(handle)
                        if ptr:
                            try:
                                return ctypes.wstring_at(ptr), user.GetClipboardSequenceNumber()
                            finally:
                                kernel.GlobalUnlock(handle)
                finally:
                    user.CloseClipboard()
            time.sleep(.05)
        return None, user.GetClipboardSequenceNumber()

    clipboard, clipboard_sequence = read_clipboard()
    if clipboard == "":
        clipboard = None
        notes.append("Existing clipboard is empty; paste cannot provide meaningful evidence")
    if clipboard is not None and len(clipboard) > 4096:
        clipboard = None
        notes.append("Existing clipboard text is too large; paste skipped")
    if clipboard is None:
        notes.append("Existing CF_UNICODETEXT unavailable; only native paste event can be verified")
    old_cursor = Point()
    user.GetCursorPos(ctypes.byref(old_cursor))
    placement = Placement()
    placement.length = ctypes.sizeof(placement)
    user.GetWindowPlacement(app, ctypes.byref(placement))
    clip = Rect()
    user.GetClipCursor(ctypes.byref(clip))
    info = Monitor()
    info.cbSize = ctypes.sizeof(info)
    user.GetMonitorInfoW(user.MonitorFromPoint(Point(-100, 100) if args.secondary_monitor else Point(0, 0), 1), ctypes.byref(info))
    original_topmost = bool(user.GetWindowLongW(app, -20) & 8)
    pressed = set()
    restored_layouts = {}
    observed_focus_hwnds = {}
    if args.english_layout or args.ime_only:
        for initial_hwnd in (app, host["hwnd"]):
            initial_thread = user.GetWindowThreadProcessId(initial_hwnd, None)
            restored_layouts[initial_thread] = (initial_hwnd, int(user.GetKeyboardLayout(initial_thread) or 0))
    initially_empty = False
    report = {"ok": False, "inputTransport": "SendInput " + ("scan-code" if args.scan_code else "virtual-key") + " + native mouse", "clipboardModified": False,
              "profileId": profile["id"], "desktopPid": session["desktopPid"], "browserPid": profile["pid"],
              "clipboardTextLength": len(clipboard) if clipboard is not None else 0,
              "cursorClip": [clip.left, clip.top, clip.right, clip.bottom],
              "monitorWorkArea": [info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom],
              "checks": checks, "focus": focus_samples, "notes": notes}
    with connect(target["webSocketDebuggerUrl"], open_timeout=3, close_timeout=1, proxy=None) as ws:
        request_id = 0

        def evaluate(expression):
            nonlocal request_id
            request_id += 1
            ws.send(json.dumps({"id": request_id, "method": "Runtime.evaluate", "params": {
                "expression": expression, "returnByValue": True}}))
            while True:
                result = json.loads(ws.recv(timeout=5))
                if result.get("id") == request_id:
                    return result.get("result", {}).get("result", {}).get("value")

        def event_evidence():
            return evaluate("window.__physicalInputObserver?.events||[]") or []

        def observe_events():
            # Passive measurement only: listeners never prevent events or set
            # input values, and neither input nor clipboard contents are logged.
            evaluate("""(()=>{const e=document.querySelector('#entry');const types=['keydown','beforeinput','input','compositionstart','compositionupdate','compositionend','paste'];const events=[];const listener=x=>events.push({type:x.type,code:x.code||'',keyLength:(x.key||'').length,isComposing:!!x.isComposing,inputType:x.inputType||'',dataLength:typeof x.data==='string'?x.data.length:0,clipboardTypes:x.type==='paste'?Array.from(x.clipboardData?.types||[]):[]});for(const type of types)e.addEventListener(type,listener);window.__physicalInputObserver={e,types,events,listener};return true})()""")
            report["cdpUsage"] = "Read state and install passive event observer only; native SendInput supplies all interaction"

        def inspect():
            nonlocal request_id
            request_id += 1
            ws.send(json.dumps({"id": request_id, "method": "Runtime.evaluate", "params": {
                "expression": """(()=>{const e=document.querySelector('#entry');if(!e)return null;const r=e.getBoundingClientRect();return {value:e.value,active:document.activeElement===e,documentFocus:document.hasFocus(),activeTag:document.activeElement?.tagName,activeId:document.activeElement?.id,hitId:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.id||'',rect:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{w:innerWidth,h:innerHeight},dpr:devicePixelRatio,url:location.href}})()""",
                "returnByValue": True}}))
            while True:
                result = json.loads(ws.recv(timeout=5))
                if result.get("id") == request_id:
                    return result.get("result", {}).get("result", {}).get("value")

        def check(name, ok):
            checks.append({"check": name, "ok": bool(ok)})

        def safe_focus(require_focus=True):
            fg = user.GetForegroundWindow()
            if owner(fg) not in allowed or (user.GetAncestor(fg, 2) != app and fg != host["hwnd"]):
                raise RuntimeError("Foreground moved outside owned workbench; input aborted")
            info = GuiThread()
            info.cbSize = ctypes.sizeof(info)
            thread = user.GetWindowThreadProcessId(fg, None)
            if not user.GetGUIThreadInfo(thread, ctypes.byref(info)):
                raise RuntimeError("Cannot verify OS focus")
            focus_pid = owner(info.hwndFocus) if info.hwndFocus else 0
            focus_thread = user.GetWindowThreadProcessId(info.hwndFocus, None) if info.hwndFocus else thread
            if focus_pid in allowed:
                observed_focus_hwnds.setdefault(focus_thread, set()).add(int(info.hwndFocus))
            focus_samples.append({"foregroundPid": owner(fg), "focusPid": focus_pid, "focusHwnd": int(info.hwndFocus or 0),
                                  "keyboardLayout": int(user.GetKeyboardLayout(focus_thread) or 0)})
            context = imm.ImmGetContext(info.hwndFocus) if info.hwndFocus else None
            if context:
                conversion, sentence = w.DWORD(), w.DWORD()
                valid = bool(imm.ImmGetConversionStatus(context, ctypes.byref(conversion), ctypes.byref(sentence)))
                focus_samples[-1]["ime"] = {"open": bool(imm.ImmGetOpenStatus(context)),
                                            "conversionAvailable": valid, "conversion": conversion.value,
                                            "sentence": sentence.value}
                imm.ImmReleaseContext(info.hwndFocus, context)
            if require_focus and focus_pid not in allowed:
                raise RuntimeError("Focus moved outside owned workbench; input aborted")
            return info

        def send(event):
            if user.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
                raise ctypes.WinError(ctypes.get_last_error())

        def english_layout():
            if not args.english_layout:
                return
            focus = safe_focus().hwndFocus
            thread = user.GetWindowThreadProcessId(focus, None)
            original = int(user.GetKeyboardLayout(thread) or 0)
            count = user.GetKeyboardLayoutList(0, None)
            layouts = (w.HANDLE * count)()
            user.GetKeyboardLayoutList(count, layouts)
            requested = next((int(h) for h in layouts if (int(h) >> 16) & 0xffff == 0x0409), None)
            if requested is None:
                raise RuntimeError("No already installed US keyboard layout; no system layout was added")
            restored_layouts.setdefault(thread, (focus, original))
            if not user.PostMessageW(focus, 0x50, 0, requested):
                raise RuntimeError("Owned thread input language request failed")
            for _ in range(20):
                if int(user.GetKeyboardLayout(thread) or 0) == requested:
                    break
                time.sleep(.05)
            actual = int(user.GetKeyboardLayout(thread) or 0)
            report.setdefault("temporaryLayouts", []).append({"thread": thread, "original": original,
                                                             "requested": requested, "actual": actual})
            if actual != requested:
                raise RuntimeError("Owned focused thread did not accept the existing US layout")

        def key(vk, down):
            safe_focus()
            scan = user.MapVirtualKeyW(vk, 0) if args.scan_code else 0
            flags = (8 if args.scan_code else 0) | (0 if down else 2)
            send(Input(1, Union(ki=Keyboard(0 if args.scan_code else vk, scan, flags, 0, 0))))
            if down:
                pressed.add(vk)
            else:
                pressed.discard(vk)
            time.sleep(.06)

        def press(vk):
            key(vk, True)
            key(vk, False)

        def clear():
            if not inspect()["active"]:
                raise RuntimeError("Local test field lost DOM focus; clear aborted")
            key(0x11, True)
            press(0x41)
            key(0x11, False)
            press(8)
            time.sleep(.2)

        def paste_matches(value):
            # Chromium's single-line text editor pastes line breaks as spaces,
            # whereas the HTML value sanitizer can remove them in other paths.
            normalized = clipboard.replace("\r\n", "\n").replace("\r", "\n")
            expected = (normalized.replace("\n", ""), normalized.replace("\n", " "))
            report["pasteReadOnlyEvidence"] = {"clipboardLength": len(clipboard), "observedLength": len(value),
                                              "lineBreaks": normalized.count("\n"),
                                              "sequenceUnchanged": user.GetClipboardSequenceNumber() == clipboard_sequence,
                                              "matchesRemovedNewlines": value == expected[0],
                                              "matchesSpaceNewlines": value == expected[1]}
            return value in expected and user.GetClipboardSequenceNumber() == clipboard_sequence

        def native_click(point):
            if not user.SetCursorPos(point.x, point.y):
                raise RuntimeError("SetCursorPos failed; no global click sent")
            actual = Point()
            user.GetCursorPos(ctypes.byref(actual))
            if (actual.x, actual.y) != (point.x, point.y):
                report["cursorMismatch"] = {"requested": [point.x, point.y], "actual": [actual.x, actual.y]}
                raise RuntimeError("Cursor was clamped/moved away from owned target; no global click sent")
            target_hwnd = user.WindowFromPoint(actual)
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(target_hwnd, name, len(name))
            report.setdefault("mouseHits", []).append({"x": point.x, "y": point.y, "hwnd": int(target_hwnd or 0),
                                                        "pid": owner(target_hwnd), "class": name.value})
            if owner(target_hwnd) not in allowed or user.GetAncestor(target_hwnd, 2) != app:
                raise RuntimeError("Mouse target is not owned workbench; click aborted")
            send(Input(0, Union(mi=Mouse(0, 0, 0, 2, 0, 0))))
            send(Input(0, Union(mi=Mouse(0, 0, 0, 4, 0, 0))))
            time.sleep(.25)

        def address_state():
            # UIAutomation READS only the WPF process's native address editor.
            # It does not Invoke/SetValue or query another browser profile.
            code = """
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot = [System.Windows.Automation.AutomationElement]::FromHandle([intptr]%d)
$taskPid = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]%d)
$taskType = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
$taskCondition = [System.Windows.Automation.AndCondition]::new($taskPid, $taskType)
$taskEdit = $taskRoot.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $taskCondition)
if ($null -eq $taskEdit) { throw 'WPF address editor missing' }
$taskPattern = $taskEdit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$taskRect = $taskEdit.Current.BoundingRectangle
[pscustomobject]@{value=$taskPattern.Current.Value; x=$taskRect.X; y=$taskRect.Y; width=$taskRect.Width; height=$taskRect.Height} | ConvertTo-Json -Compress
""" % (app, session["desktopPid"])
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                                    check=True, capture_output=True, encoding="utf-8-sig", timeout=8)
            return json.loads(result.stdout)

        def click_entry():
            value = inspect()
            report["expectedDomHit"] = value["hitId"]
            r = value["rect"]
            point = Point(round((r["x"] + r["width"]/2) * value["dpr"]), round((r["y"] + r["height"]/2) * value["dpr"]))
            if render == host["hwnd"]:
                # Native UIA content geometry is in physical screen pixels.
                # The app frame may have negative clipping offsets, and neither
                # JS innerHeight nor fingerprint devicePixelRatio is a physical
                # coordinate conversion. Re-read after moving/resizing the shell.
                code = """
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskFrame = [System.Windows.Automation.AutomationElement]::FromHandle([intptr]%d)
$taskView = $taskFrame.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'MultiContentsView'))
if ($null -eq $taskView -or $taskView.Current.ProcessId -ne %d) { throw 'Owned native browser content view unavailable' }
$taskBounds = $taskView.Current.BoundingRectangle
if ($taskBounds.IsEmpty) { throw 'Native browser viewport is empty' }
[pscustomobject]@{x=$taskBounds.X;y=$taskBounds.Y;width=$taskBounds.Width;height=$taskBounds.Height} | ConvertTo-Json -Compress
""" % (host["hwnd"], profile["pid"])
                native_result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                    check=True, capture_output=True, encoding="utf-8-sig", timeout=8)
                content = json.loads(native_result.stdout)
                nonlocal request_id
                request_id += 1
                ws.send(json.dumps({"id": request_id, "method": "Page.getLayoutMetrics", "params": {}}))
                while True:
                    message = json.loads(ws.recv(timeout=5))
                    if message.get("id") == request_id:
                        layout = message["result"].get("cssLayoutViewport", message["result"]["layoutViewport"])
                        break
                if layout["clientWidth"] <= 0 or layout["clientHeight"] <= 0:
                    raise RuntimeError("CDP native layout viewport is empty")
                point = Point(round(content["x"] + (r["x"] + r["width"]/2) * content["width"] / layout["clientWidth"]),
                              round(content["y"] + (r["y"] + r["height"]/2) * content["height"] / layout["clientHeight"]))
                report["entryGeometry"] = {"rect": r, "jsViewport": value["viewport"], "layout": layout,
                                            "nativeContent": content, "dpr": value["dpr"]}
            else:
                user.ClientToScreen(render, ctypes.byref(point))
            report["entryPoint"] = {"x": point.x, "y": point.y}
            native_click(point)

        def extended():
            nonlocal clipboard, clipboard_sequence
            state = address_state()
            saved = state["value"]
            if saved != target["url"]:
                raise RuntimeError("Address editor contains a pending user edit; extended test aborted")
            native_click(Point(round(state["x"] + state["width"]/2), round(state["y"] + state["height"]/2)))
            english_layout()
            key(0x11, True)
            press(0x41)
            key(0x11, False)
            for vk in (0x58, 0x59, 0x5A):
                press(vk)
            check("Real address-bar keyboard input remains functional", address_state()["value"].casefold() == "xyz")
            # Restore through the native TextBox undo stack, never UIA SetValue.
            for _ in range(8):
                if address_state()["value"] == saved:
                    break
                key(0x11, True)
                press(0x5A)
                key(0x11, False)
            check("Address editor restored without navigation", address_state()["value"] == saved)
            if address_state()["value"] != saved:
                raise RuntimeError("Native address undo did not restore the original URL")
            click_entry()
            time.sleep(2.6)
            english_layout()
            for vk in (0x41, 0x42, 0x43):
                press(vk)
            typed = inspect()["value"]
            check("Browser real keyboard survives address focus and polling", typed.casefold() == "abc")
            clear()
            if clipboard is not None:
                clipboard, clipboard_sequence = read_clipboard()
                if not clipboard or len(clipboard) > 4096:
                    raise RuntimeError("Existing clipboard no longer supplies bounded nonempty text; extended paste not verified")
                key(0x11, True)
                press(0x56)
                key(0x11, False)
                time.sleep(.3)
                check("Browser real paste survives address focus and polling", paste_matches(inspect()["value"]))
                clear()
            check("Extended local test field restored empty", inspect()["value"] == "")
            time.sleep(5)
            for vk in (0x44, 0x45, 0x46):
                press(vk)
            check("Browser real keyboard remains functional after five-second idle", inspect()["value"].casefold() == "def")
            clear()
            check("Five-second idle test field restored empty", inspect()["value"] == "")

        def native_geometry():
            bounds, client = Rect(), Rect()
            user.GetWindowRect(host["hwnd"], ctypes.byref(bounds))
            user.GetClientRect(host["hwnd"], ctypes.byref(client))
            style = user.GetWindowLongW(host["hwnd"], -16)
            return {"bounds": [bounds.left, bounds.top, bounds.right, bounds.bottom],
                    "clientWidth": client.right, "clientHeight": client.bottom,
                    "minimizedStyle": bool(style & 0x20000000)}

        try:
            initial = inspect()
            if not initial or initial["value"]:
                raise RuntimeError("The demo test field must be empty; existing user text is preserved")
            initially_empty = True
            observe_events()
            geometry = native_geometry()
            report["initialNativeGeometry"] = geometry
            if args.restore_demo:
                parent = user.GetParent(host["hwnd"])
                if parent != host["parent"] or owner(parent) != session["desktopPid"] or user.GetAncestor(parent, 2) != app:
                    raise RuntimeError("Demo browser parent ownership changed; recovery aborted")
                parent_rect = Rect()
                user.GetClientRect(parent, ctypes.byref(parent_rect))
                user.ShowWindow(host["hwnd"], 9)
                user.MoveWindow(host["hwnd"], 0, 0, parent_rect.right, parent_rect.bottom, True)
                time.sleep(.3)
                geometry = native_geometry()
                report["restoredNativeGeometry"] = geometry
            if geometry["clientWidth"] < 400 or geometry["clientHeight"] < 200 or geometry["minimizedStyle"]:
                raise RuntimeError("Owned browser is collapsed/minimized; restore it before native input testing")
            user.ShowWindow(app, 9)
            work = info.rcWork
            width = min(1440, work.right - work.left - 40)
            height = min(900, work.bottom - work.top - 40)
            if width < 1050 or height < 680:
                raise RuntimeError("Monitor work area cannot contain the workbench minimum size")
            user.SetWindowPos(app, None, work.left + 20, work.top + 20, width, height, 0x14)
            user.SetWindowPos(app, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x13)
            user.SetForegroundWindow(app)
            time.sleep(.2)
            app_rect = Rect()
            user.GetWindowRect(app, ctypes.byref(app_rect))
            native_click(Point(app_rect.left + 240, app_rect.top + 60))
            report["postActivationNativeGeometry"] = native_geometry()
            safe_focus(False)
            renders = []
            browser_children = []
            callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

            @callback_type
            def find_render(hwnd, _):
                name = ctypes.create_unicode_buffer(256)
                user.GetClassNameW(hwnd, name, len(name))
                browser_children.append({"hwnd": int(hwnd), "class": name.value, "pid": owner(hwnd)})
                if name.value == "Chrome_RenderWidgetHostHWND" and owner(hwnd) == profile["pid"]:
                    renders.append(hwnd)
                return True

            user.EnumChildWindows(host["hwnd"], find_render, 0)
            report["nativeBrowserChildren"] = browser_children
            # Chromium can lazily omit its RenderWidget HWND. The browser
            # client surface is still an actual native input target.
            render = renders[0] if renders else host["hwnd"]
            report["coordinateBasis"] = "render HWND" if renders else "browser client + native content inset"
            # Scroll physically, if needed, rather than mutating the DOM via CDP.
            for _ in range(8):
                value = inspect()
                r, v = value["rect"], value["viewport"]
                if r["y"] >= 0 and r["y"] + r["height"] <= v["h"]:
                    break
                point = Point(100, 150)
                user.ClientToScreen(render, ctypes.byref(point))
                user.SetCursorPos(point.x, point.y)
                actual = Point()
                user.GetCursorPos(ctypes.byref(actual))
                if (actual.x, actual.y) != (point.x, point.y) or owner(user.WindowFromPoint(actual)) != profile["pid"]:
                    raise RuntimeError("Scroll target not owned browser")
                safe_focus(False)
                delta = -360 if r["y"] >= 0 else 360
                send(Input(0, Union(mi=Mouse(0, 0, delta & 0xffffffff, 0x800, 0, 0))))
                time.sleep(.15)
            click_entry()
            if not inspect()["active"] and render == host["hwnd"]:
                # The first root-surface click can bootstrap a lazy render HWND.
                # Resolve its exact content origin before the measured key test.
                renders.clear()
                user.EnumChildWindows(host["hwnd"], find_render, 0)
                if renders:
                    render = renders[0]
                    report["coordinateBasis"] = "lazy render HWND after root bootstrap click"
                    click_entry()
            check("Real mouse click focuses local input", inspect()["active"])
            if not inspect()["active"]:
                raise RuntimeError("Native mouse did not focus the local field; no keyboard input sent")
            if args.ime_only:
                focus = safe_focus().hwndFocus
                thread = user.GetWindowThreadProcessId(focus, None)
                user.PostMessageW(focus, 0x50, 0, 0x08040804)
                for _ in range(20):
                    if int(user.GetKeyboardLayout(thread) or 0) == 0x08040804:
                        break
                    time.sleep(.05)
                check("Owned focused thread uses original Chinese layout", int(user.GetKeyboardLayout(thread) or 0) == 0x08040804)
                for vk in (0x4E, 0x49):
                    press(vk)
                time.sleep(.3)
                composing = event_evidence()
                check("Real native keys start Chinese IME composition", any(e["type"] == "compositionstart" for e in composing))
                press(0x20)
                time.sleep(.3)
                committed = inspect()["value"]
                chinese_count = sum(0x4e00 <= ord(c) <= 0x9fff for c in committed)
                report["imeEvidence"] = {"committedLength": len(committed), "chineseCharacterCount": chinese_count,
                    "compositionStartCount": sum(e["type"] == "compositionstart" for e in event_evidence()),
                    "compositionEndCount": sum(e["type"] == "compositionend" for e in event_evidence())}
                check("Native Space commits actual Chinese text", chinese_count > 0 and any(e["type"] == "compositionend" for e in event_evidence()))
                press(0x1B)
                clear()
                check("Owned IME fixture restored empty", inspect()["value"] == "")
                report["ok"] = all(c["ok"] for c in checks)
                raise StopIteration("IME measurement completed")
            english_layout()
            for vk in (0x41, 0x42, 0x43):
                press(vk)
            typed = inspect()["value"]
            report["typedCharacterCount"] = len(typed)
            check("Real A/B/C keyboard input reaches browser", typed.casefold() == "abc")
            clear()
            check("Real Ctrl+A and Backspace clear nonempty browser input", bool(typed) and inspect()["value"] == "")
            if args.extended:
                time.sleep(2.6)
            paste_before = sum(e["type"] == "paste" for e in event_evidence())
            # This check measures paste itself. The extended polling check
            # intentionally does not re-click after its 2.6-second wait.
            if args.reclick_before_paste:
                click_entry()
            report["reclickedBeforeImmediatePaste"] = args.reclick_before_paste
            if clipboard is not None:
                clipboard, clipboard_sequence = read_clipboard()
                if not clipboard or len(clipboard) > 4096:
                    raise RuntimeError("Existing clipboard no longer supplies bounded nonempty text; paste not verified")
                report["clipboardTextLength"] = len(clipboard)
            else:
                clipboard_sequence = user.GetClipboardSequenceNumber()
            key(0x11, True)
            press(0x56)
            key(0x11, False)
            time.sleep(.3)
            paste_events = sum(e["type"] == "paste" for e in event_evidence()) - paste_before
            unchanged = user.GetClipboardSequenceNumber() == clipboard_sequence
            check("Real Ctrl+V reaches browser paste event with unchanged clipboard", paste_events == 1 and unchanged)
            report["pasteEventEvidence"] = {"count": paste_events, "sequenceUnchanged": unchanged}
            if clipboard is not None:
                check("Real Ctrl+V matches unchanged existing clipboard", paste_matches(inspect()["value"]))
                report["pasteVerification"] = "Exact existing plaintext comparison"
            else:
                report["pasteVerification"] = "Native paste event only; clipboard has no available Unicode text"
            clear()
            check("Local test field restored empty", inspect()["value"] == "")
            report["ok"] = bool(checks) and all(c["ok"] for c in checks)
            keyboard_ok = all(c["ok"] for c in checks[:3])
            if keyboard_ok and args.extended:
                extended()
                report["ok"] = all(c["ok"] for c in checks)
        except StopIteration:
            pass
        except Exception as exc:
            report["ok"] = False
            report["error"] = str(exc)
            fg = user.GetForegroundWindow()
            report["failureFocus"] = {"foregroundHwnd": int(fg or 0), "foregroundPid": owner(fg),
                                      "foregroundRoot": int(user.GetAncestor(fg, 2) or 0),
                                      "expectedApp": int(app), "browserHwnd": host["hwnd"]}
        finally:
            # Never release a key into a different app if focus ownership changed.
            for vk in list(pressed):
                try:
                    key(vk, False)
                except Exception:
                    notes.append("Focus changed; key release was not sent outside the workbench")
            try:
                if initially_empty and inspect()["active"] and inspect()["value"]:
                    clear()
            except Exception:
                notes.append("Test field cleanup could not be verified through native input")
            try:
                report["passiveDomEvents"] = event_evidence()
                evaluate("(()=>{const o=window.__physicalInputObserver;if(o){for(const t of o.types)o.e.removeEventListener(t,o.listener);delete window.__physicalInputObserver;}return true})()")
            except Exception:
                notes.append("Passive event observer cleanup could not be verified")
            for thread, (focus, original) in restored_layouts.items():
                if owner(focus) not in allowed:
                    notes.append("Owned keyboard thread disappeared before layout restoration")
                    continue
                # WPF may use a hidden native focus HWND on the same thread;
                # its root alone need not process a request while unfocused.
                for restore_hwnd in {int(focus), *observed_focus_hwnds.get(thread, set())}:
                    if owner(restore_hwnd) in allowed:
                        user.PostMessageW(restore_hwnd, 0x50, 0, original)
                for _ in range(20):
                    if int(user.GetKeyboardLayout(thread) or 0) == original:
                        break
                    time.sleep(.05)
            # Attached input queues can propagate a later restoration back to
            # earlier threads; verify all final layouts after every request.
            for thread, (_, original) in restored_layouts.items():
                actual = int(user.GetKeyboardLayout(thread) or 0)
                report.setdefault("layoutRestoration", []).append({"thread": thread,
                    "original": original, "final": actual, "restored": actual == original})
                if actual != original:
                    report["ok"] = False
                    notes.append("Original keyboard layout restoration could not be verified")
            user.SetWindowPos(app, ctypes.c_void_p(-1 if original_topmost else -2), 0, 0, 0, 0, 0x13)
            user.SetWindowPlacement(app, ctypes.byref(placement))
            user.SetCursorPos(old_cursor.x, old_cursor.y)
    destination = args.out if args.out.is_absolute() else args.root / args.out
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

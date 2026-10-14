"""Independent, read-only native HWND and local-probe CDP checks.

Run after desktop --smoke. TOKEN or CLOAK_WORKBENCH_TOKEN authenticates the worker.
No launch, close, navigation, or storage writes. --baseline compares a previous
report after root manually relaunches the demo profiles.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import time
import zlib
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener
from websockets.sync.client import connect


class Rect(ctypes.Structure):
    _fields_ = [(n, ctypes.c_long) for n in ("left", "top", "right", "bottom")]


def capture_window(user, hwnd, destination, foreground=False):
    """Capture the owned workbench, using PrintWindow if foreground is blocked."""
    user.GetForegroundWindow.restype = wintypes.HWND
    if foreground:
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.ShowWindow(hwnd, 9)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        current_thread = kernel.GetCurrentThreadId()
        foreground_thread = user.GetWindowThreadProcessId(user.GetForegroundWindow(), None)
        user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        linked = foreground_thread != current_thread and user.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user.SetForegroundWindow(hwnd)
        finally:
            if linked:
                user.AttachThreadInput(current_thread, foreground_thread, False)
        time.sleep(.3)
    foreground_hwnd = user.GetForegroundWindow()
    foreground_is_workbench = foreground_hwnd == hwnd or user.GetAncestor(foreground_hwnd, 2) == hwnd
    rect = Rect()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not 100 <= width <= 8000 or not 100 <= height <= 8000:
        raise RuntimeError("Unexpected workbench bounds")
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HANDLE
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    screen = user.GetDC(None)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
    old = gdi.SelectObject(memory, bitmap)
    try:
        if foreground_is_workbench:
            if not gdi.BitBlt(memory, 0, 0, width, height, screen, rect.left, rect.top, 0x00CC0020):
                raise ctypes.WinError(ctypes.get_last_error())
            capture_method = "foreground workbench bounds"
        else:
            # WM_PRINT targets this HWND only; never read another app's pixels
            # when the desktop foreground cannot be transferred to our app.
            if not user.PrintWindow(hwnd, memory, 2):
                raise RuntimeError("Own-window PrintWindow capture failed; no desktop capture attempted")
            capture_method = "owned HWND PrintWindow"
        gdi.SelectObject(memory, old)
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        info = ctypes.create_string_buffer(header + bytes(4))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0) != height:
            raise ctypes.WinError(ctypes.get_last_error())
        raw = pixels.raw
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            row = raw[y * width * 4:(y + 1) * width * 4]
            rgb = bytearray(width * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
            rows.extend(rgb)

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

        png = bytes([137, 80, 78, 71, 13, 10, 26, 10])
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(png)
        return {"path": str(destination), "width": width, "height": height, "method": capture_method}
    finally:
        gdi.SelectObject(memory, old)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(None, screen)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--foreground", action="store_true", help="Activate only the owned workbench before capture")
    args = parser.parse_args()
    token = os.environ.get("TOKEN") or os.environ.get("CLOAK_WORKBENCH_TOKEN")
    if not token:
        session_file = args.root / ".local" / "session.json"
        if session_file.exists():
            token = json.loads(session_file.read_text(encoding="utf-8-sig")).get("token")
    if not token:
        parser.error("Set TOKEN or CLOAK_WORKBENCH_TOKEN; do not pass the secret on the command line")
    if os.name != "nt":
        parser.error("Native checks require Windows")
    opener = build_opener(ProxyHandler({}))
    request = Request(args.base + "/profiles", headers={"Authorization": "Bearer " + token})
    with opener.open(request, timeout=10) as response:
        state = json.load(response)
    snapshot = json.loads((args.root / ".local" / "ui-state.json").read_text(encoding="utf-8-sig"))
    native = {b["id"]: b for b in snapshot["browsers"]}
    profiles = {p["id"]: p for p in state["profiles"] if p["id"] in ("demo-a", "demo-b")}
    previous = json.loads(args.baseline.read_text(encoding="utf-8-sig")) if args.baseline else None
    checks, observations = [], {}

    def check(name, value):
        checks.append({"check": name, "ok": bool(value)})

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    for name in ("IsWindow", "IsWindowVisible"):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
    user.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]

    def owner(hwnd):
        value = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return value.value

    check("two demo profiles present", set(profiles) == {"demo-a", "demo-b"})
    check("two embedded hosts present", {"demo-a", "demo-b"}.issubset(native))
    for profile_id in ("demo-a", "demo-b"):
        p, b = profiles.get(profile_id), native.get(profile_id)
        if not p or not b:
            continue
        hwnd, parent = b["hwnd"], user.GetParent(b["hwnd"])
        check(profile_id + ": browser alive", user.IsWindow(hwnd))
        check(profile_id + ": HWND owned by launched browser", owner(hwnd) == p["pid"])
        check(profile_id + ": parent matches host snapshot", parent and parent == b["parent"])
        check(profile_id + ": WS_CHILD set", user.GetWindowLongW(hwnd, -16) & 0x40000000)
        check(profile_id + ": parent belongs to desktop, not browser", owner(parent) not in (0, p["pid"]))
        visible = bool(user.IsWindowVisible(hwnd))
        check(profile_id + ": native visibility matches selected tab", visible == (snapshot["selected"] == profile_id))
        browser_rect, host_rect = Rect(), Rect()
        user.GetWindowRect(hwnd, ctypes.byref(browser_rect))
        user.GetClientRect(parent, ctypes.byref(host_rect))
        user.MapWindowPoints(parent, None, ctypes.byref(host_rect), 2)
        browser_bounds = [getattr(browser_rect, f) for f, _ in Rect._fields_]
        host_bounds = [getattr(host_rect, f) for f, _ in Rect._fields_]
        insets = b.get('contentInsets', {})
        inset = lambda key: insets.get(key, insets.get(key.lower(), 0))
        content_bounds = [browser_bounds[0] + inset('Left'), browser_bounds[1] + inset('Top'),
                          browser_bounds[2] - inset('Right'), browser_bounds[3] - inset('Bottom')]
        # Hidden HwndHost arrangement can be deferred after desktop resizing;
        # selecting a host triggers RefreshBounds. Validate the active surface.
        if visible:
            check(profile_id + ": selected web content fills native host", all(abs(a - b) <= 2 for a, b in zip(content_bounds, host_bounds)))
        with opener.open(f"http://127.0.0.1:{p['cdpPort']}/json/list", timeout=5) as response:
            pages = json.load(response)
        page = next((x for x in pages if x.get("type") == "page" and urlsplit(x.get("url", "")).hostname == "127.0.0.1"
                     and urlsplit(x["url"]).path == "/probe"), None)
        check(profile_id + ": local-only probe target", page is not None)
        if page is None:
            continue
        value = None
        with connect(page["webSocketDebuggerUrl"], open_timeout=5, close_timeout=1, proxy=None) as ws:
            for attempt in range(20):
                ws.send(json.dumps({"id": attempt + 1, "method": "Runtime.evaluate", "params": {
                    "expression": "window.probeResult || null", "returnByValue": True}}))
                while True:
                    result = json.loads(ws.recv(timeout=5))
                    if result.get("id") == attempt + 1:
                        value = result.get("result", {}).get("result", {}).get("value")
                        break
                if value:
                    break
                time.sleep(.1)
        check(profile_id + ": probe loaded", isinstance(value, dict))
        if not value:
            continue
        check(profile_id + ": locale applied", value["language"] == p["locale"])
        check(profile_id + ": timezone applied", value["timezone"] == p["timezone"])
        check(profile_id + ": canvas fingerprint collected", len(value.get("canvasSHA256", "")) == 64)
        check(profile_id + ": persistent marker collected", bool(value.get("persistentMarker")))
        observations[profile_id] = {"environment": value, "nativeBounds": browser_bounds, "contentBounds": content_bounds,
                                    "contentInsets": insets, "hostBounds": host_bounds, "visible": visible,
                                    "hiddenSizeDeferred": not visible and browser_bounds != host_bounds}
        if previous and profile_id in previous.get("observations", {}):
            old = previous["observations"][profile_id]["environment"]
            for field in ("persistentMarker", "canvasSHA256", "webgl", "hardwareConcurrency", "deviceMemory", "language", "timezone"):
                check(profile_id + ": relaunch stable " + field, old.get(field) == value.get(field))
    if len(observations) == 2:
        a, b = (observations[k]["environment"] for k in ("demo-a", "demo-b"))
        check("localStorage markers isolated", a["persistentMarker"] != b["persistentMarker"])
        check("different seeds produce different canvas", a["canvasSHA256"] != b["canvasSHA256"])
    report = {"ok": all(c["ok"] for c in checks), "checks": checks, "observations": observations,
              "notVerified": ["native keyboard and IME", "upload/download dialogs", "video/fullscreen", "other DPI scales and monitor moves"]}
    if args.capture:
        current = native.get(snapshot["selected"])
        if not current:
            raise RuntimeError("Selected demo host not available")
        app_hwnd = user.GetAncestor(user.GetParent(current["hwnd"]), 2)
        report["capture"] = capture_window(user, app_hwnd, args.capture, args.foreground)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Isolated actual CloakBrowser popup and file I/O regression.

Creates only a temporary BrowserWorker/profile and local HTTP fixture. CDP drives
file selection/download/popup; this is not proof of native file-dialog or IME UI.
It opens an actual temporary browser window; coordinate with other physical tests.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
from server import BrowserWorker
from websockets.sync.client import connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    checks = []

    def check(name, ok):
        checks.append({"check": name, "ok": bool(ok)})
        if not ok:
            raise AssertionError(name)

    body = b'''<!doctype html><meta charset="utf-8"><title>Owned experience fixture</title>
<input id="upload" type="file"><a id="download" href="/download" download="owned-file.txt">Download</a>
<script>document.querySelector('#upload').onchange=async e=>window.uploadProof=await e.target.files[0].text();</script>'''
    payload = b"Owned temporary file I/O proof\n"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            data = payload if self.path == "/download" else body
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/download" else "text/html")
            if self.path == "/download":
                self.send_header("Content-Disposition", 'attachment; filename="owned-file.txt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    binary = Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe"
    report = {"ok": False, "checks": checks, "scope": "temporary own browser only",
              "notVerified": ["Native upload/save-dialog keyboard and focus", "IME candidate UI", "Other monitor DPI"]}
    try:
        with tempfile.TemporaryDirectory(prefix="cloak-experience-") as directory:
            worker = BrowserWorker(Path(directory), binary)
            try:
                worker.create({"id": "owned-experience", "name": "Temporary experience verification",
                               "startUrl": f"http://127.0.0.1:{server.server_port}/", "seed": 67543})
                state = worker.launch("owned-experience")
                rt = worker.runtime["owned-experience"]

                def evaluate(expression, **extra):
                    return worker.command(rt, "Runtime.evaluate", {
                        "expression": expression, "returnByValue": True, **extra})["result"].get("value")

                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not evaluate("!!document.querySelector('#upload')"):
                    time.sleep(.1)
                check("Local fixture loaded in owned browser", evaluate("!!document.querySelector('#upload')"))
                upload = Path(directory) / "upload.txt"
                upload.write_bytes(payload)
                # Frontend node IDs belong to a CDP session. Keep one connection
                # throughout DOM discovery and file selection.
                with connect(worker.target(rt)["webSocketDebuggerUrl"], open_timeout=5, close_timeout=1, proxy=None) as ws:
                    sequence = 0

                    def dom(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        ws.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                        while True:
                            result = json.loads(ws.recv(timeout=5))
                            if result.get("id") == sequence:
                                if "error" in result:
                                    raise RuntimeError(result["error"])
                                return result["result"]

                    document = dom("DOM.getDocument")
                    node = dom("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "#upload"})
                    dom("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(upload)]})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and evaluate("window.uploadProof") is None:
                    time.sleep(.1)
                check("Uploaded file bytes reach page", evaluate("window.uploadProof") == payload.decode())
                downloaded = Path(worker.downloads("owned-experience")["directory"]) / "owned-file.txt"
                evaluate("document.querySelector('#download').click()", userGesture=True)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and (not downloaded.exists() or downloaded.read_bytes() != payload):
                    time.sleep(.1)
                report["downloadObservation"] = worker.downloads("owned-experience")["files"]
                check("Downloaded file stays in own profile folder", downloaded.exists() and downloaded.read_bytes() == payload)
                check("Download inventory reports completed file", any(
                    item["name"] == "owned-file.txt" and item["complete"]
                    for item in worker.downloads("owned-experience")["files"]))

                before = {item["targetId"] for item in worker.command(rt, "Target.getTargets", browser=True)["targetInfos"]}
                evaluate("window.open('/popup','owned-popup','width=500,height=400') !== null", userGesture=True)
                deadline = time.monotonic() + 5
                popups = []
                while time.monotonic() < deadline:
                    popups = [item for item in worker.command(rt, "Target.getTargets", browser=True)["targetInfos"]
                              if item["targetId"] not in before and item.get("type") == "page"
                              and item.get("url") == f"http://127.0.0.1:{server.server_port}/popup"]
                    if popups:
                        break
                    time.sleep(.1)
                check("Owned window.open produces separate local popup target", len(popups) == 1)

                user = ctypes.WinDLL("user32", use_last_error=True)
                user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                user.GetAncestor.restype = wintypes.HWND
                user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                user.IsWindowVisible.argtypes = [wintypes.HWND]
                callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                windows = []

                @callback_type
                def observe(hwnd, _):
                    pid = wintypes.DWORD()
                    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    name = ctypes.create_unicode_buffer(256)
                    user.GetClassNameW(hwnd, name, len(name))
                    if pid.value == state["pid"] and name.value == "Chrome_WidgetWin_1" and user.IsWindowVisible(hwnd):
                        windows.append(hwnd)
                    return True

                user.EnumWindows(observe, 0)
                check("Popup remains a separate visible native browser window", len(windows) >= 2)
                worker.command(rt, "Target.closeTarget", {"targetId": popups[0]["targetId"]}, browser=True)
                check("Popup close leaves main browser running", worker.state("owned-experience")["state"] == "running")
                report["nativeWindowCount"] = len(windows)
                report["ok"] = True
            finally:
                worker.shutdown()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

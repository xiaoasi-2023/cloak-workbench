"""Real Windows browser startup/close visibility with isolated profile data."""
import ctypes
from ctypes import wintypes
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
from server import ApiError, BrowserWorker, make_handler


def main():
    output = ROOT / ".local/browser-lifecycle"
    output.mkdir(parents=True, exist_ok=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    monitors = []
    real_popen = subprocess.Popen
    binary = Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe"

    def launch_observed(argv, *args, **kwargs):
        process = real_popen(argv, *args, **kwargs)
        if str(argv[0]) != str(binary):
            return process
        startup = kwargs.get("startupinfo")
        report = {"pid": process.pid, "hiddenStartup": bool(startup and startup.dwFlags & subprocess.STARTF_USESHOWWINDOW and startup.wShowWindow == 0), "hiddenWindowsObserved": 0, "externalVisibleSamples": 0}

        def observe():
            root_window = None
            @callback_type
            def visit(hwnd, _):
                nonlocal root_window
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != process.pid:
                    return True
                name = ctypes.create_unicode_buffer(128)
                user.GetClassNameW(hwnd, name, len(name))
                if name.value == "Chrome_WidgetWin_1":
                    root_window = hwnd
                    if user.IsWindowVisible(hwnd) and not user.GetWindowLongW(hwnd, -16) & 0x40000000:
                        report["externalVisibleSamples"] += 1
                    else:
                        report["hiddenWindowsObserved"] += 1
                return True
            # EnumWindows visits top-level windows only. Once embedded, this
            # root HWND disappears from the enumeration. End before the test's
            # intentional external-window action by observing its child state.
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline and process.poll() is None:
                user.EnumWindows(visit, 0)
                if root_window and user.GetWindowLongW(root_window, -16) & 0x40000000:
                    report["attached"] = True
                    break
                time.sleep(.001)
        thread = threading.Thread(target=observe, daemon=True)
        monitors.append((thread, report))
        thread.start()
        return process

    with tempfile.TemporaryDirectory(prefix="cloak-lifecycle-") as directory:
        worker = BrowserWorker(Path(directory) / "data", binary)
        token = "isolated-lifecycle-test-token"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, token))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        worker.create({"id": "ui-browser", "name": "Isolated lifecycle fixture", "startUrl": f"http://127.0.0.1:{server.server_port}/probe"})
        real_close = worker.close
        reject_once = True

        def close_with_failure(profile_id):
            nonlocal reject_once
            if reject_once:
                reject_once = False
                raise ApiError(503, "Synthetic close failure; browser is still running")
            return real_close(profile_id)

        try:
            with patch("server.subprocess.Popen", side_effect=launch_observed), patch.object(worker, "close", side_effect=close_with_failure):
                result = subprocess.run([str(ROOT / ".tools/dotnet/dotnet.exe"), str(ROOT / ".local/proxy-ui-build/ProxyUiHarness.dll"),
                    f"http://127.0.0.1:{server.server_port}/", str(output), "--lifecycle"], cwd=ROOT,
                    env=dict(os.environ, CLOAK_WORKBENCH_ROOT=directory, CLOAK_WORKBENCH_TOKEN=token), timeout=80)
            for monitor, _ in monitors:
                monitor.join(timeout=5)
            reports = [report for _, report in monitors]
            (output / "startup-visibility.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
            if result.returncode or len(reports) != 4 or not all(r["hiddenStartup"] and r["hiddenWindowsObserved"] and r.get("attached") and not r["externalVisibleSamples"] for r in reports):
                raise RuntimeError("Lifecycle regression failed; see .local/browser-lifecycle evidence")
            print("PASS: 4 real hidden startups, 3 closes, failed-close recovery, explicit detach/reattach, duplicate close and app shutdown; zero external flash samples")
        finally:
            worker.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    main()

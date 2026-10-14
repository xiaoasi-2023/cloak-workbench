"""Local authenticated browser worker. Never modifies another application's profiles."""
from __future__ import annotations

import argparse
import atexit
import ctypes
from ctypes import wintypes
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import uuid

from websockets.sync.client import connect
from websockets.protocol import State
from proxy_support import ProxyError, ProxyRelay, parse_proxy, test_proxy
from fingerprint import validate_fingerprint, launch_args, capabilities, inspect_environment, require_supported
from proxy_catalog import ProxyCatalog


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def validate_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ApiError(400, "URL must be a valid HTTP(S) URL or about:blank")
    if value == "about:blank":
        return value
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ApiError(400, "Invalid URL port")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ApiError(400, "Only HTTP(S) URLs without embedded credentials are supported")
    return value


def validate_proxy(value: object) -> str:
    try:
        return parse_proxy({"proxy": value}).address
    except ProxyError as exc:
        raise ApiError(400, str(exc)) from None


def validate_profile(payload: dict) -> dict:
    proxy_id = payload.get("proxyId", "")
    if not isinstance(proxy_id, str) or proxy_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", proxy_id):
        raise ApiError(400, "Invalid proxy id")
    try:
        proxy = parse_proxy({} if proxy_id else payload)
        fingerprint = validate_fingerprint(payload)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    profile_id = payload.get("id") or uuid.uuid4().hex[:12]
    if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
        raise ApiError(400, "Profile id must use letters, digits, underscore or hyphen")
    name = payload.get("name", "New profile")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ApiError(400, "Name must contain 1 to 120 characters")
    locale = payload.get("locale", "zh-CN")
    if not isinstance(locale, str) or not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", locale):
        raise ApiError(400, "Invalid locale")
    timezone = payload.get("timezone", "Asia/Shanghai")
    if not isinstance(timezone, str) or not re.fullmatch(r"[A-Za-z0-9_+/-]{1,80}", timezone):
        raise ApiError(400, "Invalid timezone")
    # Windows Python often lacks tzdata. The installed runtime provides this dependency.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ApiError(400, "Unknown IANA timezone (install tzdata if the system has no timezone database)")
    seed = payload.get("seed", secrets.randbelow(90000) + 10000)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 1 <= seed <= 2147483647:
        raise ApiError(400, "Seed must be an integer from 1 to 2147483647")
    width, height = payload.get("screenWidth", 1280), payload.get("screenHeight", 900)
    if any(isinstance(n, bool) or not isinstance(n, int) for n in (width, height)) or not 640 <= width <= 7680 or not 480 <= height <= 4320:
        raise ApiError(400, "Initial window dimensions are outside supported bounds")
    return {"id": profile_id, "name": name.strip(), "proxyId": proxy_id, "proxy": proxy.address,
            "proxyUsername": proxy.username, "proxyPassword": proxy.password,
            "locale": locale, "timezone": timezone, "seed": seed,
            "startUrl": validate_url(payload.get("startUrl", "https://www.dola.com/")),
            "screenWidth": width, "screenHeight": height, **fingerprint}


class BrowserWorker:
    def __init__(self, data_dir: Path, binary: Path):
        self.data_dir, self.binary = data_dir.resolve(), binary.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.data_dir / "profiles.json"
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}))
        self.runtime: dict[str, dict] = {}
        self.profiles: dict[str, dict] = {}
        self.catalog = ProxyCatalog(self, ApiError)
        if self.profile_file.exists():
            stored = json.loads(self.profile_file.read_text(encoding="utf-8"))
            for payload in stored:
                profile = validate_profile(payload)
                if profile["id"] in self.profiles:
                    raise ValueError("Duplicate stored profile id")
                self.profiles[profile["id"]] = profile
            for profile in self.profiles.values():
                if profile.get("proxyId"):
                    self.catalog.entry(profile["proxyId"])

    def save(self):
        temp = self.profile_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(list(self.profiles.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.profile_file)

    def create(self, payload):
        with self.lock:
            if payload.get("id") is not None and not isinstance(payload["id"], str):
                raise ApiError(400, "Invalid profile id")
            existing = self.profiles.get(payload.get("id"))
            merged = dict(existing or {}, **payload)
            if payload.get("keepProxyPassword") is True and existing:
                merged["proxyPassword"] = existing.get("proxyPassword", "")
            profile = validate_profile(merged)
            if profile["proxyId"]:
                self.catalog.entry(profile["proxyId"])
            if existing and self.state(profile["id"])["state"] in ("running", "starting"):
                if profile != existing:
                    raise ApiError(409, "Close the profile before editing its environment")
                return self.state(profile["id"])
            self.profiles[profile["id"]] = profile
            try:
                self.save()
            except Exception:
                if existing:
                    self.profiles[profile["id"]] = existing
                else:
                    self.profiles.pop(profile["id"])
                raise
            return self.state(profile["id"])

    def profile(self, profile_id):
        if profile_id not in self.profiles:
            raise ApiError(404, "Unknown profile")
        return self.profiles[profile_id]

    def is_running(self, profile_id):
        runtime = self.runtime.get(profile_id)
        return bool(runtime and runtime["process"].poll() is None)

    def effective_proxy(self, profile):
        proxy_id = profile.get("proxyId", "")
        return dict(self.catalog.entry(proxy_id)) if proxy_id else dict(profile)

    def cdp_json(self, port, path):
        with self.opener.open(f"http://127.0.0.1:{port}{path}", timeout=1.5) as response:
            return json.load(response)

    def target(self, runtime):
        pages = [p for p in self.cdp_json(runtime["cdpPort"], "/json/list") if p.get("type") == "page"]
        if not pages:
            raise ApiError(409, "Browser has no page target")
        target_id = runtime.get("targetId")
        page = next((p for p in pages if p["id"] == target_id), pages[0])
        runtime["targetId"] = page["id"]
        return page

    def command(self, runtime, method, params=None, browser=False):
        if runtime["process"].poll() is not None:
            raise ApiError(409, "Browser is closed")
        endpoint = self.cdp_json(runtime["cdpPort"], "/json/version") if browser else self.target(runtime)
        with connect(endpoint["webSocketDebuggerUrl"], open_timeout=3, close_timeout=1, proxy=None) as ws:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = json.loads(ws.recv(timeout=max(.1, deadline-time.monotonic())))
                if result.get("id") == 1:
                    if "error" in result:
                        raise ApiError(502, result["error"].get("message", "Browser command failed"))
                    return result.get("result", {})
        raise ApiError(504, "Browser command timed out")

    def state(self, profile_id):
        profile = self.profile(profile_id)
        rt = self.runtime.get(profile_id)
        running = bool(rt and rt["process"].poll() is None)
        public = {key: value for key, value in profile.items() if key != "proxyPassword"}
        effective = self.effective_proxy(profile)
        public.update(proxy=effective.get("proxy", ""), proxyUsername=effective.get("proxyUsername", ""),
                      proxyPasswordSet=bool(effective.get("proxyPassword")),
                      proxyName=effective.get("name", "") if profile.get("proxyId") else "",
                      upstreamProxy=effective.get("upstreamProxy", ""),
                      upstreamProxyUsername=effective.get("upstreamProxyUsername", ""),
                      upstreamProxyPasswordSet=bool(effective.get("upstreamProxyPassword")),
                      proxyMode="chained" if effective.get("upstreamProxy") else "proxy" if effective.get("proxy") else "direct")
        result = dict(public, state="running" if running else "closed", pid=rt["process"].pid if running else None,
                      windowHandle=find_window(rt["process"].pid) if running else 0,
                      cdpPort=rt["cdpPort"] if running else None, url=profile["startUrl"], title=profile["name"])
        if running:
            try:
                page = self.target(rt)
                result.update(url=page.get("url", ""), title=page.get("title", ""))
            except Exception:
                result["state"] = "starting"
        elif rt:
            result["exitCode"] = rt["process"].returncode
            self.release(rt)
            self.runtime.pop(profile_id, None)
        return result

    def launch(self, profile_id):
        with self.lock:
            profile = self.profile(profile_id)
            current = self.state(profile_id)
            if current["state"] in ("running", "starting"):
                return current
            if not self.binary.is_file():
                raise ApiError(503, "CloakBrowser executable not found; set CLOAK_WORKBENCH_BROWSER")
            try:
                require_supported(profile, self.binary)
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
            profile_dir = self.data_dir / "profiles" / profile_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            lease = (profile_dir / ".workbench.lock").open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    lease.seek(0)
                    if lease.read(1) == b"":
                        lease.write(b"0")
                        lease.flush()
                    lease.seek(0)
                    msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lease.close()
                raise ApiError(409, "Profile is locked by another worker")
            args = [str(self.binary), f"--user-data-dir={profile_dir}", "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
                    f"--fingerprint={profile['seed']}",
                    f"--lang={profile['locale']}", f"--fingerprint-locale={profile['locale']}",
                    f"--fingerprint-timezone={profile['timezone']}", "--ignore-gpu-blocklist",
                    f"--window-size={profile['screenWidth']},{profile['screenHeight']}",
                    f"--app={profile['startUrl']}"]
            args.extend(launch_args(profile))
            relay = None
            try:
                proxy = self.effective_proxy(profile)
                if proxy["proxy"]:
                    relay = ProxyRelay(parse_proxy(proxy))
                    args.append(f"--proxy-server={relay.address}")
                    # Chromium must not let WebRTC create a direct UDP route around
                    # this environment's HTTP/SOCKS proxy. Loopback UI remains local.
                    args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
            except Exception:
                lease.close()
                raise ApiError(502, "Unable to initialize the profile proxy relay") from None
            active_file = profile_dir / "DevToolsActivePort"
            # This file is a transient endpoint record, never Cookie/Profile data.
            log = None
            try:
                active_file.unlink(missing_ok=True)
                log = (profile_dir / "browser.log").open("ab")
                process = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
            except Exception:
                lease.close()
                if log:
                    log.close()
                if relay:
                    relay.close()
                raise
            rt = {"process": process, "lease": lease, "log": log, "cdpPort": 0, "job": create_job(process), "relay": relay}
            self.runtime[profile_id] = rt
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.release(rt)
                    self.runtime.pop(profile_id, None)
                    raise ApiError(502, f"Browser exited during launch (code {process.returncode}); see its browser.log")
                try:
                    port = int(active_file.read_text(encoding="utf-8").splitlines()[0])
                    if not 1 <= port <= 65535:
                        raise ValueError("Invalid DevTools port")
                    self.cdp_json(port, "/json/version")
                    rt["cdpPort"] = port
                    self.target(rt)
                    self.configure_downloads(profile_id, rt)
                    return self.state(profile_id)
                except (OSError, ValueError, IndexError, ApiError):
                    time.sleep(.15)
            self.close(profile_id)
            raise ApiError(504, "Browser did not become ready within 18 seconds")

    def release(self, rt):
        download_socket = rt.pop("downloadSocket", None)
        if download_socket:
            try:
                download_socket.close()
            except Exception:
                pass
        relay = rt.pop("relay", None)
        if relay:
            relay.close()
        job = rt.pop("job", None)
        if job:
            ctypes.windll.kernel32.CloseHandle(job)
        rt["lease"].close()
        rt["log"].close()

    def proxy_test(self, payload=None, profile_id=None):
        with self.lock:
            if profile_id is not None:
                payload = self.effective_proxy(self.profile(profile_id))
            else:
                payload = dict(payload or {})
                # An editor can test a saved secret without ever reading it.
                if payload.get("id") is not None and not isinstance(payload["id"], str):
                    raise ApiError(400, "Invalid profile id")
                existing = self.profiles.get(payload.get("id"))
                if payload.get("keepProxyPassword") is True and existing:
                    payload["proxyPassword"] = existing.get("proxyPassword", "")
                if payload.get("proxyId"):
                    if not isinstance(payload["proxyId"], str):
                        raise ApiError(400, "Invalid proxy id")
                    payload = dict(self.catalog.entry(payload["proxyId"]))
        try:
            config = parse_proxy(payload)
        except ProxyError as exc:
            raise ApiError(400, str(exc)) from None
        return test_proxy(config)

    def download_directory(self, profile_id):
        directory = self.data_dir / "downloads" / profile_id
        if directory.resolve() != directory.absolute():
            raise ApiError(409, "Profile download folder must remain inside its owned data directory")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def configure_downloads(self, profile_id, runtime):
        """Chromium resets this setting when its owning CDP session disconnects."""
        retained = runtime.get("downloadSocket")
        if retained is not None and retained.state == State.OPEN:
            return
        if retained is not None:
            retained.close()
            runtime.pop("downloadSocket", None)
        directory = self.download_directory(profile_id)
        endpoint = self.cdp_json(runtime["cdpPort"], "/json/version")
        socket = connect(endpoint["webSocketDebuggerUrl"], open_timeout=3, close_timeout=1, proxy=None)
        try:
            socket.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior", "params": {
                "behavior": "allow", "downloadPath": str(directory.resolve()), "eventsEnabled": False}}))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                reply = json.loads(socket.recv(timeout=max(.1, deadline - time.monotonic())))
                if reply.get("id") == 1:
                    if "error" in reply:
                        raise ApiError(502, "Browser rejected the per-profile download directory")
                    runtime["downloadSocket"] = socket
                    return
            raise ApiError(504, "Browser download configuration timed out")
        except Exception:
            socket.close()
            raise

    def downloads(self, profile_id):
        with self.lock:
            self.profile(profile_id)
            runtime = self.runtime.get(profile_id)
            if runtime and runtime["process"].poll() is None:
                self.configure_downloads(profile_id, runtime)
        directory = self.download_directory(profile_id)
        files = []
        for path in directory.iterdir():
            # Do not resolve links into another profile or outside the owned folder.
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                continue
            try:
                stat = path.stat()
                files.append({"name": path.name, "size": stat.st_size, "modifiedAt": stat.st_mtime,
                              "complete": not path.name.endswith(".crdownload")})
            except OSError:
                continue
        return {"directory": str(directory.resolve()), "files": sorted(files, key=lambda item: item["modifiedAt"], reverse=True)}

    def environment(self, profile_id):
        with self.lock:
            if self.state(profile_id)["state"] != "running":
                raise ApiError(409, "Open the profile before inspecting its environment")
            try:
                return inspect_environment(self, profile_id)
            except ValueError as exc:
                raise ApiError(502, str(exc)) from None

    def close(self, profile_id):
        with self.lock:
            self.profile(profile_id)
            rt = self.runtime.get(profile_id)
            if rt:
                process = rt["process"]
                if process.poll() is None:
                    try:
                        self.command(rt, "Browser.close", browser=True)
                    except Exception:
                        pass
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        if rt.get("job"):
                            ctypes.windll.kernel32.TerminateJobObject(rt["job"], 1)
                        else:
                            # Popen's retained process handle avoids PID reuse races.
                            process.terminate()
                        process.wait(timeout=5)
                self.release(rt)
                self.runtime.pop(profile_id, None)
            return self.state(profile_id)

    def navigate(self, profile_id, payload):
        url = validate_url(payload.get("url"))
        with self.lock:
            self.profile(profile_id)
            rt = self.runtime.get(profile_id)
            if not rt:
                raise ApiError(409, "Open the profile first")
            result = self.command(rt, "Page.navigate", {"url": url})
            if result.get("errorText"):
                raise ApiError(502, result["errorText"])
            state = self.state(profile_id)
            state["url"] = url
            return state

    def action(self, profile_id, payload):
        action = payload.get("action")
        if action not in ("back", "forward", "reload"):
            raise ApiError(400, "Action must be back, forward or reload")
        with self.lock:
            self.profile(profile_id)
            rt = self.runtime.get(profile_id)
            if not rt:
                raise ApiError(409, "Open the profile first")
            if action == "reload":
                self.command(rt, "Page.reload")
            else:
                history = self.command(rt, "Page.getNavigationHistory")
                index = history["currentIndex"] + (-1 if action == "back" else 1)
                if 0 <= index < len(history["entries"]):
                    self.command(rt, "Page.navigateToHistoryEntry", {"entryId": history["entries"][index]["id"]})
            return self.state(profile_id)

    def shutdown(self):
        self.catalog.close()
        for profile_id in list(self.runtime):
            try:
                self.close(profile_id)
            except Exception:
                rt = self.runtime.pop(profile_id, None)
                if rt:
                    if rt["process"].poll() is None:
                        rt["process"].terminate()
                    self.release(rt)


def find_window(pid):
    if os.name != "nt":
        return 0
    user = ctypes.windll.user32
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            # Chromium's primary UI is Chrome_WidgetWin_1; skip tool windows.
            class_name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, class_name, len(class_name))
            if class_name.value == "Chrome_WidgetWin_1" and not user.GetWindowLongW(hwnd, -20) & 0x80:
                result.append(int(hwnd))
        return True
    user.EnumWindows(callback, 0)
    return result[0] if result else 0


def create_job(process):
    """Kill only this browser tree if the owning worker crashes or is terminated."""
    if os.name != "nt":
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        if job:
            kernel.CloseHandle(job)
        return None
    return job


def make_handler(worker, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CloakWorkbench/0.1"

        def log_message(self, *_):
            pass

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                # A browser can cancel favicon/API requests during close.
                pass

        def route(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                raise ApiError(401, "Unauthorized")
            if self.headers.get("Origin"):
                raise ApiError(403, "Browser-origin requests are not accepted by this native-only API")
            parts = [p for p in urlsplit(self.path).path.split("/") if p]
            if self.command == "GET":
                if parts == ["health"]:
                    support = capabilities(worker.binary)["supportedFields"]
                    return {"status": "ok", "engineAvailable": worker.binary.is_file(), "enginePath": str(worker.binary),
                            "version": "0.1.0", "capabilities": {"nativeAppWindow": True, "persistentProfiles": True,
                            "stableFingerprintSeed": True, "screenFingerprintOverride": bool(support.get("fingerprintScreenWidth") and support.get("fingerprintScreenHeight")), "authenticatedProxy": True,
                            "proxyProtocols": ["http", "https", "socks5"], "proxyTest": True,
                            "proxyCatalog": True, "chainedProxy": True, "batchProxyDiagnostics": True, "perProfileDownloads": True}}
                with worker.lock:
                    if parts == ["proxies"]:
                        return {"proxies": [worker.catalog.public(p) for p in worker.catalog.entries.values()]}
                    if len(parts) == 2 and parts[0] == "proxy-checks":
                        return worker.catalog.snapshot(parts[1])
                    if parts in (["profiles"], ["state"]):
                        return {"profiles": [worker.state(pid) for pid in worker.profiles]}
                    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "state":
                        return worker.state(parts[1])
                    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "downloads":
                        return worker.downloads(parts[1])
                    if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "environment":
                        return worker.environment(parts[1])
                    if parts == ["capabilities"]:
                        return capabilities(worker.binary)
                raise ApiError(404, "Unknown route")
            if self.command != "POST":
                raise ApiError(405, "Method not allowed")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 65536:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise ApiError(400, "Expected a JSON object smaller than 64 KiB")
            if parts == ["profiles"]:
                return worker.create(payload)
            if parts == ["proxies"]:
                return worker.catalog.upsert(payload)
            if parts == ["proxies", "test"]:
                return worker.catalog.draft_test(payload)
            if parts == ["proxies", "check"]:
                return worker.catalog.start(payload.get("ids"))
            if len(parts) == 3 and parts[0] == "proxies":
                if parts[2] == "check":
                    return worker.catalog.start([parts[1]])
                if parts[2] == "delete":
                    return worker.catalog.delete(parts[1])
            if len(parts) == 3 and parts[0] == "proxy-checks" and parts[2] == "cancel":
                return worker.catalog.cancel(parts[1])
            if parts == ["proxy", "test"]:
                return worker.proxy_test(payload)
            if parts == ["shutdown"]:
                with worker.lock:
                    worker.shutdown()
                    return {"status": "closed", "profiles": [worker.state(pid) for pid in worker.profiles]}
            if len(parts) == 3 and parts[0] == "profiles":
                profile_id, operation = parts[1:]
                if operation == "launch":
                    return worker.launch(profile_id)
                if operation == "close":
                    return worker.close(profile_id)
                if operation == "navigate":
                    return worker.navigate(profile_id, payload)
                if operation == "action":
                    return worker.action(profile_id, payload)
                if operation == "proxy-test":
                    return worker.proxy_test(profile_id=profile_id)
            raise ApiError(404, "Unknown route")

        def handle_request(self):
            try:
                self.connection.settimeout(20)
                if self.command == "GET" and urlsplit(self.path).path == "/probe":
                    body = PROBE_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_json(200, self.route())
            except ApiError as exc:
                self.send_json(exc.status, {"error": str(exc)})
            except Exception as exc:
                # Avoid echoing profile data, URLs with secrets or system internals.
                self.send_json(500, {"error": "Worker operation failed", "type": type(exc).__name__})

        do_GET = handle_request
        do_POST = handle_request
    return Handler


PROBE_HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>环境验证 · Cloak Workbench</title>
<style>
:root{color-scheme:light;--ink:#142c29;--muted:#668079;--line:#dbe6e1;--teal:#087d6b;--wash:#f3f7f5}
*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font:14px "Microsoft YaHei UI","Microsoft YaHei",sans-serif}
header{height:44px;background:#132b28;color:#abc4bb;display:flex;align-items:center;justify-content:space-between;padding:0 30px;font-size:11px;letter-spacing:1.6px}
header span{color:#7bdbc0;letter-spacing:0}header span:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:#65d9b5;margin-right:7px}
main{max-width:1020px;margin:0 auto;padding:28px 32px 38px}.intro{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:24px}.eyebrow{font:11px Consolas,monospace;letter-spacing:2px;color:var(--teal)}h1{font-size:28px;font-weight:600;letter-spacing:-.8px;margin:8px 0 10px}p{margin:0;color:var(--muted);line-height:1.7}.badge{padding:8px 12px;border:1px solid #c4dfd2;border-radius:5px;white-space:nowrap;background:#eaf5ee;color:#39705b;font-size:12px;margin-top:20px}
.section-head{display:flex;justify-content:space-between;align-items:center;margin:20px 0 10px}h2{font-size:14px;font-weight:600;margin:0}.section-head small{font-size:11px;color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.card{background:#fff;border:1px solid var(--line);border-radius:7px;padding:15px 16px;min-height:90px}.card small{display:block;color:var(--muted);font-size:11px;margin-bottom:10px}.card strong{font:600 16px Consolas,"Microsoft YaHei UI",sans-serif;overflow-wrap:anywhere}.card.gpu{grid-column:span 2;min-height:88px}.card.gpu strong{font-size:12px;line-height:1.65}.card.hash strong{font-size:13px}.card.hash span{display:block;color:var(--muted);font-size:10px;margin-top:6px}
.persistence{display:flex;gap:18px;align-items:flex-start;background:#e9f2ed;border-left:3px solid #5caa8e;padding:13px 16px;margin-top:12px;border-radius:0 6px 6px 0}.persistence .icon{font-size:18px;color:var(--teal)}.persistence strong{font-size:12px}.persistence p{font-size:11px;margin-top:4px;overflow-wrap:anywhere}.persistence code{font-family:Consolas,monospace;font-size:11px}.note{font-size:12px;color:var(--ink);margin-top:7px}
.tools{background:#fff;border:1px solid var(--line);border-radius:7px;padding:16px}.input-row{display:flex;gap:8px}input[type=text]{min-width:0;flex:1;padding:11px 12px;border:1px solid #ccdad3;border-radius:5px;color:var(--ink);font:13px "Microsoft YaHei UI",sans-serif;background:#fafcfb}button,.download{display:inline-flex;align-items:center;justify-content:center;padding:10px 13px;border:1px solid #d1dfd7;border-radius:5px;background:white;color:#3b5a50;font:12px "Microsoft YaHei UI",sans-serif;text-decoration:none;cursor:pointer}button:hover,.download:hover{border-color:#56ad91;background:#f0f8f4}.primary{background:var(--teal);border-color:var(--teal);color:white}.primary:hover{background:#076b5c;color:white}.input-row input:focus{outline:2px solid #79bfa5;outline-offset:1px}.compat{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;align-items:center}.upload-label{font-size:12px;color:#3b5a50;display:flex;gap:9px;align-items:center}.upload-label input{font-size:11px;width:210px}.status{min-height:17px;font-size:11px;color:var(--teal);margin-top:10px}.help{font-size:11px;margin-top:3px;color:var(--muted)}details{margin-top:18px;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;font-size:12px;color:var(--muted)}pre{background:white;border:1px solid var(--line);border-radius:7px;padding:18px;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.7 Consolas,monospace}canvas{display:none}footer{font-size:10px;color:#7f938a;margin-top:18px;line-height:1.7}
@media(max-width:760px){main{padding:20px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}header{padding:0 20px}.intro{gap:8px}.badge{display:none}h1{font-size:24px}.input-row{flex-wrap:wrap}.input-row input{flex-basis:100%}}
</style>
<header><b>CLOAK WORKBENCH</b><span>本地离线验证</span></header>
<main>
<div class="intro"><div><div class="eyebrow">ENVIRONMENT / <span id="profile-name">LOCAL</span></div><h1>每个账号，都有自己的环境。</h1><p>以下信息由当前浏览器实时读取。关闭并重新打开后，可对照环境与存储是否保持一致。</p></div><div class="badge">真实浏览器 · 非模拟画面</div></div>
<div class="section-head"><h2>当前环境</h2><small>页面读取值 / 未注入覆盖脚本</small></div>
<div class="cards">
<div class="card"><small>语言 / LANGUAGE</small><strong id="language-value">—</strong></div>
<div class="card"><small>时区 / TIMEZONE</small><strong id="timezone-value">—</strong></div>
<div class="card"><small>CPU / 逻辑处理器</small><strong id="cpu-value">—</strong></div>
<div class="card"><small>内存 / 浏览器暴露值</small><strong id="ram-value">—</strong></div>
<div class="card gpu"><small>WebGL / 渲染器</small><strong id="gpu-value">—</strong></div>
<div class="card hash"><small>Canvas / 内容摘要</small><strong id="hash-value">—</strong><span>固定种子重开后应保持稳定</span></div>
<div class="card"><small>实际屏幕 / SCREEN</small><strong id="screen-value">—</strong></div>
</div>
<div class="persistence"><div class="icon">↳</div><div><strong>独立会话存储</strong><p>本环境标记：<code id="marker-value">—</code></p><div class="note">已保存备注：<span id="note-value">尚未保存</span></div></div></div>
<div class="section-head"><h2>交互兼容性检查</h2><small>中文输入 / 剪贴板 / 文件 / 弹窗</small></div>
<div class="tools"><div class="input-row"><input type="text" id="entry" placeholder="在这里输入中文，测试输入法与持久化备注" aria-label="输入法和备注测试"><button onclick="copyText()">复制内容</button><button class="primary" onclick="saveNote()">保存环境备注</button></div>
<div class="compat"><button onclick="openPopup()">打开测试弹窗 ↗</button><a class="download" href="data:text/plain;charset=utf-8,Cloak%20Workbench%20local%20download%20test%0A" download="cloak-local-download.txt">下载本地测试文件 ↓</a><label class="upload-label">上传文件<input type="file" id="upload"></label></div>
<div class="status" id="status" aria-live="polite"></div><p class="help">保存的备注仅写入当前环境的 localStorage。上传测试只显示文件名称，不读取或发送文件内容。</p></div>
<details><summary>展开浏览器原始信息 JSON</summary><pre id="result">正在读取环境…</pre></details><canvas id="canvas" width="320" height="80"></canvas>
<footer>此页运行于 127.0.0.1，无外部请求。显示的是浏览器对页面暴露的值，并非电脑真实硬件清单。Canvas、WebGL 等环境特征由当前内核与种子生成。</footer>
<script>
async function inspect(){
 const c=document.querySelector('#canvas'),ctx=c.getContext('2d');ctx.fillStyle='#197b72';ctx.fillRect(0,0,320,80);ctx.font='20px Arial';ctx.fillStyle='#fff';ctx.fillText('Cloak Workbench 2026',12,44);
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(c.toDataURL()));
 const hash=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
 const gl=document.createElement('canvas').getContext('webgl');const ext=gl&&gl.getExtension('WEBGL_debug_renderer_info');
 let marker=localStorage.getItem('workbench-marker');if(!marker){marker=crypto.randomUUID();localStorage.setItem('workbench-marker',marker)}
 const value={profile:new URL(location.href).searchParams.get('profile'),language:navigator.language,languages:navigator.languages,
 timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,userAgent:navigator.userAgent,platform:navigator.platform,hardwareConcurrency:navigator.hardwareConcurrency,
 deviceMemory:navigator.deviceMemory,screen:{width:screen.width,height:screen.height},window:{innerWidth,innerHeight,outerWidth,outerHeight,devicePixelRatio},
 webgl:ext?{vendor:gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),renderer:gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)}:null,canvasSHA256:hash,
 persistentMarker:marker,note:localStorage.getItem('workbench-note')||''};window.probeResult=value;document.querySelector('#result').textContent=JSON.stringify(value,null,2);
 const text=(id,v)=>document.getElementById(id).textContent=v;
 text('profile-name',value.profile||'LOCAL');text('language-value',value.language);text('timezone-value',value.timezone);
 text('cpu-value',value.hardwareConcurrency+' 核');text('ram-value',value.deviceMemory?value.deviceMemory+' GB':'未暴露');
 text('gpu-value',value.webgl?value.webgl.renderer:'WebGL 不可用');text('hash-value',hash.slice(0,12)+'…'+hash.slice(-6));
 text('screen-value',value.screen.width+' × '+value.screen.height);text('marker-value',marker);text('note-value',value.note||'尚未保存');
}
function saveNote(){localStorage.setItem('workbench-note',document.querySelector('#entry').value);inspect();document.querySelector('#status').textContent='已保存。关闭并重开此环境，检查备注是否保留。'}
async function copyText(){try{await navigator.clipboard.writeText(document.querySelector('#entry').value);document.querySelector('#status').textContent='已复制，可在其他应用中粘贴验证。'}catch(e){document.querySelector('#status').textContent='剪贴板写入失败：'+e.name}}
function openPopup(){const w=window.open('about:blank','workbench-local-popup','width=560,height=340');if(!w){document.querySelector('#status').textContent='弹窗被浏览器拦截，请允许此本地页面打开弹窗。';return}w.document.title='本地弹窗验证';w.document.body.style.cssText='margin:40px;background:#f3f7f5;color:#142c29;font:15px Microsoft YaHei,sans-serif';const h=w.document.createElement('h2');h.textContent='浏览器弹窗已打开';const p=w.document.createElement('p');p.textContent='此窗口由当前浏览器创建，不连接任何外部网站。';const b=w.document.createElement('button');b.textContent='关闭测试弹窗';b.onclick=()=>w.close();w.document.body.append(h,p,b);document.querySelector('#status').textContent='弹窗已创建。请检查焦点、位置与关闭操作。'}
document.querySelector('#upload').addEventListener('change',e=>{document.querySelector('#status').textContent=e.target.files.length?'已选择：'+e.target.files[0].name+'（未读取或上传）':'未选择文件'});
inspect();
</script></main></html>'''


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--browser", type=Path, default=Path(os.environ.get("CLOAK_WORKBENCH_BROWSER", str(Path.home() / ".cloakbrowser" / "chromium-146.0.7680.177.5" / "chrome.exe"))))
    args = parser.parse_args()
    token = os.environ.get("CLOAK_WORKBENCH_TOKEN", "")
    if len(token) < 24:
        parser.error("CLOAK_WORKBENCH_TOKEN must be a random secret of at least 24 characters")
    worker = BrowserWorker(args.data_dir, args.browser)
    atexit.register(worker.shutdown)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(worker, token))
    server.daemon_threads = True
    print(f"Cloak Workbench worker ready at http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        worker.shutdown()


if __name__ == "__main__":
    main()

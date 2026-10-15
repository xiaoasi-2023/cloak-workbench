"""Production inpage WPF flows and native preservation against an isolated worker."""
import argparse
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
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
from server import BrowserWorker, make_handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--accounts", action="store_true")
    parser.add_argument("--account-center", action="store_true")
    parser.add_argument("--google-login", action="store_true")
    parser.add_argument("--login-accounts", action="store_true")
    parser.add_argument("--session-backup", action="store_true")
    parser.add_argument("--proxy-sessions", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--browser-workspace", action="store_true")
    parser.add_argument("--product-flow", action="store_true")
    parser.add_argument("--environment-semantics", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cloak-proxy-ui-") as directory:
        temp = Path(directory)
        binary = Path.home() / ".cloakbrowser/chromium-146.0.7680.177.5/chrome.exe"
        if not binary.is_file():
            raise RuntimeError("Installed CloakBrowser is required for isolated native regression")
        worker = BrowserWorker(temp / "data", binary)
        worker.catalog.upsert({"id": "ui-residential", "name": "住宅线路 · 验证示例", "kind": "residential",
            "proxy": "http://residential-gateway.invalid:3010", "upstreamProxy": "http://127.0.0.1:7897"})
        worker.catalog.upsert({"id": "ui-standard", "name": "普通线路 · 验证示例", "kind": "standard", "proxy": "socks5://127.0.0.1:1080"})
        worker.catalog.upsert({"id": "ui-failed", "name": "失败线路 · 验证示例", "kind": "standard", "proxy": "http://failed-gateway.invalid:3010"})
        worker.catalog.upsert({"id": "ui-queued", "name": "等待线路 · 验证示例", "kind": "standard", "proxy": "http://queued-gateway.invalid:3010"})
        token = "isolated-ui-fixture-token"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, token))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        worker.create({"id": "ui-browser", "name": "隔离页面切换验证", "startUrl": f"http://127.0.0.1:{server.server_port}/probe"})
        if args.session_backup:
            from session_browser_fixture import offline_document
            from session_backup import dola_origin
            original_navigate = worker.navigate
            worker.navigate = lambda pid, payload: offline_document(worker, pid, payload['url'], '<body><button>Log out</button>Offline session UI fixture</body>') if dola_origin(payload['url']) else original_navigate(pid, payload)
            worker.launch('ui-browser', embedded=True)
            worker.navigate('ui-browser', {'url':'https://www.dola.com/chat/'})
            worker.session_backups._call(worker.cdp_json(worker.runtime['ui-browser']['cdpPort'], '/json/version'), 'Storage.setCookies', {'cookies':[{'name':'sessionid','value':'isolated-ui-session-token','domain':'.dola.com','path':'/','httpOnly':True,'secure':True}]})
            worker.session_backups._evaluate(worker.target(worker.runtime['ui-browser']), "localStorage.setItem('fixture','isolated-ui-storage');sessionStorage.setItem('tab','isolated-tab');true")
        diagnostic = {"reachable": True, "egressIp": "203.0.113.10", "latencyMs": 1800, "isp": "TEST FIXTURE ISP",
            "asn": "AS64500", "country": "US", "countryCode": "US", "region": "Texas", "city": "Dallas", "timezone": "America/Chicago", "isResidential": True, "riskScore": 10, "qualityScore": 96,
            "qualityComplete": True, "checkedAt": "2026-10-14T02:00:00+00:00", "sources": {}, "metadataError": None}
        try:
            env = dict(os.environ, CLOAK_WORKBENCH_ROOT=str(temp), CLOAK_WORKBENCH_TOKEN=token)
            def slow_diagnostic(config):
                time.sleep(1.8 if config.host == "residential-gateway.invalid" else 3)
                if config.host == "failed-gateway.invalid":
                    return {"reachable": False, "error": "Isolated fixture connection failure", "checkedAt": diagnostic["checkedAt"]}
                if config.scheme == "socks5":
                    return dict(diagnostic, country="JP", countryCode="JP", region="Tokyo", city="Tokyo", timezone="Asia/Tokyo")
                return dict(diagnostic)
            def login_fixture(pid, job, runtime, generation, email, password, resume_target=None):
                # No third-party login traffic. Verify actual request/UI/vault
                # contracts with a synthetic provider requiring user attention.
                if password is None:
                    worker.google_login._update(pid, job, 'running', 'authorizing_dola')
                    if job['cancel'].wait(1.5):
                        worker.google_login._update(pid, job, 'cancelled', 'cancelled')
                    else:
                        worker.google_login._update(pid, job, 'needs_attention', 'dola_confirmation' if job['options'].get('ageConfirmed') else 'age_confirmation_required')
                    return
                worker.google_login._update(pid, job, 'running', 'submitting_email')
                if job['cancel'].wait(3):
                    worker.google_login._update(pid, job, 'cancelled', 'cancelled')
                else:
                    worker.google_login._update(pid, job, 'needs_attention', 'verification_required')
            with patch("proxy_catalog.diagnose_proxy", side_effect=slow_diagnostic), patch("account_management.diagnose_proxy", side_effect=slow_diagnostic), patch("server.diagnose_proxy", side_effect=slow_diagnostic), patch.object(worker.google_login, '_run', side_effect=login_fixture) if args.google_login or args.login_accounts else nullcontext():
                result = subprocess.run([str(ROOT / ".tools/dotnet/dotnet.exe"),
                    str(ROOT / ".local/proxy-ui-build/ProxyUiHarness.dll"),
                    f"http://127.0.0.1:{server.server_port}/", str(args.out), *(['--environment-semantics'] if args.environment_semantics else []), *(['--browser-workspace'] if args.browser_workspace else []), *(['--product-flow'] if args.product_flow else []), *(['--login-accounts'] if args.login_accounts else []), *(['--accounts'] if args.accounts else []), *(['--account-center'] if args.account_center else []), *(['--google-login'] if args.google_login else []), *(['--session-backup'] if args.session_backup else []), *(['--proxy-sessions'] if args.proxy_sessions else []), *(['--diagnostics'] if args.diagnostics else [])], cwd=ROOT, env=env, timeout=180)
            if result.returncode:
                raise RuntimeError("UI flow failed; see isolated ui-error.txt")
            mode = "login account inventory" if args.login_accounts else "session backup" if args.session_backup else "Google login" if args.google_login else "account center" if args.account_center else "proxy session editor" if args.proxy_sessions else "account cards" if args.accounts else "diagnostics" if args.diagnostics else "proxy and environment"
            print(f"Production WPF {mode} isolated flow passed")
        finally:
            worker.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    main()

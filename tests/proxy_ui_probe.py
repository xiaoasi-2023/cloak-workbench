"""Production inpage WPF flows and native preservation against an isolated worker."""
import argparse
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
        diagnostic = {"reachable": True, "egressIp": "203.0.113.10", "latencyMs": 1800, "isp": "TEST FIXTURE ISP",
            "asn": "AS64500", "country": "TEST", "isResidential": True, "riskScore": 10, "qualityScore": 96,
            "qualityComplete": True, "checkedAt": "2026-10-14T02:00:00+00:00", "sources": {}, "metadataError": None}
        try:
            env = dict(os.environ, CLOAK_WORKBENCH_ROOT=str(temp), CLOAK_WORKBENCH_TOKEN=token)
            def slow_diagnostic(config):
                time.sleep(1.8 if config.host == "residential-gateway.invalid" else 3)
                if config.host == "failed-gateway.invalid":
                    return {"reachable": False, "error": "Isolated fixture connection failure", "checkedAt": diagnostic["checkedAt"]}
                return dict(diagnostic)
            with patch("proxy_catalog.diagnose_proxy", side_effect=slow_diagnostic):
                result = subprocess.run([str(ROOT / ".tools/dotnet/dotnet.exe"),
                    str(ROOT / ".local/proxy-ui-build/ProxyUiHarness.dll"),
                    f"http://127.0.0.1:{server.server_port}/", str(args.out)], cwd=ROOT, env=env, timeout=100)
            if result.returncode:
                raise RuntimeError("UI flow failed; see isolated ui-error.txt")
            print("Production WPF main proxy page: inventory, job navigation, picker/editor save and native document preservation passed")
        finally:
            worker.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    main()

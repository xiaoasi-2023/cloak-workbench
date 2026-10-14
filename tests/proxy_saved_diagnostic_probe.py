"""Check saved proxy routes without printing authentication or changing config.

Explicitly invoked for the user's proxy diagnostic repair. Public reports only
contain field availability, never passwords, usernames, endpoints or raw bodies.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
from proxy_support import parse_proxy, ProxyRelay
from proxy_diagnostics import diagnose_proxy, request_json, RISK_ENDPOINT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--api", action="store_true")
    args = parser.parse_args()
    entries = json.loads((ROOT / "data/proxies.json").read_text(encoding="utf-8"))
    reports = []
    if args.api:
        session = json.loads((ROOT / ".local/session.json").read_text(encoding="utf-8-sig"))
        opener = build_opener(ProxyHandler({}))
        def call(path, body=None):
            request = Request("http://127.0.0.1:8766/" + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": "Bearer " + session["token"], "Content-Type": "application/json"})
            with opener.open(request, timeout=40) as response:
                return json.load(response)
        public = call("proxies")["proxies"]
        job = call("proxies/check", {"ids": [entry["id"] for entry in public]})
        deadline = time.monotonic() + 120
        while job["state"] in ("running", "cancelling"):
            if time.monotonic() >= deadline:
                raise RuntimeError("Saved diagnostic batch deadline exceeded")
            time.sleep(.3)
            job = call("proxy-checks/" + job["id"])
        if job["state"] != "completed":
            raise RuntimeError("Saved diagnostic batch did not complete")
        after = json.loads((ROOT / "data/proxies.json").read_text(encoding="utf-8"))
        def configuration(values):
            return {entry["id"]: {k: v for k, v in entry.items() if k != "check"} for entry in values}
        assert configuration(entries) == configuration(after), "Proxy configuration changed during test"
        refreshed = call("proxies")["proxies"]
        required = ("egressIp", "country", "region", "isp", "asn", "isResidential", "riskScore", "qualityScore")
        for index, entry in enumerate(refreshed):
            result = entry["check"]
            reports.append({"entryIndex": index + 1, "reachable": result["reachable"],
                "fieldsAvailable": {key: result.get(key) is not None for key in required},
                "rotatingExit": result.get("rotatingExit"), "snapshotSource": result.get("snapshotSource"),
                "persisted": True, "metadataError": result.get("metadataError"), "error": result.get("error")})
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(reports, ensure_ascii=True))
        return
    for index, entry in enumerate(entries):
        config = parse_proxy(entry)
        if args.schema:
            if index:
                break
            with ProxyRelay(config) as relay:
                data = request_json(relay, RISK_ENDPOINT)
            print(json.dumps({"service": "IPPure", "responseFieldTypes": {k: type(v).__name__ for k, v in data.items()}}))
            continue
        result = diagnose_proxy(config)
        required = ("egressIp", "country", "region", "isp", "asn", "isResidential", "riskScore", "qualityScore")
        reports.append({"entryIndex": index + 1, "reachable": result["reachable"],
                        "fieldsAvailable": {key: result.get(key) is not None for key in required},
                        "rotatingExit": result.get("rotatingExit"), "snapshotSource": result.get("snapshotSource"),
                        "metadataError": result.get("metadataError"), "error": result.get("error")})
    if reports:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(reports, ensure_ascii=True))


if __name__ == "__main__":
    main()

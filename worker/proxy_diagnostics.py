"""Fixed, bounded HTTPS diagnostics through exactly one configured relay."""
from datetime import datetime, timezone
from contextlib import nullcontext
from http.client import HTTPSConnection
import ipaddress
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from proxy_support import ProxyRelay

IP_ENDPOINTS = ("https://api.ipify.org?format=json", "https://api64.ipify.org?format=json")
INFO_ENDPOINT = "https://ipinfo.io/json"
RISK_ENDPOINT = "https://my.ippure.com/v1/info"


def request_json(relay, endpoint, timeout=8):
    target = urlsplit(endpoint)
    deadline = time.monotonic() + timeout
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Diagnostic request deadline exceeded")
        return value
    class DeadlineConnection(HTTPSConnection):
        def _tunnel(self):
            self.sock.settimeout(remaining())
            super()._tunnel()
            # HTTPSConnection next performs the verified TLS handshake.
            self.sock.settimeout(remaining())
    connection = DeadlineConnection("127.0.0.1" if relay else target.hostname, relay.port if relay else 443, timeout=remaining())
    active_socket = []
    def abort():
        sock = active_socket[0] if active_socket else connection.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        if relay:
            connection.set_tunnel(target.hostname, 443)
        connection.connect()
        active_socket.append(connection.sock)
        connection.sock.settimeout(remaining())
        connection.request("GET", (target.path or "/") + ("?" + target.query if target.query else ""),
                           headers={"Accept": "application/json", "Connection": "close"})
        connection.sock.settimeout(remaining())
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Diagnostic service unavailable")
        body = bytearray()
        while len(body) <= 65536:
            if connection.sock:
                connection.sock.settimeout(remaining())
            chunk = response.read1(min(4096, 65537 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            remaining()
        if len(body) > 65536:
            raise ValueError("Diagnostic response too large")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Invalid diagnostic response")
        return value
    finally:
        timer.cancel()
        connection.close()


def ip_value(value):
    if not isinstance(value, str) or "%" in value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except (ValueError, TypeError):
        return None


def short_text(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text[:200] if text and not any(ord(c) < 32 for c in text) else None


def quality_score(latency, residential, risk):
    if residential is None or risk is None or latency is None:
        return None
    latency_points = 25 if latency <= 2000 else 20 if latency <= 5000 else 15 if latency <= 10000 else 8 if latency <= 20000 else 0
    return max(0, min(100, round(latency_points + (35 if residential else 0) + 40 * (1 - risk / 100))))


def network_fields(data):
    org = short_text(data.get("asOrganization")) or short_text(data.get("org")) or ""
    match = re.match(r"^(AS\d+)\s*(.*)$", org, re.IGNORECASE)
    asn = short_text(data.get("asn"))
    if asn and re.fullmatch(r"(?:AS)?\d+", asn, re.IGNORECASE):
        asn = "AS" + re.sub(r"^AS", "", asn, flags=re.IGNORECASE)
    else:
        asn = match.group(1).upper() if match else None
    country = short_text(data.get("countryCode")) or short_text(data.get("country"))
    code = country.upper() if country and re.fullmatch(r"[A-Za-z]{2}", country) else None
    zone = short_text(data.get("timezone"))
    if zone:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = None
    return {"country": country, "countryCode": code, "region": short_text(data.get("region")),
            "city": short_text(data.get("city")), "timezone": zone,
            "isp": (match.group(2) or None) if match else (org or None), "asn": asn}


def diagnose_proxy(config):
    result = {"reachable": False, "egressIp": None, "latencyMs": None,
              "protocol": config.scheme or "direct", "mode": "chained" if getattr(config, "upstream", None) else "proxy" if config.address else "direct",
              "country": None, "countryCode": None, "region": None, "city": None, "timezone": None, "isp": None, "asn": None,
              "isResidential": None, "riskScore": None, "riskStatus": "unknown",
              "qualityScore": None, "qualityComplete": False, "sources": {},
              "checkedAt": datetime.now(timezone.utc).isoformat(), "metadataError": None,
              "error": None, "endpoint": None, "snapshotSource": None,
              "rotatingExit": False, "observations": [], "observationNote": None}
    try:
        with ProxyRelay(config) if config.address else nullcontext(None) as relay:
            deadline = time.monotonic() + 32
            def observe(endpoint, source, kind, exit_observation=True):
                """Only self-IP responses are exit observations; lookups are metadata."""
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                started = time.monotonic()
                try:
                    data = request_json(relay, endpoint, timeout=min(8, remaining))
                    ip = ip_value(data.get("ip"))
                    if not ip:
                        raise ValueError("Invalid IP response")
                    elapsed = round((time.monotonic() - started) * 1000)
                    observation = {"source": source, "url": endpoint, "ip": ip,
                                   "latencyMs": elapsed, "isExitObservation": exit_observation}
                    result["observations"].append(observation)
                    result["sources"][kind] = {"url": endpoint, "ip": ip}
                    return {"data": data, "ip": ip, "source": source, "url": endpoint, "latencyMs": elapsed}
                except Exception:
                    return None

            # A rotating gateway may choose a different exit for every TCP connection.
            # Keep risk and network facts attached to the self-IP response that supplied them.
            primary = observe(RISK_ENDPOINT, "ippure", "risk")
            network = observe(INFO_ENDPOINT, "ipinfo", "network")
            snapshot = primary or network
            if snapshot is None:
                for endpoint in IP_ENDPOINTS:
                    snapshot = observe(endpoint, "ipify", "ip")
                    if snapshot:
                        break
            if snapshot is None:
                result["error"] = "Unable to reach the HTTPS IP services through the configured proxy route"
                return result

            result.update(reachable=True, egressIp=snapshot["ip"], endpoint=snapshot["url"],
                          latencyMs=snapshot["latencyMs"], snapshotSource=snapshot["source"])
            result["sources"]["egressIp"] = {"url": snapshot["url"], "ip": snapshot["ip"]}
            result.update(network_fields(snapshot["data"]))
            if primary:
                residential = primary["data"].get("isResidential")
                result["isResidential"] = residential if isinstance(residential, bool) else None
                raw_risk = primary["data"].get("fraudScore")
                try:
                    risk = float(raw_risk) if isinstance(raw_risk, (int, float, str)) and not isinstance(raw_risk, bool) else float("nan")
                except ValueError:
                    risk = float("nan")
                if math.isfinite(risk) and 0 <= risk <= 100:
                    result["riskScore"] = round(risk)
                    result["riskStatus"] = "high" if risk >= 70 else "medium" if risk >= 30 else "low"

            def fill_network(observation):
                if observation and observation["ip"] == snapshot["ip"]:
                    for key, value in network_fields(observation["data"]).items():
                        if result[key] is None:
                            result[key] = value

            fill_network(network)
            if any(result[key] is None for key in ("country", "region", "isp", "asn", "city", "timezone")):
                # ip_value has already canonicalized the literal; this cannot become
                # an arbitrary URL or leak proxy credentials into a lookup request.
                lookup_url = "https://ipinfo.io/" + snapshot["ip"] + "/json"
                lookup = observe(lookup_url, "ipinfo-lookup", "networkLookup", exit_observation=False)
                fill_network(lookup)

            for source in result["sources"].values():
                source["matchesEgress"] = source["ip"] == snapshot["ip"]
            exits = {o["ip"] for o in result["observations"] if o["isExitObservation"]}
            result["rotatingExit"] = len(exits) > 1
            if result["rotatingExit"]:
                result["observationNote"] = "检测期间观察到出口变化；列表展示 " + snapshot["source"] + " 返回的 " + snapshot["ip"] + " 单次出口快照。"

            result["qualityScore"] = quality_score(result["latencyMs"], result["isResidential"], result["riskScore"])
            result["qualityComplete"] = result["qualityScore"] is not None
            missing = []
            if not result["qualityComplete"]:
                missing.append("Risk score or explicit residential classification unavailable")
            if any(result[key] is None for key in ("country", "region", "isp", "asn")):
                missing.append("Some network details unavailable for this exit IP")
            result["metadataError"] = "; ".join(missing) or None
    except Exception:
        result["error"] = "Unable to initialize the configured proxy route"
    return result

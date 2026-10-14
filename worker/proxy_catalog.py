"""Persistent proxy inventory and revision-safe bounded diagnostic jobs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import re
import threading
import uuid

from proxy_support import parse_proxy, ProxyError
from proxy_diagnostics import diagnose_proxy


class ProxyCatalog:
    def __init__(self, worker, error_type):
        self.worker, self.error = worker, error_type
        self.path = worker.data_dir / "proxies.json"
        self.entries = {}
        self.jobs = {}
        self.inflight = set()
        self.closed = False
        if self.path.exists():
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, list):
                raise ValueError("Invalid saved proxy catalog")
            for payload in values:
                entry = self.validate(payload)
                if entry["id"] in self.entries:
                    raise ValueError("Duplicate saved proxy id")
                entry.update(revision=payload.get("revision", 1), check=payload.get("check"))
                self.entries[entry["id"]] = entry

    def validate(self, payload):
        pid = payload.get("id") or uuid.uuid4().hex[:12]
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pid):
            raise self.error(400, "Invalid proxy id")
        name = payload.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise self.error(400, "Proxy name must contain 1 to 120 characters")
        kind = payload.get("kind", "standard")
        if kind not in ("standard", "residential"):
            raise self.error(400, "Unknown proxy type")
        try:
            terminal = parse_proxy(payload)
            upstream = parse_proxy({"proxy": payload.get("upstreamProxy", ""),
                                    "proxyUsername": payload.get("upstreamProxyUsername", ""),
                                    "proxyPassword": payload.get("upstreamProxyPassword", ""),
                                    "clearProxyPassword": payload.get("clearUpstreamProxyPassword", False)})
        except ProxyError as exc:
            raise self.error(400, str(exc)) from None
        if not terminal.address:
            raise self.error(400, "A proxy address is required")
        if kind == "residential" and not upstream.address:
            raise self.error(400, "Residential proxies require an upstream proxy address")
        return {"id": pid, "name": name.strip(), "kind": kind,
                "proxy": terminal.address, "proxyUsername": terminal.username, "proxyPassword": terminal.password,
                "upstreamProxy": upstream.address, "upstreamProxyUsername": upstream.username,
                "upstreamProxyPassword": upstream.password}

    def entry(self, pid):
        if pid not in self.entries:
            raise self.error(404, "Unknown proxy")
        return self.entries[pid]

    def public(self, entry):
        result = {k: copy.deepcopy(v) for k, v in entry.items() if k not in ("proxyPassword", "upstreamProxyPassword")}
        result.update(proxyPasswordSet=bool(entry.get("proxyPassword")),
                      upstreamProxyPasswordSet=bool(entry.get("upstreamProxyPassword")),
                      bindingCount=sum(p.get("proxyId") == entry["id"] for p in self.worker.profiles.values()))
        return result

    def save(self):
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(list(self.entries.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def merge(self, payload):
        pid = payload.get("id")
        if pid is not None and (not isinstance(pid, str) or pid and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pid)):
            raise self.error(400, "Invalid proxy id")
        existing = self.entries.get(pid)
        merged = dict(existing or {}, **payload)
        for prefix in ("proxy", "upstreamProxy"):
            if payload.get("keep" + prefix[0].upper() + prefix[1:] + "Password") is True and existing:
                merged[prefix + "Password"] = existing.get(prefix + "Password", "")
        return self.validate(merged)

    def upsert(self, payload):
        with self.worker.lock:
            entry = self.merge(payload)
            old = self.entries.get(entry["id"])
            if old and any(p.get("proxyId") == entry["id"] and self.worker.is_running(pid) for pid, p in self.worker.profiles.items()):
                raise self.error(409, "Close bound environments before editing this proxy")
            same_route = bool(old and parse_proxy(old) == parse_proxy(entry))
            entry.update(revision=(old or {}).get("revision", 0) + 1,
                         check=copy.deepcopy(old.get("check")) if same_route else None)
            self.entries[entry["id"]] = entry
            try:
                self.save()
            except Exception:
                if old:
                    self.entries[entry["id"]] = old
                else:
                    self.entries.pop(entry["id"], None)
                raise
            return self.public(entry)

    def delete(self, pid):
        with self.worker.lock:
            entry = self.entry(pid)
            if any(p.get("proxyId") == pid for p in self.worker.profiles.values()):
                raise self.error(409, "This proxy is still bound to an environment")
            if pid in self.inflight:
                raise self.error(409, "This proxy is being checked")
            del self.entries[pid]
            try:
                self.save()
            except Exception:
                self.entries[pid] = entry
                raise
            return {"deleted": pid}

    def draft_test(self, payload):
        with self.worker.lock:
            draft = self.merge(payload)
            config = parse_proxy(draft)
            saved = self.entries.get(draft["id"])
            revision = saved["revision"] if saved else None
        result = diagnose_proxy(config)
        with self.worker.lock:
            current = self.entries.get(draft["id"])
            # Only the exact saved route may receive an editor's check result.
            # Unsaved changes never replace diagnostics for a different route.
            if (not self.closed and current and current["revision"] == revision
                    and parse_proxy(current) == config and draft["id"] not in self.inflight):
                previous = current.get("check")
                current["check"] = copy.deepcopy(result)
                try:
                    self.save()
                except Exception:
                    current["check"] = previous
        return result

    def snapshot(self, job_id):
        with self.worker.lock:
            if job_id not in self.jobs:
                raise self.error(404, "Unknown proxy check job")
            return copy.deepcopy({k: v for k, v in self.jobs[job_id].items() if k != "cancel"})

    def start(self, ids):
        with self.worker.lock:
            if self.closed:
                raise self.error(409, "Proxy diagnostics are shutting down")
            if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(pid, str) for pid in ids):
                raise self.error(400, "Select between 1 and 500 proxy ids")
            if any(job["state"] in ("running", "cancelling") for job in self.jobs.values()):
                raise self.error(409, "A proxy check job is already running")
            ids = list(dict.fromkeys(ids))
            entries = [copy.deepcopy(self.entry(pid)) for pid in ids]
            # Bound retained history, without dropping the current live job.
            while len(self.jobs) >= 30:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {"id": job_id, "state": "running", "total": len(ids), "completed": 0,
                                 "results": [], "items": {pid: "queued" for pid in ids}, "cancel": threading.Event()}
            self.inflight.update(ids)
            try:
                threading.Thread(target=self.run, args=(job_id, entries), daemon=True).start()
            except Exception:
                self.jobs.pop(job_id, None)
                self.inflight.difference_update(ids)
                raise self.error(503, "Unable to start proxy diagnostics") from None
            return self.snapshot(job_id)

    def close(self):
        with self.worker.lock:
            self.closed = True
            for job in self.jobs.values():
                if job["state"] in ("running", "cancelling"):
                    job["state"] = "cancelling"
                    job["cancel"].set()

    def cancel(self, job_id):
        with self.worker.lock:
            self.snapshot(job_id)
            job = self.jobs[job_id]
            if job["state"] == "running":
                job["state"] = "cancelling"
                job["cancel"].set()
            return self.snapshot(job_id)

    def run(self, job_id, entries):
        job = self.jobs[job_id]
        failed = False
        def check(entry):
            with self.worker.lock:
                if job["cancel"].is_set():
                    job["items"][entry["id"]] = "cancelled"
                    return {"cancelled": True}
                job["items"][entry["id"]] = "running"
            try:
                return diagnose_proxy(parse_proxy(entry))
            except Exception:
                return {"reachable": False, "error": "Proxy diagnostics failed", "qualityScore": None, "qualityComplete": False}
        try:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="proxy-check") as pool:
                futures = {pool.submit(check, entry): entry for entry in entries}
                for future in as_completed(futures):
                    entry, result = futures[future], future.result()
                    with self.worker.lock:
                        current = self.entries.get(entry["id"])
                        applied = bool(not self.closed and current and current["revision"] == entry["revision"] and not result.get("cancelled"))
                        if applied:
                            previous = current.get("check")
                            current["check"] = result
                            try:
                                self.save()
                            except Exception:
                                current["check"] = previous
                                applied = False
                        job["results"].append({"id": entry["id"], "check": result, "applied": applied})
                        job["items"][entry["id"]] = "cancelled" if result.get("cancelled") else "completed" if result.get("reachable") else "failed"
                        job["completed"] += 1
                        self.inflight.discard(entry["id"])
        except Exception:
            failed = True
        finally:
            with self.worker.lock:
                self.inflight.difference_update(e["id"] for e in entries)
                job["state"] = "failed" if failed else "cancelled" if job["cancel"].is_set() else "completed"
                for pid, state in job["items"].items():
                    if state in ("queued", "running"):
                        job["items"][pid] = "aborted" if failed else "cancelled"

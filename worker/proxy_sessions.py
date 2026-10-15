"""Explicit provider templates and persisted, independent environment sessions."""
import copy
import hashlib
import json
import string
import time
import uuid

from proxy_support import parse_proxy, ProxyError


def session_policy(payload, error):
    mode = payload.get("sessionMode", "fixed")
    template = payload.get("sessionTemplate", "")
    lifetime = payload.get("sessionLifetimeMinutes", 0)
    if mode not in ("fixed", "per_profile"):
        raise error(400, "Unknown residential session mode")
    if not isinstance(template, str) or len(template) > 1024 or any(ord(c) < 32 for c in template):
        raise error(400, "Invalid residential session template")
    if type(lifetime) is not int or not 0 <= lifetime <= 43200:
        raise error(400, "Session lifetime must be between 0 and 43200 minutes")
    if mode == "per_profile":
        if payload.get("kind") != "residential":
            raise error(400, "Independent sessions require a residential proxy")
        try:
            fields = [field for _, field, spec, conversion in string.Formatter().parse(template)
                      if field is not None]
            if (set(fields) != {"user", "session"} or
                    any(spec or conversion for _, field, spec, conversion in string.Formatter().parse(template))):
                raise ValueError()
            username = template.format(user=payload.get("proxyUsername", ""), session="a" * 16)
            if not payload.get("proxyUsername"):
                raise ValueError()
            parse_proxy(dict(payload, proxyUsername=username))
        except (ValueError, KeyError, IndexError, ProxyError):
            raise error(400, "Use an explicit provider template containing {user} and {session}") from None
    return dict(sessionMode=mode, sessionTemplate=template, sessionLifetimeMinutes=lifetime)


def policy_key(entry):
    # No secrets are persisted in the session index or returned metadata.
    fields = ("id", "proxy", "proxyUsername", "proxyPassword", "upstreamProxy",
              "upstreamProxyUsername", "upstreamProxyPassword", "sessionMode",
              "sessionTemplate", "sessionLifetimeMinutes")
    return hashlib.sha256(json.dumps([entry.get(k) for k in fields], ensure_ascii=False).encode()).hexdigest()


def effective_entry(entry, token):
    result = copy.deepcopy(entry)
    if entry.get("sessionMode") == "per_profile":
        result["proxyUsername"] = entry["sessionTemplate"].format(user=entry["proxyUsername"], session=token)
    return result


class ProxySessions:
    def __init__(self, catalog):
        self.catalog = catalog
        self.path = catalog.worker.data_dir / "proxy-sessions.json"
        self.records = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.records, dict) or any(not isinstance(v, dict) for v in self.records.values()):
            raise ValueError("Invalid saved proxy sessions")

    def save(self):
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def record(self, profile, entry):
        record = self.records.get(profile["id"])
        return record if record and record.get("policy") == policy_key(entry) else None

    def replace(self, profile, entry, reason):
        previous = self.records.get(profile["id"])
        now = time.time()
        record = dict(proxyId=entry["id"], policy=policy_key(entry), session=uuid.uuid4().hex[:16],
                      createdAt=now, rotatedAt=now, rotationReason=reason)
        self.records[profile["id"]] = record
        try:
            self.save()
        except Exception:
            if previous is None:
                self.records.pop(profile["id"], None)
            else:
                self.records[profile["id"]] = previous
            raise
        self.invalidate(profile["id"])
        return record

    def invalidate(self, profile_id):
        worker = self.catalog.worker
        getattr(worker, "account_checks", {}).pop(profile_id, None)
        accounts = getattr(worker, "accounts", None)
        if accounts and hasattr(accounts, "invalidate_proxy"):
            accounts.invalidate_proxy(profile_id)

    def expired(self, record, entry):
        lifetime = entry.get("sessionLifetimeMinutes", 0)
        return bool(lifetime and time.time() >= record["rotatedAt"] + lifetime * 60)

    def resolve(self, profile, entry, refresh=False):
        if entry.get("sessionMode") != "per_profile":
            return copy.deepcopy(entry)
        record = self.record(profile, entry)
        if record is None:
            if not refresh:
                return copy.deepcopy(entry)
            record = self.replace(profile, entry, "created" if profile["id"] not in self.records else "configuration_changed")
        elif refresh and self.expired(record, entry) and not self.catalog.worker.is_running(profile["id"]):
            record = self.replace(profile, entry, "expired")
        return effective_entry(entry, record["session"])

    def info(self, profile, entry):
        result = dict(mode=entry.get("sessionMode", "fixed"), proxyId=entry["id"],
                      session=None, createdAt=None, rotatedAt=None, expiresAt=None,
                      expired=False, rotationReason=None)
        if result["mode"] == "fixed":
            return result
        record = self.record(profile, entry)
        if record:
            result.update({k: record[k] for k in ("session", "createdAt", "rotatedAt", "rotationReason")})
            lifetime = entry.get("sessionLifetimeMinutes", 0)
            result.update(expiresAt=record["rotatedAt"] + lifetime * 60 if lifetime else None,
                          expired=self.expired(record, entry))
        return result

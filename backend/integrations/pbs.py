"""Proxmox Backup Server integration – datastores, backup groups, verification and tasks."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

DEFAULT_PORT = 8007
TASK_WINDOW_HOURS = 24
TASK_LIMIT = 1000
# Newest groups/snapshots are what matters; keep the stored lists bounded.
MAX_ISSUES = 50
# Every namespace costs two requests per datastore.
MAX_NAMESPACES = 100
NAMESPACE_CONCURRENCY = 8

# Task worker types that matter for backup health. PBS worker types are
# free-form strings ("backup", "verificationjob", "garbage_collection",
# "syncjob", "prunejob", ...), so match on a fragment.
_RELEVANT_TASK_FRAGMENTS = ("backup", "verif", "garbage", "sync", "prune")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _int(value) -> int | None:
    """An int, or None for anything else (bools and floats with fractions included)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _epoch_iso(value) -> str | None:
    """Unix epoch seconds -> ISO 8601 UTC string, or None."""
    ts = _int(value)
    if ts is None or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def parse_iso(value) -> datetime | None:
    """Timezone-aware UTC datetime from an ISO 8601 string, else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _base_url(host: str) -> str:
    """Normalise a user-entered host to ``https://host:port`` (default port 8007)."""
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("PBS host is not configured")
    if "://" not in host:
        host = f"https://{host}"
    parts = urlsplit(host)
    netloc = parts.netloc
    if parts.port is None:
        netloc = f"{netloc}:{DEFAULT_PORT}"
    return f"{parts.scheme}://{netloc}"


def _is_relevant_task(worker_type: str) -> bool:
    wt = (worker_type or "").lower()
    return any(frag in wt for frag in _RELEVANT_TASK_FRAGMENTS)


def _task_result(status: str) -> str:
    """ok | warning | error for a finished task's status string."""
    if status == "OK":
        return "ok"
    if status.upper().startswith("WARNINGS"):
        return "warning"
    return "error"


# ── API Client ────────────────────────────────────────────────────────────────


class PbsAPI:
    def __init__(self, host: str, token_id: str, token_secret: str, verify_ssl: bool = True):
        self.base = _base_url(host)
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify_ssl = verify_ssl
        self.unavailable: set[str] = set()

    def _headers(self) -> dict:
        return {"Authorization": f"PBSAPIToken={self.token_id}:{self.token_secret}"}

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None):
        """The ``data`` member of a PBS answer. Raises with a message free of secrets."""
        resp = await client.get(f"{self.base}/api2/json{path}", headers=self._headers(), params=params)
        if resp.status_code in (401, 403):
            raise ValueError(f"PBS rejected the API token (HTTP {resp.status_code}) for {path}")
        if resp.status_code >= 400:
            raise ValueError(f"PBS request {path} failed (HTTP {resp.status_code})")
        body = resp.json()
        if not isinstance(body, dict) or "data" not in body:
            raise ValueError(f"PBS request {path} returned an unexpected answer")
        return body["data"]

    async def _optional(self, label: str, coro):
        """Result of an optional sub-request; None (and a note) when it fails."""
        try:
            return await coro
        except Exception as exc:
            log.warning("PBS %s unavailable: %s", label, exc)
            self.unavailable.add(label)
            return None

    async def fetch_all(self) -> dict:
        self.unavailable = set()
        collected_at = datetime.now(timezone.utc)
        async with safe_async_client(verify=self.verify_ssl, timeout=30.0) as client:
            # Core request: without it there is nothing to show, and an auth
            # failure must surface as an error rather than as "no datastores".
            usage = await self._get(client, "/status/datastore-usage")
            if not isinstance(usage, list):
                raise ValueError("PBS datastore usage returned an unexpected answer")

            since = int((collected_at - timedelta(hours=TASK_WINDOW_HOURS)).timestamp())
            version_t = self._optional("version", self._get(client, "/version"))
            node_t = self._optional("node status", self._get(client, "/nodes/localhost/status"))
            tasks_t = self._optional("tasks", self._get(
                client, "/nodes/localhost/tasks", {"since": since, "limit": TASK_LIMIT}))
            version, node, tasks = await asyncio.gather(version_t, node_t, tasks_t)

            groups: dict[str, list | None] = {}
            snapshots: dict[str, list | None] = {}
            sem = asyncio.Semaphore(NAMESPACE_CONCURRENCY)

            async def ns_list(store: str, ns: str, kind: str):
                async with sem:
                    return await self._get(client, f"/admin/datastore/{store}/{kind}",
                                           {"ns": ns} if ns else None)

            async def per_store(store: str) -> None:
                # groups/snapshots only list one namespace (the root by default);
                # backups in namespaces would otherwise be invisible.
                spaces = [""]
                listed = await self._optional(f"namespaces ({store})",
                                              self._get(client, f"/admin/datastore/{store}/namespace"))
                if isinstance(listed, list):
                    for item in listed:
                        ns = item.get("ns") if isinstance(item, dict) else None
                        if isinstance(ns, str) and ns and ns not in spaces:
                            spaces.append(ns)
                elif listed is not None:
                    self.unavailable.add(f"namespaces ({store})")
                if len(spaces) > MAX_NAMESPACES:
                    spaces = spaces[:MAX_NAMESPACES]
                    self.unavailable.add(f"namespaces ({store}, truncated)")
                g_res, s_res = await asyncio.gather(
                    asyncio.gather(*(self._optional(f"groups ({store})", ns_list(store, ns, "groups"))
                                     for ns in spaces)),
                    asyncio.gather(*(self._optional(f"snapshots ({store})", ns_list(store, ns, "snapshots"))
                                     for ns in spaces)),
                )
                groups[store] = _merge_ns(spaces, g_res)
                snapshots[store] = _merge_ns(spaces, s_res)
                if groups[store] is None:
                    self.unavailable.add(f"groups ({store})")
                if snapshots[store] is None:
                    self.unavailable.add(f"snapshots ({store})")

            stores = [
                u["store"] for u in usage
                if isinstance(u, dict) and u.get("store") and not u.get("error")
                and u.get("mount-status") != "notmounted"
            ]
            await asyncio.gather(*(per_store(s) for s in stores))

        return parse_pbs_data(
            collected_at=collected_at, version=version, node=node, usage=usage,
            groups=groups, snapshots=snapshots, tasks=tasks,
            tasks_limit=TASK_LIMIT, unavailable=sorted(self.unavailable),
        )

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0) as client:
                await self._get(client, "/version")
            return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def _merge_ns(spaces: list[str], results: list) -> list | None:
    """One list over all namespaces, each item tagged with its ``ns``; None
    when any namespace could not be read (a partial list is not the full one)."""
    merged: list = []
    for ns, items in zip(spaces, results):
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict):
                item = {**item, "ns": ns}
            merged.append(item)
    return merged


def _parse_tasks(tasks, collected_at: datetime, limit: int) -> dict | None:
    """Finished backup-relevant tasks of the window, with failures that a later
    successful run of the same job has superseded marked as such."""
    if tasks is None:
        return None
    finished = []
    for t in tasks if isinstance(tasks, list) else []:
        if not isinstance(t, dict):
            continue
        wtype = str(t.get("worker_type") or "")
        status = t.get("status")
        end = _int(t.get("endtime"))
        if not _is_relevant_task(wtype) or not isinstance(status, str) or not status or end is None:
            continue  # running, or not a backup-health task
        finished.append({
            "upid": str(t.get("upid") or ""),
            "type": wtype,
            "worker_id": str(t.get("worker_id") or ""),
            "user": str(t.get("user") or ""),
            "started_at": _epoch_iso(t.get("starttime")),
            "ended_at": _epoch_iso(end),
            "status": status,
            "result": _task_result(status),
            "_end": end,
        })
    finished.sort(key=lambda x: x["_end"])
    latest_ok: dict[tuple[str, str], int] = {}
    for t in finished:
        if t["result"] != "error":
            latest_ok[(t["type"], t["worker_id"])] = t["_end"]
    issues = []
    for t in finished:
        if t["result"] == "ok":
            continue
        later_ok = latest_ok.get((t["type"], t["worker_id"]))
        t["superseded"] = bool(t["result"] == "error" and later_ok is not None and later_ok > t["_end"])
        issues.append(t)
    for t in finished:
        t.pop("_end", None)
    issues.reverse()  # newest first
    return {
        "window_hours": TASK_WINDOW_HOURS,
        "total": len(finished),
        "failed": sum(1 for t in finished if t["result"] == "error"),
        "warnings": sum(1 for t in finished if t["result"] == "warning"),
        "truncated": isinstance(tasks, list) and len(tasks) >= limit > 0,
        "issues": issues[:MAX_ISSUES],
    }


def _parse_store(entry: dict, groups, snapshots) -> dict:
    total, used, avail = _int(entry.get("total")), _int(entry.get("used")), _int(entry.get("avail"))
    if avail is not None and avail < 0:
        avail = None  # PBS reports -1 on error
    pct = round(used / total * 100, 1) if total and used is not None and total > 0 else None
    err = entry.get("error")

    # Group the snapshots so every group can report its verification state.
    # A group is identified by namespace, type and id ("ns" is "" for the root).
    by_group: dict[tuple[str, str, str], list[dict]] = {}
    for s in snapshots if isinstance(snapshots, list) else []:
        if isinstance(s, dict):
            key = (str(s.get("ns") or ""), str(s.get("backup-type") or ""), str(s.get("backup-id") or ""))
            by_group.setdefault(key, []).append(s)

    parsed_groups = None
    if isinstance(groups, list):
        parsed_groups = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            ns, gtype, gid = str(g.get("ns") or ""), str(g.get("backup-type") or ""), str(g.get("backup-id") or "")
            verification: str | None = None
            failed = None
            if isinstance(snapshots, list):
                snaps = by_group.get((ns, gtype, gid), [])
                failed = sum(1 for s in snaps if (s.get("verification") or {}).get("state") == "failed")
                newest = max(snaps, key=lambda s: _int(s.get("backup-time")) or 0, default=None)
                if newest is not None:
                    state = (newest.get("verification") or {}).get("state")
                    verification = state if state in ("ok", "failed") else "unverified"
            parsed_groups.append({
                "type": gtype, "id": gid, "ns": ns,
                "name": f"{ns}/{gtype}/{gid}" if ns else f"{gtype}/{gid}",
                "backup_count": _int(g.get("backup-count")),
                "last_backup": _epoch_iso(g.get("last-backup")),
                "owner": g.get("owner") or "",
                "comment": g.get("comment") or "",
                "verification": verification,
                "verify_failed_count": failed,
            })
        parsed_groups.sort(key=lambda x: x["name"])

    snap_stats = None
    if isinstance(snapshots, list):
        states = [(s.get("verification") or {}).get("state") for s in snapshots if isinstance(s, dict)]
        snap_stats = {
            "total": len(states),
            "verified_ok": sum(1 for x in states if x == "ok"),
            "verify_failed": sum(1 for x in states if x == "failed"),
            "unverified": sum(1 for x in states if x not in ("ok", "failed")),
        }

    return {
        "name": str(entry.get("store") or ""),
        "mount_status": entry.get("mount-status") or "",
        "error": err if isinstance(err, str) and err else None,
        "total": total, "used": used, "avail": avail, "pct": pct,
        "estimated_full": _epoch_iso(entry.get("estimated-full-date")),
        "groups": parsed_groups,
        "snapshots": snap_stats,
    }


def parse_pbs_data(*, collected_at: datetime, version, node, usage, groups: dict, snapshots: dict,
                   tasks, tasks_limit: int = TASK_LIMIT, unavailable: list[str] | None = None) -> dict:
    """Turn the raw PBS answers into the snapshot. ``None`` means "not fetched"."""
    datastores = []
    for entry in usage if isinstance(usage, list) else []:
        if isinstance(entry, dict) and entry.get("store"):
            store = str(entry["store"])
            datastores.append(_parse_store(entry, groups.get(store), snapshots.get(store)))
    datastores.sort(key=lambda d: d["name"])

    ver = None
    if isinstance(version, dict):
        ver = {"version": version.get("version") or "", "release": version.get("release") or "",
               "repoid": version.get("repoid") or ""}

    node_info = None
    if isinstance(node, dict):
        mem, root = node.get("memory") or {}, node.get("root") or {}
        load = node.get("loadavg")
        node_info = {
            "uptime_s": _int(node.get("uptime")),
            "cpu_pct": round(node["cpu"] * 100, 1) if isinstance(node.get("cpu"), (int, float)) else None,
            "mem_total": _int(mem.get("total")), "mem_used": _int(mem.get("used")),
            "root_total": _int(root.get("total")), "root_used": _int(root.get("used")),
            "load_1m": load[0] if isinstance(load, list) and load and isinstance(load[0], (int, float)) else None,
            "kernel": (node.get("current-kernel") or {}).get("release") if isinstance(node.get("current-kernel"), dict) else None,
        }

    all_groups = [g for d in datastores for g in (d["groups"] or [])]
    # A readable datastore whose groups or snapshots could not be read makes the
    # totals unknown: a count over the rest would look like a healthy "0".
    readable = [d for d in datastores if not d["error"] and d["mount_status"] != "notmounted"]
    groups_known = all(d["groups"] is not None for d in readable)
    verify_known = groups_known and all(d["snapshots"] is not None for d in readable)
    return {
        "collected_at": collected_at.astimezone(timezone.utc).isoformat(),
        "version": ver,
        "node": node_info,
        "datastores": datastores,
        "tasks": _parse_tasks(tasks, collected_at, tasks_limit),
        "unavailable": sorted(unavailable or []),
        "totals": {
            "datastores": len(datastores),
            "groups": len(all_groups) if groups_known else None,
            "verify_failed_groups": sum(1 for g in all_groups if (g["verify_failed_count"] or 0) > 0)
            if verify_known else None,
        },
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class PbsIntegration(BaseIntegration):
    name = "pbs"
    display_name = "Proxmox Backup Server"
    icon = "proxmox"
    color = "orange"
    description = "Monitor Proxmox Backup Server datastores, backup freshness, verification and tasks."
    default_interval_seconds = 300

    config_fields = [
        ConfigField(key="host", label="Host URL", field_type="url", placeholder="https://pbs.example.com:8007"),
        ConfigField(key="token_id", label="API token ID", placeholder="monitor@pbs!nodeglow"),
        ConfigField(key="token_secret", label="API token secret", field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox", required=False, default=True),
    ]

    alert_thresholds = (
        Threshold("ds_warn_pct", "Datastore usage warning", 85, "%", 1, 100),
        Threshold("ds_crit_pct", "Datastore usage critical", 95, "%", 1, 100),
        Threshold("stale_hours", "Backup group older than", 48, "h", 0, zero_disables=True),
    )

    def _api(self) -> PbsAPI:
        return PbsAPI(
            host=self.config["host"],
            token_id=self.config["token_id"],
            token_secret=self.config["token_secret"],
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc) or type(exc).__name__)

    async def health_check(self) -> bool:
        try:
            return await self._api().health_check()
        except Exception:
            return False

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        collected = parse_iso(data.get("collected_at"))
        warn, crit = self.threshold("ds_warn_pct"), self.threshold("ds_crit_pct")
        stale_h = self.threshold("stale_hours")

        for ds in data.get("datastores") or []:
            if not isinstance(ds, dict):
                continue
            name = str(ds.get("name") or "")
            if ds.get("error"):
                alerts.append(Alert(severity="critical", title="Datastore error",
                                    detail=f"{name}: {ds['error']}", entity=name))
            pct = ds.get("pct")
            if isinstance(pct, (int, float)):
                if pct >= crit:
                    alerts.append(Alert(severity="critical", title="Datastore nearly full",
                                        detail=f"{name}: {pct}% used", entity=name))
                elif pct >= warn:
                    alerts.append(Alert(severity="warning", title="Datastore filling up",
                                        detail=f"{name}: {pct}% used", entity=name))

            for g in ds.get("groups") or []:
                if not isinstance(g, dict):
                    continue
                gname = f"{name}:{g.get('name') or '?'}"
                if (g.get("verify_failed_count") or 0) > 0:
                    alerts.append(Alert(
                        severity="critical", title="Verification failed",
                        detail=f"{gname}: {g['verify_failed_count']} snapshot(s) failed verification",
                        entity=gname))
                last = parse_iso(g.get("last_backup"))
                if stale_h > 0 and collected is not None and last is not None:
                    age_h = (collected - last).total_seconds() / 3600
                    if age_h > stale_h:
                        alerts.append(Alert(
                            severity="warning", title="Backup group overdue",
                            detail=f"{gname}: newest snapshot is {age_h:.0f} h old (limit {stale_h:g} h)",
                            entity=gname))

        tasks = data.get("tasks")
        for t in (tasks.get("issues") if isinstance(tasks, dict) else None) or []:
            if not isinstance(t, dict) or t.get("result") != "error" or t.get("superseded"):
                continue
            entity = f"{t.get('type') or '?'}: {t.get('worker_id') or '-'}"
            alerts.append(Alert(severity="critical", title="PBS task failed",
                                detail=f"{entity} at {t.get('ended_at') or '?'}: {t.get('status') or '?'}",
                                entity=entity))
        return alerts

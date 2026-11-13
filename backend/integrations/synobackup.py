"""Synology backup integration – Hyper Backup and Active Backup for Business task status (DSM Web API).

The backup APIs are thinly documented. Everything a NAS does not answer (a
package that is not installed, a missing permission, an unknown response) is
reported in ``data["unavailable"]``, never as an empty healthy task list.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from utils.net_safety import safe_async_client

from integrations import _backup_rules as backup_rules
from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold
from integrations.synology import SynologyAPI

log = logging.getLogger(__name__)

HYPER_BACKUP = "Hyper Backup"
ACTIVE_BACKUP = "Active Backup for Business"
_LABEL = {"hyper_backup": HYPER_BACKUP, "active_backup": ACTIVE_BACKUP}

# DSM error codes: 102 API does not exist, 103 method does not exist,
# 104 version not supported, 105 no permission, 114 lost required parameter.
_ERR_LOST_PARAM = 114

_HB_STATUS_FIELDS = ["last_bkp_time", "next_bkp_time", "last_bkp_result", "is_modified", "last_bkp_progress"]

_NAS_TIME_FORMATS = ("%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")
_TASK_TIME_FORMATS = (
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%a %b %d %H:%M:%S %Y",
)


class DsmError(ValueError):
    """A DSM answer with ``success: false``."""

    def __init__(self, what: str, code):
        super().__init__(f"{what} failed (DSM error code: {code})")
        self.code = code


# ── Time helpers ──────────────────────────────────────────────────────────────


def parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _strptime_any(text: str, formats: tuple[str, ...]) -> datetime | None:
    text = " ".join(text.split())
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def nas_utc_offset(nas_time, collected_at: datetime) -> timedelta | None:
    """The NAS clock's offset from UTC, from its reported local time.

    Hyper Backup reports local wall-clock strings without a zone. The offset is
    the difference between the NAS's local "now" and the collection instant,
    rounded to a quarter hour; anything beyond +/-14 h is rejected."""
    if not isinstance(nas_time, str) or not nas_time.strip():
        return None
    naive = _strptime_any(nas_time.strip(), _NAS_TIME_FORMATS)
    if naive is None:
        return None
    delta = naive - collected_at.astimezone(timezone.utc).replace(tzinfo=None)
    seconds = round(delta.total_seconds() / 900) * 900
    if abs(seconds) > 14 * 3600:
        return None
    return timedelta(seconds=seconds)


def to_utc_iso(value, offset: timedelta | None) -> tuple[str | None, bool]:
    """(UTC ISO string or None, readable). Empty values are readable-and-missing;
    a value that cannot be placed on the UTC axis is not."""
    if value is None or value is False or (isinstance(value, str) and not value.strip()):
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, str):
        try:
            value = int(value.strip())  # epoch seconds delivered as a string
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        if secs <= 0:
            return None, True  # 0 / -1: "never"
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat(), True
        except (OverflowError, OSError, ValueError):
            return None, False
    if isinstance(value, str):
        text = value.strip()
        dt = None
        if "T" in text:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        if dt is None:
            dt = _strptime_any(text, _TASK_TIME_FORMATS)
        if dt is None:
            return None, False
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).isoformat(), True
        if offset is None:
            return None, False  # a wall-clock time we cannot place on the UTC axis
        return dt.replace(tzinfo=timezone(offset)).astimezone(timezone.utc).isoformat(), True
    return None, False


# ── API Client ────────────────────────────────────────────────────────────────


class SynoBackupAPI(SynologyAPI):
    """Reuses the login/logout handling of the Synology DSM integration."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unavailable: set[str] = set()

    @staticmethod
    def _unwrap(body, what: str):
        if not isinstance(body, dict):
            raise ValueError(f"{what} returned an unexpected answer")
        if not body.get("success"):
            code = (body.get("error") or {}).get("code", "unknown") if isinstance(body.get("error"), dict) else "unknown"
            raise DsmError(what, code)
        data = body.get("data")
        return data if data is not None else {}

    async def _call(self, client: httpx.AsyncClient, sid: str, path: str, api: str, method: str,
                    version: int, params: dict | None = None):
        query = {"api": api, "version": str(version), "method": method, "_sid": sid}
        if params:
            query.update(params)
        resp = await client.get(f"{self.base}/webapi/{path}", params=query)
        if resp.status_code >= 400:
            # Not raise_for_status(): its message carries the URL with the session id.
            raise ValueError(f"{api}.{method} failed (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise ValueError(f"{api}.{method} returned no JSON") from None
        return self._unwrap(body, f"{api}.{method}")

    async def _discover(self, client: httpx.AsyncClient, sid: str) -> dict:
        """API name -> {path, minVersion, maxVersion} for the APIs this NAS knows."""
        data = await self._call(client, sid, "query.cgi", "SYNO.API.Info", "query", 1,
                                {"query": "SYNO.Backup.Task,SYNO.ActiveBackup.Task"})
        return data if isinstance(data, dict) else {}

    async def _hyper_backup(self, client: httpx.AsyncClient, sid: str, info: dict) -> list[dict]:
        api = info["SYNO.Backup.Task"]
        path, version = api.get("path") or "entry.cgi", int(api.get("minVersion") or 1)
        data = await self._call(client, sid, path, "SYNO.Backup.Task", "list", version)
        tasks = data.get("task_list") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            raise ValueError("Hyper Backup task list has an unexpected shape")
        merged = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            item = dict(t)
            try:
                status = await self._call(client, sid, path, "SYNO.Backup.Task", "status", version, {
                    "task_id": t.get("task_id"), "blOnline": "false",
                    "additional": json.dumps(_HB_STATUS_FIELDS)})
                if isinstance(status, dict):
                    extra = status.pop("additional", None)
                    item.update(status)
                    if isinstance(extra, dict):
                        item.update(extra)
            except Exception as exc:
                log.warning("Hyper Backup status unavailable for task %s: %s", t.get("task_id"), exc)
                self.unavailable.add(f"{HYPER_BACKUP} status ({t.get('name') or t.get('task_id')})")
            merged.append(item)
        return merged

    async def _active_backup(self, client: httpx.AsyncClient, sid: str, info: dict) -> list[dict]:
        api = info["SYNO.ActiveBackup.Task"]
        path = api.get("path") or "entry.cgi"
        params = {"load_status": "true", "load_result": "true", "load_devices": "false", "load_versions": "false"}
        try:
            data = await self._call(client, sid, path, "SYNO.ActiveBackup.Task", "list", 1, params)
        except DsmError as exc:
            if exc.code != _ERR_LOST_PARAM:
                raise
            data = await self._call(client, sid, path, "SYNO.ActiveBackup.Task", "list", 1,
                                    {**params, "filter": "{}"})
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            raise ValueError("Active Backup task list has an unexpected shape")
        return [t for t in tasks if isinstance(t, dict)]

    async def fetch_all(self) -> dict:
        self.unavailable = set()
        collected_at = datetime.now(timezone.utc)
        hb: list[dict] | None = None
        abb: list[dict] | None = None
        errors: list[str] = []
        nas_time = None
        async with safe_async_client(verify=self.verify_ssl, timeout=30) as client:
            sid = await self._login(client)
            try:
                info = await self._discover(client, sid)  # core: nothing works without it
                try:
                    clock = await self._call(client, sid, "entry.cgi", "SYNO.DSM.Info", "getinfo", 2)
                    nas_time = clock.get("time") if isinstance(clock, dict) else None
                except Exception as exc:
                    log.warning("Synology NAS clock unavailable: %s", exc)

                for key, api_name, fn in (("hyper_backup", "SYNO.Backup.Task", self._hyper_backup),
                                          ("active_backup", "SYNO.ActiveBackup.Task", self._active_backup)):
                    label = _LABEL[key]
                    if api_name not in info:
                        self.unavailable.add(label)
                        errors.append(f"{label}: not installed or not permitted")
                        continue
                    try:
                        result = await fn(client, sid, info)
                    except Exception as exc:
                        log.warning("%s unavailable: %s", label, exc)
                        self.unavailable.add(label)
                        errors.append(f"{label}: {exc}")
                        continue
                    if key == "hyper_backup":
                        hb = result
                    else:
                        abb = result
            finally:
                await self._logout(client, sid)
        if hb is None and abb is None:
            raise ValueError("Neither Hyper Backup nor Active Backup for Business could be read ("
                             + "; ".join(errors) + ")")
        return parse_synobackup_data(
            collected_at=collected_at, nas_time=nas_time, hyper_backup=hb, active_backup=abb,
            unavailable=sorted(self.unavailable))


# ── Parser ────────────────────────────────────────────────────────────────────


def _hb_result(raw) -> str | None:
    """success | partial | failed | none, or None when absent or not recognised."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if s in ("", "none"):
        return "none"
    if s in ("done", "success", "ok"):
        return "success"
    if "partial" in s or "warn" in s:
        return "partial"
    if "fail" in s or "error" in s:
        return "failed"
    return None


_ABB_RESULT = {2: "success", 3: "partial", 4: "failed", 5: "cancelled", 6: "none"}
_ABB_RESULT_STR = {"success": "success", "partial_success": "partial", "fail": "failed", "failed": "failed",
                   "cancel": "cancelled", "cancelled": "cancelled", "no_backup": "none"}


def _abb_result(raw) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return _ABB_RESULT.get(raw)
    if isinstance(raw, str):
        return _ABB_RESULT_STR.get(raw.strip().lower())
    return None


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _parse_hb(t: dict, offset: timedelta | None, unreadable: list[bool]) -> dict:
    def when(*keys):
        iso, ok = to_utc_iso(_first(t, *keys), offset)
        if not ok:
            unreadable.append(True)
        return iso

    return {
        "source": "hyper_backup",
        "id": str(t.get("task_id") if t.get("task_id") is not None else ""),
        "name": str(t.get("name") or t.get("task_id") or ""),
        "type": str(t.get("target_type") or ""),
        "state": str(t.get("state") or "") or None,
        "last_result": _hb_result(t.get("last_bkp_result")) if "last_bkp_result" in t else None,
        "last_result_raw": t.get("last_bkp_result") if isinstance(t.get("last_bkp_result"), str) else None,
        "last_run": when("last_bkp_time", "last_bkp_end_time"),
        "next_run": when("next_bkp_time"),
        "scheduled": None,
        "counts": None,
    }


def _parse_abb(t: dict, unreadable: list[bool]) -> dict:
    last = t.get("last_result") if isinstance(t.get("last_result"), dict) else {}

    def when(value):
        iso, ok = to_utc_iso(value, None)
        if not ok:
            unreadable.append(True)
        return iso

    status = t.get("status")
    status = status.lower() if isinstance(status, str) else None
    counts = None
    if last:
        counts = {k: last.get(k) for k in ("success_count", "warning_count", "error_count")
                  if isinstance(last.get(k), int) and not isinstance(last.get(k), bool)}
    return {
        "source": "active_backup",
        "id": str(t.get("task_id") if t.get("task_id") is not None else ""),
        "name": str(t.get("task_name") or t.get("task_id") or ""),
        "type": str(t.get("source_type") or t.get("backup_type") or ""),
        "state": status or None,
        "last_result": _abb_result(last.get("status")) if "status" in last else None,
        "last_result_raw": None,
        "last_run": when(last.get("time_start")),
        "next_run": when(t.get("next_trigger_time")),
        "scheduled": None if status is None else status != "unscheduled",
        "counts": counts or None,
    }


def parse_synobackup_data(*, collected_at: datetime, nas_time, hyper_backup, active_backup,
                          unavailable: list[str] | None = None) -> dict:
    """Turn the raw task lists into the snapshot. ``None`` means "not fetched"."""
    offset = nas_utc_offset(nas_time, collected_at)
    unreadable: list[bool] = []
    tasks: list[dict] = []
    for t in hyper_backup or []:
        if isinstance(t, dict):
            tasks.append(_parse_hb(t, offset, unreadable))
    for t in active_backup or []:
        if isinstance(t, dict):
            tasks.append(_parse_abb(t, unreadable))
    tasks.sort(key=lambda x: (x["source"], x["name"].lower()))
    labels = set(unavailable or [])
    if unreadable:
        labels.add("backup times")
    return {
        "collected_at": collected_at.astimezone(timezone.utc).isoformat(),
        "sources": {"hyper_backup": hyper_backup is not None, "active_backup": active_backup is not None},
        "tasks": tasks,
        "unavailable": sorted(labels),
        "totals": {
            "tasks": len(tasks),
            "failed": sum(1 for t in tasks if t["last_result"] == "failed"),
            "partial": sum(1 for t in tasks if t["last_result"] == "partial"),
        },
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class SynobackupIntegration(BaseIntegration):
    name = "synobackup"
    display_name = "Synology Backup"
    icon = "synology"
    color = "sky"
    description = "Monitor Synology Hyper Backup and Active Backup for Business task results."
    default_interval_seconds = 300

    config_fields = [
        ConfigField(key="host", label="Host", placeholder="synology.local"),
        ConfigField(key="port", label="Port", field_type="number", placeholder="5001", required=False, default=5001),
        ConfigField(key="username", label="Username", placeholder="admin"),
        ConfigField(key="password", label="Password", field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox", required=False, default=True),
        ConfigField(key="stale_overrides", label="Overdue limit per task (hours or off)",
                    field_type="textarea", required=False,
                    placeholder="Weekly *  = 200\nOffsite copy = off\n"
                                "# Matched against the task name, case-insensitive. First match wins."),
    ]

    alert_thresholds = (
        Threshold("task_stale_hours", "Task not run for", 48, "h", 0, zero_disables=True),
    )

    def _api(self) -> SynoBackupAPI:
        return SynoBackupAPI(
            host=self.config["host"],
            port=int(self.config.get("port", 5001) or 5001),
            username=self.config["username"],
            password=self.config["password"],
            verify_ssl=self.config.get("verify_ssl", True),
        )

    def validate_config(self, config: dict) -> list[str]:
        return backup_rules.parse_overrides((config or {}).get("stale_overrides"))[1]

    def _overrides(self):
        return backup_rules.parse_overrides(self.config.get("stale_overrides"))

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            rules, errors = self._overrides()
            data["override_errors"] = errors
            for t in data.get("tasks") or []:
                t["stale_override"] = backup_rules.override_label(
                    rules, str(t.get("name") or t.get("id") or ""))
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
        stale_h = self.threshold("task_stale_hours")
        rules, errors = self._overrides()
        if errors:
            alerts.append(backup_rules.errors_alert(errors))
        for t in data.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or t.get("id") or "")
            if not name:
                continue
            entity = f"{_LABEL.get(t.get('source'), 'Backup')}: {name}"
            result = t.get("last_result")
            if result == "failed":
                alerts.append(Alert(severity="critical", title="Backup task failed",
                                    detail=f"{entity}: last run failed", entity=entity))
            elif result == "partial":
                alerts.append(Alert(severity="warning", title="Backup task partially failed",
                                    detail=f"{entity}: last run finished with errors", entity=entity))
            last = parse_iso(t.get("last_run"))
            running = str(t.get("state") or "") in ("backingup", "running")
            limit = backup_rules.effective_limit(rules, stale_h, name)
            if (limit > 0 and collected is not None and last is not None
                    and t.get("scheduled") is not False and not running):
                age_h = (collected - last).total_seconds() / 3600
                if age_h > limit:
                    alerts.append(Alert(severity="warning", title="Backup task overdue",
                                        detail=f"{entity}: last run {age_h:.0f} h ago (limit {limit:g} h)",
                                        entity=entity))
        return alerts

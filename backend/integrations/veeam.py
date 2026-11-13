"""Veeam Backup & Replication integration – job states, repositories and sessions (REST API)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

DEFAULT_PORT = 9419
DEFAULT_API_VERSION = "1.1-rev1"
PAGE_LIMIT = 200
MAX_PAGES = 25
SESSION_LIMIT = 50

_RESULTS = ("success", "warning", "failed", "none")
_FRACTION = re.compile(r"(\.\d{6})\d+")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value) -> int | None:
    n = _num(value)
    return int(n) if n is not None and n.is_integer() else None


def _utc_iso(value) -> str | None:
    """Offset-carrying ISO 8601 string -> UTC ISO string; None otherwise.

    Veeam answers with .NET timestamps (up to 7 fractional digits). A value
    without an offset is ambiguous and therefore treated as missing."""
    if not isinstance(value, str) or not value:
        return None
    text = _FRACTION.sub(r"\1", value.strip().replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


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


def _result(value) -> str | None:
    """success | warning | failed | none, or None when absent/unknown."""
    if isinstance(value, str) and value.strip().lower() in _RESULTS:
        return value.strip().lower()
    return None


def _base_url(host: str, port) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("Veeam host is not configured")
    if "://" not in host:
        host = f"https://{host}"
    parts = urlsplit(host)
    if parts.port is not None:
        return f"{parts.scheme}://{parts.netloc}"
    p = _int(port) or DEFAULT_PORT
    return f"{parts.scheme}://{parts.netloc}:{p}"


# ── API Client ────────────────────────────────────────────────────────────────


class VeeamAPI:
    def __init__(self, host: str, username: str, password: str, port=DEFAULT_PORT,
                 api_version: str = DEFAULT_API_VERSION, verify_ssl: bool = True):
        self.base = _base_url(host, port)
        self.username = username
        self.password = password
        self.api_version = (api_version or DEFAULT_API_VERSION).strip()
        self.verify_ssl = verify_ssl
        self.unavailable: set[str] = set()

    def _headers(self, token: str | None = None) -> dict:
        h = {"x-api-version": self.api_version, "Accept": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    async def _login(self, client: httpx.AsyncClient) -> str:
        # Credentials travel in the form body, never in the URL.
        resp = await client.post(
            f"{self.base}/api/oauth2/token", headers=self._headers(),
            data={"grant_type": "password", "username": self.username, "password": self.password})
        if resp.status_code >= 400:
            raise ValueError(f"Veeam login failed (HTTP {resp.status_code})")
        token = resp.json().get("access_token") if resp.content else None
        if not isinstance(token, str) or not token:
            raise ValueError("Veeam login returned no access token")
        return token

    async def _logout(self, client: httpx.AsyncClient, token: str) -> None:
        try:
            await client.post(f"{self.base}/api/oauth2/logout", headers=self._headers(token))
        except Exception:
            log.debug("Veeam logout failed", exc_info=True)

    async def _get(self, client: httpx.AsyncClient, token: str, path: str, params: dict | None = None) -> dict:
        resp = await client.get(f"{self.base}{path}", headers=self._headers(token), params=params)
        if resp.status_code in (401, 403):
            raise ValueError(f"Veeam denied access to {path} (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise ValueError(f"Veeam request {path} failed (HTTP {resp.status_code})")
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError(f"Veeam request {path} returned an unexpected answer")
        return body

    async def _get_all(self, client: httpx.AsyncClient, token: str, path: str) -> list:
        items: list = []
        skip = 0
        for _ in range(MAX_PAGES):
            body = await self._get(client, token, path, {"skip": skip, "limit": PAGE_LIMIT})
            data = body.get("data")
            if not isinstance(data, list):
                raise ValueError(f"Veeam request {path} returned an unexpected answer")
            items.extend(data)
            total = _int((body.get("pagination") or {}).get("total"))
            if not data or total is None or len(items) >= total:
                return items
            skip += len(data)
        raise ValueError(f"Veeam request {path} has too many pages")

    async def _optional(self, label: str, coro):
        try:
            return await coro
        except Exception as exc:
            log.warning("Veeam %s unavailable: %s", label, exc)
            self.unavailable.add(label)
            return None

    async def fetch_all(self) -> dict:
        self.unavailable = set()
        collected_at = datetime.now(timezone.utc)
        async with safe_async_client(verify=self.verify_ssl, timeout=30.0) as client:
            token = await self._login(client)
            try:
                # Core request: without job states there is nothing to monitor.
                jobs = await self._get_all(client, token, "/api/v1/jobs/states")
                server = await self._optional("server info", self._get(client, token, "/api/v1/serverInfo"))
                repos = await self._optional("repositories", self._get_all(
                    client, token, "/api/v1/backupInfrastructure/repositories/states"))
                sessions = await self._optional("sessions", self._get(
                    client, token, "/api/v1/sessions",
                    {"limit": SESSION_LIMIT, "orderColumn": "CreationTime", "orderAsc": "false"}))
            finally:
                await self._logout(client, token)
        session_list = sessions.get("data") if isinstance(sessions, dict) else None
        if sessions is not None and not isinstance(session_list, list):
            # An answer without a session list is "could not read", not "no sessions".
            self.unavailable.add("sessions")
        return parse_veeam_data(
            collected_at=collected_at, server=server, jobs=jobs, repositories=repos,
            sessions=session_list if isinstance(session_list, list) else None,
            unavailable=sorted(self.unavailable))

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0) as client:
                token = await self._login(client)
                await self._logout(client, token)
            return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def _parse_job(j: dict) -> dict:
    return {
        "id": str(j.get("id") or ""),
        "name": str(j.get("name") or ""),
        "description": str(j.get("description") or ""),
        "type": str(j.get("type") or ""),
        "workload": str(j.get("workload") or ""),
        "status": str(j.get("status") or "").lower() or None,
        "last_result": _result(j.get("lastResult")),
        "last_run": _utc_iso(j.get("lastRun")),
        "next_run": _utc_iso(j.get("nextRun")),
        "repository_name": str(j.get("repositoryName") or ""),
        "objects_count": _int(j.get("objectsCount")),
    }


def _parse_repo(r: dict) -> dict:
    cap, free, used = _num(r.get("capacityGB")), _num(r.get("freeGB")), _num(r.get("usedSpaceGB"))
    if used is None and cap is not None and free is not None:
        used = cap - free
    pct = round(used / cap * 100, 1) if cap and cap > 0 and used is not None else None
    return {
        "id": str(r.get("id") or ""),
        "name": str(r.get("name") or ""),
        "type": str(r.get("type") or ""),
        "host": str(r.get("hostName") or ""),
        "path": str(r.get("path") or ""),
        "capacity_gb": cap, "free_gb": free, "used_gb": used, "pct": pct,
    }


def _parse_session(s: dict) -> dict:
    res = s.get("result") if isinstance(s.get("result"), dict) else {}
    return {
        "id": str(s.get("id") or ""),
        "name": str(s.get("name") or ""),
        "type": str(s.get("sessionType") or ""),
        "state": str(s.get("state") or ""),
        "result": _result(res.get("result")),
        "message": str(res.get("message") or ""),
        "created_at": _utc_iso(s.get("creationTime")),
        "ended_at": _utc_iso(s.get("endTime")),
    }


def parse_veeam_data(*, collected_at: datetime, server, jobs, repositories, sessions,
                     unavailable: list[str] | None = None) -> dict:
    """Turn the raw Veeam answers into the snapshot. ``None`` means "not fetched"."""
    srv = None
    if isinstance(server, dict):
        patches = server.get("patches")
        srv = {
            "name": str(server.get("name") or ""),
            "build": str(server.get("buildVersion") or ""),
            "vbr_id": str(server.get("vbrId") or ""),
            "patches": [str(p) for p in patches] if isinstance(patches, list) else [],
        }
    parsed_jobs = sorted((_parse_job(j) for j in jobs or [] if isinstance(j, dict)), key=lambda j: j["name"].lower())
    parsed_repos = None
    if isinstance(repositories, list):
        parsed_repos = sorted((_parse_repo(r) for r in repositories if isinstance(r, dict)),
                              key=lambda r: r["name"].lower())
    parsed_sessions = None
    if isinstance(sessions, list):
        parsed_sessions = [_parse_session(s) for s in sessions if isinstance(s, dict)][:SESSION_LIMIT]
    active = [j for j in parsed_jobs if j["status"] != "disabled"]
    return {
        "collected_at": collected_at.astimezone(timezone.utc).isoformat(),
        "server": srv,
        "jobs": parsed_jobs,
        "repositories": parsed_repos,
        "sessions": parsed_sessions,
        "unavailable": sorted(unavailable or []),
        "totals": {
            "jobs": len(parsed_jobs),
            "jobs_failed": sum(1 for j in active if j["last_result"] == "failed"),
            "jobs_warning": sum(1 for j in active if j["last_result"] == "warning"),
            "jobs_disabled": len(parsed_jobs) - len(active),
        },
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class VeeamIntegration(BaseIntegration):
    name = "veeam"
    display_name = "Veeam Backup & Replication"
    icon = "veeam"
    color = "green"
    description = "Monitor Veeam Backup & Replication jobs, repositories and sessions via the REST API."
    default_interval_seconds = 300

    config_fields = [
        ConfigField(key="host", label="Backup server", placeholder="vbr.example.com"),
        ConfigField(key="port", label="REST API port", field_type="number", placeholder="9419",
                    required=False, default=DEFAULT_PORT),
        ConfigField(key="username", label="Username", placeholder="DOMAIN\\svc-nodeglow"),
        ConfigField(key="password", label="Password", field_type="password", encrypted=True),
        ConfigField(key="api_version", label="API version", placeholder=DEFAULT_API_VERSION,
                    required=False, default=DEFAULT_API_VERSION),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox", required=False, default=True),
    ]

    alert_thresholds = (
        Threshold("repo_warn_pct", "Repository usage warning", 85, "%", 1, 100),
        Threshold("repo_crit_pct", "Repository usage critical", 95, "%", 1, 100),
        Threshold("job_stale_hours", "Job not run for", 48, "h", 0, zero_disables=True),
    )

    def _api(self) -> VeeamAPI:
        return VeeamAPI(
            host=self.config["host"],
            port=self.config.get("port") or DEFAULT_PORT,
            username=self.config["username"],
            password=self.config["password"],
            api_version=self.config.get("api_version") or DEFAULT_API_VERSION,
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
        stale_h = self.threshold("job_stale_hours")

        for job in data.get("jobs") or []:
            if not isinstance(job, dict) or job.get("status") == "disabled":
                continue  # a disabled job is a deliberate state, not an incident
            name = str(job.get("name") or job.get("id") or "")
            if not name:
                continue
            result = job.get("last_result")
            if result == "failed":
                alerts.append(Alert(severity="critical", title="Backup job failed",
                                    detail=f"{name}: last run failed", entity=name))
            elif result == "warning":
                alerts.append(Alert(severity="warning", title="Backup job finished with warnings",
                                    detail=f"{name}: last run finished with warnings", entity=name))
            last = parse_iso(job.get("last_run"))
            if stale_h > 0 and collected is not None and last is not None and job.get("status") != "running":
                age_h = (collected - last).total_seconds() / 3600
                if age_h > stale_h:
                    alerts.append(Alert(severity="warning", title="Backup job overdue",
                                        detail=f"{name}: last run {age_h:.0f} h ago (limit {stale_h:g} h)",
                                        entity=name))

        warn, crit = self.threshold("repo_warn_pct"), self.threshold("repo_crit_pct")
        for repo in data.get("repositories") or []:
            if not isinstance(repo, dict):
                continue
            pct = repo.get("pct")
            name = str(repo.get("name") or repo.get("id") or "")
            if not isinstance(pct, (int, float)) or not name:
                continue
            if pct >= crit:
                alerts.append(Alert(severity="critical", title="Repository nearly full",
                                    detail=f"{name}: {pct}% used", entity=name))
            elif pct >= warn:
                alerts.append(Alert(severity="warning", title="Repository filling up",
                                    detail=f"{name}: {pct}% used", entity=name))
        return alerts

"""VMware vSphere integration – vCenter hosts, VMs, datastores, clusters and appliance health.

Uses the vSphere Automation REST API (``/api/...``, vCenter 7.0 Update 2 and
later). A session is created with HTTP basic auth, used as the
``vmware-api-session-id`` header and always deleted again, also on errors.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

# GET /api/vcenter/vm refuses to answer when more VMs match than this.
VM_LIST_LIMIT = 4000

# Appliance health endpoints (vCenter Server Appliance only), by snapshot key.
APPLIANCE_HEALTH = {
    "system": "/api/appliance/health/system",
    "storage": "/api/appliance/health/storage",
    "database_storage": "/api/appliance/health/database-storage",
    "load": "/api/appliance/health/load",
    "memory": "/api/appliance/health/mem",
    "swap": "/api/appliance/health/swap",
    "software_packages": "/api/appliance/health/software-packages",
    "applmgmt": "/api/appliance/health/applmgmt",
}

_HEALTHY = "green"
# "gray": vCenter has no data for this health check. Unknown, not unhealthy.
_UNKNOWN = "gray"
_HOST_BAD = {"DISCONNECTED", "NOT_RESPONDING"}


class RestError(Exception):
    """A request failed; carries the HTTP status and the API's error_type if any."""

    def __init__(self, message: str, status: int | None = None, error_type: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


# ── Pure parsers ──────────────────────────────────────────────────────────────


def _num(value) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _s(value) -> str | None:
    return str(value) if value not in (None, "") else None


def parse_version(body) -> dict | None:
    if not isinstance(body, dict):
        return None
    return {
        "version": _s(body.get("version")),
        "build": _s(body.get("build")),
        "product": _s(body.get("product")),
        "summary": _s(body.get("summary")),
        "release_date": _s(body.get("releasedate")),
    }


def parse_hosts(items) -> list[dict]:
    out = []
    for h in items if isinstance(items, list) else []:
        if not isinstance(h, dict):
            continue
        out.append({
            "id": str(h.get("host") or ""),
            "name": str(h.get("name") or h.get("host") or ""),
            "connection_state": _s(h.get("connection_state")),
            "power_state": _s(h.get("power_state")),
        })
    out.sort(key=lambda h: h["name"].lower())
    return out


def parse_vms(items) -> list[dict]:
    out = []
    for v in items if isinstance(items, list) else []:
        if not isinstance(v, dict):
            continue
        out.append({
            "id": str(v.get("vm") or ""),
            "name": str(v.get("name") or v.get("vm") or ""),
            "power_state": _s(v.get("power_state")),
            "cpu_count": _num(v.get("cpu_count")),
            "memory_mib": _num(v.get("memory_size_MiB")),
        })
    out.sort(key=lambda v: v["name"].lower())
    return out


def parse_datastores(items) -> list[dict]:
    """Datastores with usage; ``pct`` is None unless capacity and free space are known."""
    out = []
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict):
            continue
        cap, free = _num(d.get("capacity")), _num(d.get("free_space"))
        used = pct = None
        if cap is not None and free is not None and cap > 0:
            used = max(cap - free, 0)
            pct = round(used / cap * 100, 1)
        out.append({
            "id": str(d.get("datastore") or ""),
            "name": str(d.get("name") or d.get("datastore") or ""),
            "type": _s(d.get("type")),
            "capacity": cap,
            "free_space": free,
            "used": used,
            "pct": pct,
        })
    out.sort(key=lambda d: d["name"].lower())
    return out


def parse_clusters(items) -> list[dict]:
    out = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": str(c.get("cluster") or ""),
            "name": str(c.get("name") or c.get("cluster") or ""),
            "ha_enabled": c.get("ha_enabled") if isinstance(c.get("ha_enabled"), bool) else None,
            "drs_enabled": c.get("drs_enabled") if isinstance(c.get("drs_enabled"), bool) else None,
        })
    out.sort(key=lambda c: c["name"].lower())
    return out


def parse_health_value(body) -> str | None:
    """The health endpoints answer with a bare JSON string ("green")."""
    if isinstance(body, str) and body.strip():
        return body.strip().lower()
    return None


def build_snapshot(now: datetime, version, hosts, vms, vm_list_error, datastores, clusters,
                   appliance_health: dict, unavailable: list[str]) -> dict:
    vms_known = vms is not None
    return {
        "collected_at": now.astimezone(timezone.utc).isoformat(),
        "version": version,
        "hosts": hosts,
        "vms": vms,
        "vm_list_error": vm_list_error,
        "datastores": datastores,
        "clusters": clusters,
        "appliance_health": appliance_health,
        "totals": {
            "hosts_total": len(hosts),
            "hosts_connected": sum(1 for h in hosts if h["connection_state"] == "CONNECTED"),
            "vms_total": len(vms) if vms_known else None,
            "vms_powered_on": sum(1 for v in vms if v["power_state"] == "POWERED_ON") if vms_known else None,
            "datastores_total": len(datastores),
            "clusters_total": len(clusters) if clusters is not None else None,
        },
        "unavailable": unavailable,
    }


# ── API client ────────────────────────────────────────────────────────────────


def normalize_base(host: str) -> str:
    base = (host or "").strip().rstrip("/")
    if base and "://" not in base:
        base = "https://" + base
    return base


class VsphereAPI:
    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = True):
        self.base = normalize_base(host)
        self.username = username or ""
        self.password = password or ""
        self.verify_ssl = verify_ssl

    async def create_session(self, client: httpx.AsyncClient) -> str:
        if not self.base or not self.username or not self.password:
            raise ValueError("Host, username and password are required")
        try:
            resp = await client.post(f"{self.base}/api/session", auth=(self.username, self.password))
        except httpx.HTTPError as exc:
            raise ValueError(f"vCenter session request failed: {type(exc).__name__}") from None
        if resp.status_code in (401, 403):
            raise ValueError("vCenter login failed: invalid credentials or insufficient privileges")
        if resp.status_code not in (200, 201):
            raise ValueError(f"vCenter session request failed: HTTP {resp.status_code}")
        try:
            token = resp.json()
        except ValueError:
            token = None
        if not isinstance(token, str) or not token:
            raise ValueError("vCenter session request failed: no session token in response")
        return token

    async def delete_session(self, client: httpx.AsyncClient, token: str) -> None:
        try:
            await client.delete(f"{self.base}/api/session", headers={"vmware-api-session-id": token})
        except Exception as exc:  # the session then expires on its own
            log.warning("vCenter session could not be deleted: %s", type(exc).__name__)

    async def get(self, client: httpx.AsyncClient, token: str, path: str, params: dict | None = None):
        try:
            resp = await client.get(f"{self.base}{path}", params=params,
                                    headers={"vmware-api-session-id": token, "Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise RestError(type(exc).__name__) from None
        if resp.status_code != 200:
            error_type = None
            try:
                body = resp.json()
                if isinstance(body, dict):
                    error_type = _s(body.get("error_type"))
            except ValueError:
                pass
            raise RestError(f"HTTP {resp.status_code}", resp.status_code, error_type)
        try:
            return resp.json()
        except ValueError:
            raise RestError("invalid JSON") from None

    async def list_vms(self, client: httpx.AsyncClient, token: str,
                       hosts: list[dict]) -> tuple[list[dict] | None, str | None]:
        """All VMs and an explanatory note when the list was not complete.

        vCenter fails the whole call when more than ~4000 VMs match. That is
        retried host by host; a result that cannot be completed is returned as
        (None, reason) so a partial list is never presented as the full one.
        """
        try:
            return parse_vms(await self.get(client, token, "/api/vcenter/vm")), None
        except RestError as exc:
            if exc.status not in (400, 413, 500) and exc.error_type != "UNABLE_TO_ALLOCATE_RESOURCE":
                return None, f"VM list failed ({exc})"
            first = f"VM list rejected by vCenter ({exc}), probably more than {VM_LIST_LIMIT} VMs"
        if not any(h["id"] for h in hosts):
            return None, first
        merged: dict[str, dict] = {}
        for h in hosts:
            if not h["id"]:
                continue
            try:
                for vm in parse_vms(await self.get(client, token, "/api/vcenter/vm", {"hosts": h["id"]})):
                    merged[vm["id"]] = vm
            except RestError as exc:
                return None, f"{first}; per-host retry failed on {h['name']} ({exc})"
        # VMs not placed on any host are invisible to a per-host listing.
        return (sorted(merged.values(), key=lambda v: v["name"].lower()),
                f"{first}; listed per host, unplaced VMs may be missing")

    async def fetch_all(self) -> dict:
        now = datetime.now(timezone.utc)
        unavailable: set[str] = set()
        async with safe_async_client(verify=self.verify_ssl, timeout=30.0) as client:
            token = await self.create_session(client)
            try:
                try:
                    hosts = parse_hosts(await self.get(client, token, "/api/vcenter/host"))
                    datastores = parse_datastores(await self.get(client, token, "/api/vcenter/datastore"))
                except RestError as exc:
                    raise ValueError(f"vCenter request failed: {exc}") from None

                version = None
                try:
                    version = parse_version(await self.get(client, token, "/api/appliance/system/version"))
                except RestError as exc:
                    unavailable.add(f"vCenter version ({exc})")

                clusters = None
                try:
                    clusters = parse_clusters(await self.get(client, token, "/api/vcenter/cluster"))
                except RestError as exc:
                    unavailable.add(f"clusters ({exc})")

                vms, vm_note = await self.list_vms(client, token, hosts)
                if vm_note:
                    unavailable.add("VMs" if vms is None else "VMs (incomplete)")

                health: dict[str, str | None] = {}
                failures: dict[str, str] = {}
                for key, path in APPLIANCE_HEALTH.items():
                    try:
                        health[key] = parse_health_value(await self.get(client, token, path))
                        if health[key] is None:
                            failures[key] = "unexpected response"
                        elif health[key] == _UNKNOWN:
                            failures[key] = "unknown (gray)"
                    except RestError as exc:
                        health[key] = None
                        failures[key] = str(exc)
                if failures and len(failures) == len(APPLIANCE_HEALTH):
                    # Typically a Windows vCenter or a missing privilege: one line, not eight.
                    unavailable.add(f"appliance health ({next(iter(failures.values()))})")
                else:
                    for key, why in failures.items():
                        unavailable.add(f"appliance health: {key} ({why})")
            finally:
                await self.delete_session(client, token)
        return build_snapshot(now, version, hosts, vms, vm_note, datastores, clusters, health,
                              sorted(unavailable))


# ── Integration Plugin ────────────────────────────────────────────────────────


class VsphereIntegration(BaseIntegration):
    name = "vsphere"
    display_name = "VMware vSphere"
    icon = "vmware"
    color = "emerald"
    description = ("Monitor a VMware vCenter through the vSphere Automation REST API: hosts, VMs, "
                   "datastores, clusters and appliance health.")
    default_interval_seconds = 120

    config_fields = [
        ConfigField(key="host", label="vCenter URL", field_type="url", placeholder="https://vcenter.local"),
        ConfigField(key="username", label="Username", field_type="text",
                    placeholder="Read-only user, e.g. monitoring@vsphere.local"),
        ConfigField(key="password", label="Password", field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox", required=False, default=True),
    ]

    alert_thresholds = (
        Threshold("datastore_warn_pct", "Datastore usage warning", 80, "%", 1, 100),
        Threshold("datastore_crit_pct", "Datastore usage critical", 90, "%", 1, 100),
    )

    def _api(self) -> VsphereAPI:
        return VsphereAPI(
            host=self.config.get("host", ""),
            username=self.config.get("username", ""),
            password=self.config.get("password", ""),
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            return CollectorResult(success=True, data=await self._api().fetch_all())
        except ValueError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:  # never echo str(exc): it may carry request details
            log.warning("vSphere collection failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"Collection failed: {type(exc).__name__}")

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts

        for h in data.get("hosts") or []:
            if not isinstance(h, dict):
                continue
            state = h.get("connection_state")
            name = h.get("name") or h.get("id") or "?"
            if state in _HOST_BAD:
                label = "not responding" if state == "NOT_RESPONDING" else "disconnected"
                alerts.append(Alert(severity="critical", title=f"ESXi host {name} {label}",
                                    detail=f"vCenter reports connection state {state}",
                                    entity=f"host: {name}", host=name))
            elif state and state != "CONNECTED":
                alerts.append(Alert(severity="warning", title=f"ESXi host {name} in state {state}",
                                    detail=f"Unexpected connection state {state}",
                                    entity=f"host: {name}", host=name))

        warn, crit = self.threshold("datastore_warn_pct"), self.threshold("datastore_crit_pct")
        for d in data.get("datastores") or []:
            if not isinstance(d, dict):
                continue
            cap, free = d.get("capacity"), d.get("free_space")
            if not isinstance(cap, (int, float)) or not isinstance(free, (int, float)) or cap <= 0:
                continue
            pct = max(cap - free, 0) / cap * 100
            name = d.get("name") or d.get("id") or "?"
            if pct >= crit:
                sev, title = "critical", f"Datastore {name} nearly full"
            elif pct >= warn:
                sev, title = "warning", f"Datastore {name} filling up"
            else:
                continue
            alerts.append(Alert(severity=sev, title=title, detail=f"{name}: {pct:.1f}% used",
                                entity=f"datastore: {name}"))

        health = data.get("appliance_health")
        if isinstance(health, dict):
            for key, value in sorted(health.items()):
                if not isinstance(value, str) or not value or value in (_HEALTHY, _UNKNOWN):
                    continue  # missing or unknown is reported via unavailable, not alerted
                sev = "critical" if value == "red" else "warning"
                # The colour stays out of the title (part of the incident key):
                # yellow -> orange -> red is one problem getting worse.
                alerts.append(Alert(severity=sev, title=f"vCenter appliance health {key} degraded",
                                    detail=f"Appliance health '{key}' is {value} (expected green)",
                                    entity=f"appliance: {key}"))
        return alerts

    async def health_check(self) -> bool:
        api = self._api()
        try:
            async with safe_async_client(verify=api.verify_ssl, timeout=10.0) as client:
                token = await api.create_session(client)
                await api.delete_session(client, token)
            return True
        except Exception:
            return False

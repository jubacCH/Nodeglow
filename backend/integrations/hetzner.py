"""Hetzner Cloud integration – servers, volumes, load balancers and IPs.

Read-only: a Read-only project token is enough. The servers list is the core
request; volumes, load balancers and IPs are optional parts that are listed in
``data["unavailable"]`` when they cannot be fetched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

API_BASE = "https://api.hetzner.cloud/v1"
PER_PAGE = 50
MAX_PAGES = 100

# Server states that are expected to pass by themselves.
TRANSIENT_STATES = frozenset({
    "initializing", "starting", "stopping", "migrating", "rebuilding", "deleting",
})


class HetznerError(Exception):
    """A request failed; the message is safe to show (never holds the token)."""


# ── Parsers ───────────────────────────────────────────────────────────────────


def _num(value: Any) -> float | int | None:
    """A real number, else None (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def traffic_pct(outgoing: Any, included: Any) -> float | None:
    """Outgoing traffic as percent of the included quota; None without both."""
    out, inc = _num(outgoing), _num(included)
    if out is None or inc is None or inc <= 0:
        return None
    return round(out / inc * 100, 1)


def parse_server(raw: dict) -> dict:
    net = _dict(raw.get("public_net"))
    dc = _dict(raw.get("datacenter"))
    location = _dict(raw.get("location")) or _dict(dc.get("location"))
    stype = _dict(raw.get("server_type"))
    prot = _dict(raw.get("protection"))
    outgoing = _num(raw.get("outgoing_traffic"))
    included = _num(raw.get("included_traffic"))
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "status": raw.get("status") or "unknown",
        "server_type": stype.get("name") or "",
        "cores": _num(stype.get("cores")),
        "memory_gb": _num(stype.get("memory")),
        "datacenter": dc.get("name") or "",
        "location": location.get("name") or "",
        "ipv4": _dict(net.get("ipv4")).get("ip") or "",
        "ipv6": _dict(net.get("ipv6")).get("ip") or "",
        "ingoing_traffic": _num(raw.get("ingoing_traffic")),
        "outgoing_traffic": outgoing,
        "included_traffic": included,
        "traffic_pct": traffic_pct(outgoing, included),
        "protect_delete": bool(prot["delete"]) if "delete" in prot else None,
        "protect_rebuild": bool(prot["rebuild"]) if "rebuild" in prot else None,
        "locked": bool(raw["locked"]) if "locked" in raw else None,
        "created": raw.get("created") or "",
    }


def parse_volume(raw: dict) -> dict:
    server = raw.get("server")
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "status": raw.get("status") or "unknown",
        "size_gb": _num(raw.get("size")),
        "location": _dict(raw.get("location")).get("name") or "",
        # None: the API did not say; False: explicitly not attached.
        "attached": (server is not None) if "server" in raw else None,
        "server_id": server if isinstance(server, int) else None,
    }


def _target_health(statuses: list) -> bool | None:
    """True healthy, False when any port is unhealthy, None when unknown."""
    values = [_dict(s).get("status") for s in statuses]
    if any(v == "unhealthy" for v in values):
        return False
    if values and all(v == "healthy" for v in values):
        return True
    return None


def _target_label(target: dict) -> str:
    ttype = target.get("type") or ""
    if ttype == "server":
        sid = _dict(target.get("server")).get("id")
        return f"server {sid}" if sid is not None else "server"
    if ttype == "ip":
        return f"ip {_dict(target.get('ip')).get('ip') or '?'}"
    if ttype == "label_selector":
        return f"label {_dict(target.get('label_selector')).get('selector') or '?'}"
    return ttype or "target"


def _leaf_targets(target: dict) -> list[dict]:
    """A label selector holds its resolved targets in ``targets``."""
    if target.get("type") == "label_selector":
        return [t for t in _list(target.get("targets")) if isinstance(t, dict)]
    return [target]


def parse_load_balancer(raw: dict) -> dict:
    targets: list[dict] = []
    for target in _list(raw.get("targets")):
        if not isinstance(target, dict):
            continue
        for leaf in _leaf_targets(target):
            targets.append({
                "label": _target_label(leaf),
                "type": leaf.get("type") or "",
                "healthy": _target_health(_list(leaf.get("health_status"))),
                "unhealthy_ports": sorted(
                    p for p in (
                        _dict(s).get("listening_port") for s in _list(leaf.get("health_status"))
                        if _dict(s).get("status") == "unhealthy"
                    ) if isinstance(p, int)
                ),
            })
    net = _dict(raw.get("public_net"))
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "type": _dict(raw.get("load_balancer_type")).get("name") or "",
        "location": _dict(raw.get("location")).get("name") or "",
        "ipv4": _dict(net.get("ipv4")).get("ip") or "",
        "targets": targets,
        "healthy_targets": sum(1 for t in targets if t["healthy"] is True),
        "unhealthy_targets": sum(1 for t in targets if t["healthy"] is False),
        "service_count": len(_list(raw.get("services"))),
    }


def parse_floating_ip(raw: dict) -> dict:
    server = raw.get("server")
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "ip": raw.get("ip") or "",
        "type": raw.get("type") or "",
        "location": _dict(raw.get("home_location")).get("name") or "",
        "assigned": (server is not None) if "server" in raw else None,
    }


def parse_primary_ip(raw: dict) -> dict:
    assignee = raw.get("assignee_id")
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "ip": raw.get("ip") or "",
        "type": raw.get("type") or "",
        "location": _dict(raw.get("datacenter")).get("name") or "",
        "assigned": (assignee is not None) if "assignee_id" in raw else None,
    }


# ── API client ────────────────────────────────────────────────────────────────


class HetznerAPI:
    def __init__(self, api_token: str):
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> dict:
        try:
            resp = await client.get(f"{API_BASE}{path}", headers=self._headers, params=params)
        except httpx.HTTPError as exc:
            raise HetznerError(f"Request to Hetzner Cloud failed ({type(exc).__name__})") from None
        if resp.status_code == 429:
            reset = resp.headers.get("RateLimit-Reset")
            hint = f"; quota resets at unix time {reset}" if reset else ""
            raise HetznerError(f"Hetzner Cloud API rate limit exceeded (HTTP 429){hint}")
        if resp.status_code in (401, 403):
            raise HetznerError(f"Hetzner Cloud rejected the API token (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise HetznerError(f"Hetzner Cloud API error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise HetznerError("Hetzner Cloud returned a response that is not JSON") from None
        if not isinstance(body, dict):
            raise HetznerError("Hetzner Cloud returned an unexpected response")
        return body

    async def list_all(self, client: httpx.AsyncClient, path: str, key: str) -> list[dict]:
        """Every item of a collection, following ``meta.pagination``."""
        items: list[dict] = []
        page = 1
        for _ in range(MAX_PAGES):
            body = await self._get(client, path, {"page": page, "per_page": PER_PAGE})
            chunk = body.get(key)
            if not isinstance(chunk, list):
                raise HetznerError(f"Hetzner Cloud response for {path} has no '{key}' list")
            items.extend(i for i in chunk if isinstance(i, dict))
            nxt = _dict(_dict(body.get("meta")).get("pagination")).get("next_page")
            if not isinstance(nxt, int) or isinstance(nxt, bool):
                return items
            if nxt <= page:
                raise HetznerError(f"Hetzner Cloud pagination for {path} does not advance")
            page = nxt
        raise HetznerError(f"Hetzner Cloud list {path} exceeds {MAX_PAGES} pages")

    async def fetch_all(self) -> dict:
        unavailable: set[str] = set()
        async with safe_async_client(timeout=30.0) as client:
            # Core request: a failure here fails the whole collection.
            servers = await self.list_all(client, "/servers", "servers")

            async def optional(path: str, key: str, label: str, parse) -> list[dict]:
                try:
                    return [parse(i) for i in await self.list_all(client, path, key)]
                except HetznerError as exc:
                    log.warning("Hetzner %s unavailable: %s", label, exc)
                    unavailable.add(label)
                    return []

            volumes = await optional("/volumes", "volumes", "volumes", parse_volume)
            lbs = await optional("/load_balancers", "load_balancers", "load balancers",
                                 parse_load_balancer)
            fips = await optional("/floating_ips", "floating_ips", "floating IPs", parse_floating_ip)
            pips = await optional("/primary_ips", "primary_ips", "primary IPs", parse_primary_ip)

        parsed = [parse_server(s) for s in servers]
        return {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "servers": parsed,
            "server_count": len(parsed),
            "volumes": volumes,
            "load_balancers": lbs,
            "floating_ips": fips,
            "primary_ips": pips,
            "unavailable": sorted(unavailable),
        }


# ── Integration plugin ────────────────────────────────────────────────────────


class HetznerIntegration(BaseIntegration):
    name = "hetzner"
    display_name = "Hetzner Cloud"
    icon = "hetzner"
    color = "red"
    description = "Monitor Hetzner Cloud servers, traffic quota, volumes, load balancers and IPs."
    # A public SaaS API: the core collects it, never a probe.
    network_scope = "cloud"
    default_interval_seconds = 300

    config_fields = [
        ConfigField(
            key="api_token",
            label="API Token",
            field_type="password",
            placeholder="Read-only project token (Security > API tokens)",
            encrypted=True,
            required=True,
        ),
    ]

    alert_thresholds = (
        Threshold("traffic_warn_pct", "Outgoing traffic warning", 80, "%", 1, 1000),
        Threshold("traffic_crit_pct", "Outgoing traffic critical", 95, "%", 1, 1000),
    )

    async def collect(self) -> CollectorResult:
        token = self.config.get("api_token")
        if not token:
            return CollectorResult(success=False, error="No API token configured")
        try:
            return CollectorResult(success=True, data=await HetznerAPI(token).fetch_all())
        except HetznerError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:
            log.warning("Hetzner collection failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"Hetzner collection failed ({type(exc).__name__})")

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        warn = self.threshold("traffic_warn_pct")
        crit = self.threshold("traffic_crit_pct")

        for srv in _list(data.get("servers")):
            if not isinstance(srv, dict):
                continue
            name = srv.get("name") or str(srv.get("id") or "?")
            entity = f"server:{srv.get('id') or name}"
            status = srv.get("status")
            # "unknown" is also what parse_server stores when the API sent no
            # status: missing data is not an outage.
            if isinstance(status, str) and status and status not in ("running", "unknown"):
                if status == "off":
                    detail = "The server is switched off."
                elif status in TRANSIENT_STATES:
                    detail = f"The server is {status} and has not reached 'running'."
                else:
                    detail = f"The server reports status '{status}'."
                alerts.append(Alert(
                    # The state is in the detail: a title naming it would open a
                    # new incident on every change (stopping -> off -> starting).
                    severity="warning", title=f"Server {name} is not running",
                    detail=detail, entity=entity, host=srv.get("name") or "",
                ))

            pct = traffic_pct(srv.get("outgoing_traffic"), srv.get("included_traffic"))
            if pct is not None and pct >= warn:
                severity = "critical" if pct >= crit else "warning"
                alerts.append(Alert(
                    severity=severity,
                    # One title for both severities, so the incident escalates.
                    title=f"Server {name} is using its outgoing traffic quota",
                    detail=f"{pct}% of the included traffic quota used.",
                    entity=f"traffic:{srv.get('id') or name}", host=srv.get("name") or "",
                ))

        for lb in _list(data.get("load_balancers")):
            if not isinstance(lb, dict):
                continue
            lname = lb.get("name") or str(lb.get("id") or "?")
            targets = [t for t in _list(lb.get("targets")) if isinstance(t, dict)]
            any_healthy = any(t.get("healthy") is True for t in targets)
            for t in targets:
                if t.get("healthy") is not False:
                    continue  # healthy or unknown: unknown is not an alert
                ports = ", ".join(str(p) for p in _list(t.get("unhealthy_ports")))
                alerts.append(Alert(
                    severity="warning" if any_healthy else "critical",
                    title=f"Load balancer {lname} has an unhealthy target",
                    detail=f"Target {t.get('label') or '?'} is unhealthy"
                           + (f" on port {ports}." if ports else "."),
                    entity=f"lb:{lb.get('id') or lname}:{t.get('label') or '?'}",
                ))
        return alerts

    def get_dashboard_summary(self, data: dict) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            return None
        servers = [s for s in data["servers"] if isinstance(s, dict)]
        missing = set(_list(data.get("unavailable")))

        def count(key: str, label: str) -> int | None:
            # A part that could not be fetched has no count, not zero.
            return None if label in missing else len(_list(data.get(key)))

        return {
            "servers": len(servers),
            "running": sum(1 for s in servers if s.get("status") == "running"),
            "load_balancers": count("load_balancers", "load balancers"),
            "volumes": count("volumes", "volumes"),
        }

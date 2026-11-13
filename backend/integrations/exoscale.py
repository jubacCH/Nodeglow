"""Exoscale integration – compute instances, SKS, DBaaS and network load balancers.

Uses the Exoscale API v2 of one zone, signed with ``EXO2-HMAC-SHA256``. The
instance list is the core request; SKS clusters, DBaaS services and load
balancers are optional parts (an API key restricted by role may lack one of
them) and are listed in ``data["unavailable"]`` when they cannot be fetched.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField

log = logging.getLogger(__name__)

ZONES = ("ch-gva-2", "ch-dk-2", "de-fra-1", "de-muc-1", "at-vie-1", "at-vie-2", "bg-sof-1")
DEFAULT_ZONE = "ch-gva-2"
SIGNATURE_VALIDITY_S = 600

RUNNING = "running"
# NLB health check results that count as healthy / unhealthy; anything else is unknown.
HEALTHY = frozenset({"success", "healthy", "ok"})
UNHEALTHY = frozenset({"failure", "unhealthy", "failed"})


class ExoscaleError(Exception):
    """A request failed; the message is safe to show (never holds key or secret)."""


# ── Request signing ───────────────────────────────────────────────────────────


def signature_message(method: str, path: str, body: str, query: dict[str, str], expires: int) -> tuple[str, str]:
    """The message Exoscale expects to be signed, and the ``signed-query-args`` value.

    Five segments joined by a newline: ``METHOD path``, the request body, the
    values of the (single-valued) query parameters sorted by name and
    concatenated without separator, the signed headers (none, so an empty
    line) and the expiry as unix seconds.
    """
    names = sorted(query)
    values = "".join(str(query[n]) for n in names)
    message = "\n".join([f"{method.upper()} {path}", body, values, "", str(expires)])
    return message, ";".join(names)


def authorization_header(api_key: str, api_secret: str, method: str, path: str,
                         expires: int, body: str = "", query: dict[str, str] | None = None) -> str:
    """The ``Authorization`` header value for one request."""
    query = query or {}
    message, query_args = signature_message(method, path, body, query, expires)
    digest = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()
    parts = [f"EXO2-HMAC-SHA256 credential={api_key}"]
    if query_args:
        parts.append(f"signed-query-args={query_args}")
    parts.append(f"expires={expires}")
    parts.append(f"signature={base64.b64encode(digest).decode()}")
    return ",".join(parts)


# ── Parsers ───────────────────────────────────────────────────────────────────


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def parse_instance(raw: dict) -> dict:
    itype = _dict(raw.get("instance-type"))
    family, size = itype.get("family"), itype.get("size")
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "state": raw.get("state") or "unknown",
        "type": f"{family}.{size}" if family and size else "",
        "public_ip": raw.get("public-ip") or "",
        "ipv6": raw.get("ipv6-address") or "",
        "created_at": raw.get("created-at") or "",
    }


def parse_sks_cluster(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "state": raw.get("state") or "unknown",
        "version": raw.get("version") or "",
        "level": raw.get("level") or "",
        "endpoint": raw.get("endpoint") or "",
        "nodepool_count": len(_list(raw.get("nodepools"))) if "nodepools" in raw else None,
    }


def parse_dbaas_service(raw: dict) -> dict:
    return {
        "name": raw.get("name") or "",
        "type": raw.get("type") or "",
        "state": raw.get("state") or "unknown",
        "plan": raw.get("plan") or "",
        "version": raw.get("version") or "",
        "termination_protection": (bool(raw["termination-protection"])
                                   if "termination-protection" in raw else None),
    }


def _check_state(status: Any) -> str:
    if isinstance(status, str):
        low = status.lower()
        if low in HEALTHY:
            return "healthy"
        if low in UNHEALTHY:
            return "unhealthy"
    return "unknown"


def parse_load_balancer(raw: dict) -> dict:
    services = []
    for svc in _list(raw.get("services")):
        if not isinstance(svc, dict):
            continue
        checks = [c for c in _list(svc.get("healthcheck-status")) if isinstance(c, dict)]
        states = [_check_state(c.get("status")) for c in checks]
        services.append({
            "id": svc.get("id"),
            "name": svc.get("name") or "",
            "state": svc.get("state") or "unknown",
            "protocol": svc.get("protocol") or "",
            "port": svc.get("port") if isinstance(svc.get("port"), int) else None,
            "healthy": states.count("healthy"),
            "unhealthy": states.count("unhealthy"),
            "unknown": states.count("unknown"),
            "unhealthy_targets": [c.get("public-ip") or "?" for c, s in zip(checks, states)
                                  if s == "unhealthy"],
        })
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "state": raw.get("state") or "unknown",
        "ip": raw.get("ip") or "",
        "services": services,
    }


# ── API client ────────────────────────────────────────────────────────────────


class ExoscaleAPI:
    def __init__(self, api_key: str, api_secret: str, zone: str):
        if zone not in ZONES:
            raise ExoscaleError(f"Unknown Exoscale zone '{zone}'")
        self.zone = zone
        self.base = f"https://api-{zone}.exoscale.com"
        self._key = api_key
        self._secret = api_secret

    async def get(self, client: httpx.AsyncClient, path: str) -> dict:
        """GET ``/v2<path>``, signed. Raises ExoscaleError on any failure."""
        full = f"/v2{path}"
        expires = int(time.time()) + SIGNATURE_VALIDITY_S
        headers = {
            "Authorization": authorization_header(self._key, self._secret, "GET", full, expires),
            "Accept": "application/json",
        }
        try:
            resp = await client.get(f"{self.base}{full}", headers=headers)
        except httpx.HTTPError as exc:
            raise ExoscaleError(f"Request to Exoscale failed ({type(exc).__name__})") from None
        if resp.status_code == 429:
            raise ExoscaleError("Exoscale API rate limit exceeded (HTTP 429)")
        if resp.status_code in (401, 403):
            raise ExoscaleError(f"Exoscale rejected the API key or its role (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise ExoscaleError(f"Exoscale API error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise ExoscaleError("Exoscale returned a response that is not JSON") from None
        if not isinstance(body, dict):
            raise ExoscaleError("Exoscale returned an unexpected response")
        return body

    async def list_items(self, client: httpx.AsyncClient, path: str, key: str) -> list[dict]:
        body = await self.get(client, path)
        items = body.get(key)
        if not isinstance(items, list):
            raise ExoscaleError(f"Exoscale response for {path} has no '{key}' list")
        return [i for i in items if isinstance(i, dict)]

    async def fetch_all(self) -> dict:
        unavailable: set[str] = set()
        async with safe_async_client(timeout=30.0) as client:
            # Core request: a failure here fails the whole collection.
            instances = [parse_instance(i) for i in await self.list_items(client, "/instance", "instances")]

            async def optional(path: str, key: str, label: str, parse) -> list[dict]:
                try:
                    return [parse(i) for i in await self.list_items(client, path, key)]
                except ExoscaleError as exc:
                    log.warning("Exoscale %s unavailable: %s", label, exc)
                    unavailable.add(label)
                    return []

            sks = await optional("/sks-cluster", "sks-clusters", "SKS clusters", parse_sks_cluster)
            dbaas = await optional("/dbaas-service", "dbaas-services", "DBaaS services", parse_dbaas_service)
            lbs = await optional("/load-balancer", "load-balancers", "load balancers", parse_load_balancer)

        return {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "zone": self.zone,
            "instances": instances,
            "sks_clusters": sks,
            "dbaas_services": dbaas,
            "load_balancers": lbs,
            "unavailable": sorted(unavailable),
        }


# ── Integration plugin ────────────────────────────────────────────────────────


class ExoscaleIntegration(BaseIntegration):
    name = "exoscale"
    display_name = "Exoscale"
    icon = ""  # no Simple Icons entry
    color = "rose"
    description = "Monitor Exoscale instances, SKS clusters, DBaaS services and load balancers of one zone."
    # A public SaaS API: the core collects it, never a probe.
    network_scope = "cloud"
    default_interval_seconds = 300

    config_fields = [
        ConfigField(
            key="api_key",
            label="API Key",
            field_type="text",
            placeholder="EXO...",
            required=True,
        ),
        ConfigField(
            key="api_secret",
            label="API Secret",
            field_type="password",
            placeholder="Secret of a read-only IAM key",
            encrypted=True,
            required=True,
        ),
        ConfigField(
            key="zone",
            label="Zone",
            field_type="select",
            required=True,
            default=DEFAULT_ZONE,
            options=[{"value": z, "label": z} for z in ZONES],
        ),
    ]

    async def collect(self) -> CollectorResult:
        key = self.config.get("api_key")
        secret = self.config.get("api_secret")
        if not key or not secret:
            return CollectorResult(success=False, error="API key and secret are required")
        try:
            api = ExoscaleAPI(key, secret, self.config.get("zone") or DEFAULT_ZONE)
            return CollectorResult(success=True, data=await api.fetch_all())
        except ExoscaleError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:
            log.warning("Exoscale collection failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"Exoscale collection failed ({type(exc).__name__})")

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts

        def not_running(items: Any, kind: str, id_key: str, host: bool = False) -> None:
            for item in _list(items):
                if not isinstance(item, dict):
                    continue
                state = item.get("state")
                # No state is not a bad state; the parsers store a missing one as "unknown".
                if not isinstance(state, str) or not state or state in (RUNNING, "unknown"):
                    continue
                name = item.get("name") or str(item.get(id_key) or "?")
                alerts.append(Alert(
                    severity="critical" if state == "error" else "warning",
                    # The state is in the detail: a title naming it would open a
                    # new incident on every change (stopping -> stopped).
                    title=f"{kind} {name} is not running",
                    detail=f"{kind} state is '{state}', expected '{RUNNING}'.",
                    entity=f"{kind.lower()}:{item.get(id_key) or name}",
                    host=(item.get("name") or "") if host else "",
                ))

        not_running(data.get("instances"), "Instance", "id", host=True)
        not_running(data.get("sks_clusters"), "SKS cluster", "id")
        not_running(data.get("dbaas_services"), "DBaaS service", "name")
        not_running(data.get("load_balancers"), "Load balancer", "id")

        for lb in _list(data.get("load_balancers")):
            if not isinstance(lb, dict):
                continue
            lname = lb.get("name") or str(lb.get("id") or "?")
            for svc in _list(lb.get("services")):
                if not isinstance(svc, dict):
                    continue
                unhealthy = svc.get("unhealthy")
                if not isinstance(unhealthy, int) or isinstance(unhealthy, bool) or unhealthy <= 0:
                    continue
                healthy = svc.get("healthy")
                targets = ", ".join(str(t) for t in _list(svc.get("unhealthy_targets")))
                sname = svc.get("name") or str(svc.get("id") or "?")
                alerts.append(Alert(
                    severity="warning" if isinstance(healthy, int) and healthy > 0 else "critical",
                    title=f"Load balancer {lname} service {sname} has unhealthy targets",
                    detail=f"{unhealthy} unhealthy target(s)" + (f": {targets}." if targets else "."),
                    entity=f"nlb-service:{lb.get('id') or lname}:{svc.get('id') or sname}",
                ))
        return alerts

    def get_dashboard_summary(self, data: dict) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
            return None
        instances = [i for i in data["instances"] if isinstance(i, dict)]
        missing = set(_list(data.get("unavailable")))

        def count(key: str, label: str) -> int | None:
            # A part that could not be fetched has no count, not zero.
            return None if label in missing else len(_list(data.get(key)))

        return {
            "instances": len(instances),
            "running": sum(1 for i in instances if i.get("state") == RUNNING),
            "sks_clusters": count("sks_clusters", "SKS clusters"),
            "dbaas_services": count("dbaas_services", "DBaaS services"),
        }

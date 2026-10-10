"""Technitium DNS Server integration – stats, cluster health and updates via its HTTP API."""
from __future__ import annotations

import asyncio

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField


# ── API Client ────────────────────────────────────────────────────────────────


class TechnitiumAPI:
    def __init__(self, host: str, api_token: str, verify_ssl: bool = True):
        self.base = host.rstrip("/")
        self.api_token = api_token
        self.verify_ssl = verify_ssl

    async def _get(self, client: httpx.AsyncClient, endpoint: str, **params) -> dict:
        resp = await client.get(f"{self.base}/api/{endpoint}", params=params,
                                headers={"Authorization": f"Bearer {self.api_token}"})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "ok":
            raise ValueError(f"{endpoint}: {body.get('errorMessage') or body.get('status')}")
        return body.get("response") or {}

    async def fetch_all(self) -> dict:
        async with safe_async_client(verify=self.verify_ssl, timeout=10.0, follow_redirects=True) as client:
            stats, settings, cluster, update = await asyncio.gather(
                self._get(client, "dashboard/stats/get", type="LastDay"),
                self._get(client, "settings/get"),
                self._get(client, "admin/cluster/state"),
                self._get(client, "user/checkForUpdate"),
            )
        return parse_technitium_data(stats, settings, cluster, update)


# ── Parser ────────────────────────────────────────────────────────────────────

# Cluster node states that mean "reachable": the node itself and peers it hears from.
_HEALTHY_NODE_STATES = {"Self", "Connected"}


def parse_technitium_data(stats: dict, settings: dict, cluster: dict, update: dict) -> dict:
    s = stats.get("stats") or {}
    queries = int(s.get("totalQueries", 0))
    blocked = int(s.get("totalBlocked", 0))

    def _top(items: list, key: str = "domain") -> list:
        return [{key: i.get("name", ""), "count": int(i.get("hits", 0))} for i in (items or [])[:10]]

    nodes = [
        {
            "name": n.get("name", ""),
            "type": n.get("type", ""),
            "state": n.get("state", ""),
            "version": n.get("version", ""),
            "ip": ", ".join(n.get("ipAddresses") or []),
            "last_seen": n.get("lastSeen"),
        }
        for n in cluster.get("clusterNodes") or []
    ]
    unhealthy = [n["name"] for n in nodes if n["state"] not in _HEALTHY_NODE_STATES]
    versions = {n["version"] for n in nodes if n["version"]}

    return {
        "status": "enabled" if settings.get("enableBlocking", True) else "disabled",
        "version": str(settings.get("version") or update.get("currentVersion") or ""),
        "server_domain": settings.get("dnsServerDomain", ""),
        "update_available": bool(update.get("updateAvailable", False)),
        "update_version": update.get("updateVersion") or "",
        "queries_today": queries,
        "blocked_today": blocked,
        "blocked_pct": round(blocked / queries * 100, 1) if queries else 0.0,
        "server_failures_today": int(s.get("totalServerFailure", 0)),
        "nxdomain_today": int(s.get("totalNxDomain", 0)),
        "clients": int(s.get("totalClients", 0)),
        "domains_blocked": int(s.get("blockListZones", 0)),
        "zones": int(s.get("zones", 0)),
        "cached_entries": int(s.get("cachedEntries", 0)),
        "blocklist_next_update": settings.get("blockListNextUpdatedOn") or "",
        "top_queries": _top(stats.get("topDomains")),
        "top_blocked": _top(stats.get("topBlockedDomains")),
        "top_clients": _top(stats.get("topClients"), key="client"),
        "cluster_initialized": bool(cluster.get("clusterInitialized", False)),
        "cluster_domain": cluster.get("clusterDomain") or "",
        "cluster_nodes": nodes,
        "cluster_nodes_unhealthy": len(unhealthy),
        "cluster_unhealthy_names": unhealthy,
        "cluster_version_mismatch": len(versions) > 1,
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class TechnitiumIntegration(BaseIntegration):
    name = "technitium"
    display_name = "Technitium DNS"
    icon = ""
    color = "teal"
    description = "Monitor Technitium DNS Server: queries, blocking, cluster health and updates."

    config_fields = [
        ConfigField(key="host", label="Web API URL", field_type="url",
                    placeholder="http://dns1.local:5380"),
        ConfigField(key="api_token", label="API Token", field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox",
                    required=False, default=True),
    ]

    def _api(self) -> TechnitiumAPI:
        return TechnitiumAPI(
            host=self.config["host"],
            api_token=self.config.get("api_token", ""),
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        for name in data.get("cluster_unhealthy_names") or []:
            alerts.append(Alert(severity="critical", title="Cluster node unreachable",
                                detail=f"{name} is not connected to the cluster", entity=f"node: {name}"))
        if data.get("status") == "disabled":
            alerts.append(Alert(severity="warning", title="Blocking disabled",
                                detail="DNS blocking is turned off", entity=data.get("server_domain", "")))
        if data.get("cluster_version_mismatch"):
            alerts.append(Alert(severity="warning", title="Cluster nodes run different versions",
                                detail=", ".join(f"{n['name']}={n['version']}" for n in data.get("cluster_nodes", []))))
        if data.get("update_available"):
            alerts.append(Alert(severity="info", title="Update available",
                                detail=f"{data.get('version')} -> {data.get('update_version')}",
                                entity=data.get("server_domain", "")))
        return alerts

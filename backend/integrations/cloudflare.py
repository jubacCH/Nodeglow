"""Cloudflare integration – zones, DNS records, analytics & security events."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField

log = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"


# ── API Client ────────────────────────────────────────────────────────────────


class CloudflareAPI:
    def __init__(self, api_token: str):
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
        resp = await client.get(f"{API_BASE}{path}", headers=self.headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success", True):
            errors = body.get("errors", [])
            raise ValueError(f"Cloudflare API error: {errors}")
        return body

    async def verify_token(self, client: httpx.AsyncClient) -> bool:
        resp = await self._get(client, "/user/tokens/verify")
        return resp.get("result", {}).get("status") == "active"

    async def list_zones(self, client: httpx.AsyncClient) -> list[dict]:
        zones = []
        page = 1
        while True:
            resp = await self._get(client, "/zones", params={"page": page, "per_page": 50})
            zones.extend(resp.get("result", []))
            info = resp.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return zones

    async def get_dns_records(self, client: httpx.AsyncClient, zone_id: str) -> list[dict]:
        records = []
        page = 1
        while True:
            resp = await self._get(client, f"/zones/{zone_id}/dns_records",
                                   params={"page": page, "per_page": 100})
            records.extend(resp.get("result", []))
            info = resp.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return records

    async def get_zone_analytics(self, client: httpx.AsyncClient, zone_id: str) -> dict:
        """Fetch 24h analytics for a zone via the dashboard endpoint."""
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = await self._get(
                client,
                f"/zones/{zone_id}/analytics/dashboard",
                params={"since": since, "until": until},
            )
            return resp.get("result", {})
        except Exception as exc:
            log.debug("Analytics unavailable for zone %s: %s", zone_id, exc)
            return {}

    async def get_firewall_events(self, client: httpx.AsyncClient, zone_id: str, limit: int = 20) -> list[dict]:
        """Fetch recent firewall events."""
        try:
            resp = await self._get(
                client,
                f"/zones/{zone_id}/security/events",
                params={"per_page": limit},
            )
            return resp.get("result", [])
        except Exception:
            # Firewall events API may not be available on all plans
            return []

    async def fetch_all(self) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Verify token
            try:
                active = await self.verify_token(client)
                if not active:
                    raise ValueError("API token is not active")
            except httpx.HTTPStatusError:
                raise ValueError("Invalid API token")

            # Fetch zones
            zones = await self.list_zones(client)

            zone_data = []
            total_requests = 0
            total_bandwidth = 0
            total_threats = 0
            total_cached = 0
            total_all = 0

            for zone in zones:
                zid = zone["id"]
                zname = zone["name"]
                zstatus = zone.get("status", "unknown")
                zplan = zone.get("plan", {}).get("name", "Unknown")
                ssl_mode = zone.get("ssl", {}).get("mode", "unknown") if isinstance(zone.get("ssl"), dict) else "unknown"

                # DNS records
                dns_records = await self.get_dns_records(client, zid)
                dns_summary = []
                for r in dns_records:
                    dns_summary.append({
                        "type": r.get("type", ""),
                        "name": r.get("name", ""),
                        "content": r.get("content", ""),
                        "proxied": r.get("proxied", False),
                        "ttl": r.get("ttl", 0),
                    })

                # Analytics (24h)
                analytics = await self.get_zone_analytics(client, zid)
                totals = analytics.get("totals", {})
                req = totals.get("requests", {})
                bw = totals.get("bandwidth", {})
                threats_data = totals.get("threats", {})

                requests_all = req.get("all", 0)
                requests_cached = req.get("cached", 0)
                bandwidth_all = bw.get("all", 0)
                bandwidth_cached = bw.get("cached", 0)
                threats_count = threats_data.get("all", 0)

                total_requests += requests_all
                total_bandwidth += bandwidth_all
                total_threats += threats_count
                total_cached += requests_cached
                total_all += requests_all

                # Firewall events
                fw_events = await self.get_firewall_events(client, zid)
                fw_summary = []
                for ev in fw_events[:10]:
                    fw_summary.append({
                        "action": ev.get("action", ""),
                        "source": ev.get("clientIP", ""),
                        "country": ev.get("clientCountry", ""),
                        "rule": ev.get("ruleId", "")[:16] if ev.get("ruleId") else "",
                        "host": ev.get("host", ""),
                        "uri": (ev.get("clientRequestPath") or "")[:60],
                        "timestamp": ev.get("datetime", ""),
                    })

                zone_data.append({
                    "id": zid,
                    "name": zname,
                    "status": zstatus,
                    "plan": zplan,
                    "ssl_mode": ssl_mode,
                    "dns_records": dns_summary,
                    "dns_count": len(dns_summary),
                    "analytics": {
                        "requests_all": requests_all,
                        "requests_cached": requests_cached,
                        "cache_pct": round(requests_cached / requests_all * 100, 1) if requests_all else 0,
                        "bandwidth_all": bandwidth_all,
                        "bandwidth_cached": bandwidth_cached,
                        "threats": threats_count,
                    },
                    "firewall_events": fw_summary,
                })

            return {
                "zone_count": len(zone_data),
                "zones": zone_data,
                "totals": {
                    "requests": total_requests,
                    "bandwidth": total_bandwidth,
                    "threats": total_threats,
                    "cache_pct": round(total_cached / total_all * 100, 1) if total_all else 0,
                },
            }

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await self.verify_token(client)
        except Exception:
            return False


# ── Integration Plugin ────────────────────────────────────────────────────────


class CloudflareIntegration(BaseIntegration):
    name = "cloudflare"
    display_name = "Cloudflare"
    icon = "cloudflare"
    color = "orange"
    description = "Monitor Cloudflare zones, DNS records, analytics & security events."

    config_fields = [
        ConfigField(
            key="api_token",
            label="API Token",
            field_type="password",
            placeholder="Cloudflare API token (not Global API Key)",
            encrypted=True,
            required=True,
        ),
    ]

    def _api(self) -> CloudflareAPI:
        return CloudflareAPI(api_token=self.config["api_token"])

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts = []
        for zone in data.get("zones", []):
            if zone.get("status") != "active":
                alerts.append(Alert(
                    severity="warning",
                    title=f"Zone {zone['name']} is {zone.get('status', 'unknown')}",
                    detail=f"Zone status: {zone.get('status')}",
                    entity=f"zone: {zone['name']}",
                ))
            threats = zone.get("analytics", {}).get("threats", 0)
            if threats > 100:
                alerts.append(Alert(
                    severity="warning",
                    title=f"High threat count on {zone['name']}",
                    detail=f"{threats} threats in last 24h",
                    entity=f"zone: {zone['name']}",
                ))
        return alerts

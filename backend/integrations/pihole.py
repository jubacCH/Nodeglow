"""Pi-hole integration – DNS stats via Pi-hole API (v5 + v6 support)."""
from __future__ import annotations

import logging

import httpx

from integrations._base import BaseIntegration, CollectorResult, ConfigField

log = logging.getLogger(__name__)


# ── API Client ────────────────────────────────────────────────────────────────


class PiholeAPI:
    def __init__(self, host: str, api_key: str | None = None, verify_ssl: bool = True):
        self.base = host.rstrip("/")
        self.api_key = api_key
        self.verify_ssl = verify_ssl

    async def _try_v6(self, client: httpx.AsyncClient) -> dict | None:
        """Attempt Pi-hole v6 API. Returns parsed data or None."""
        # v6 requires authentication via session
        auth_resp = await client.post(
            f"{self.base}/api/auth", json={"password": self.api_key or ""})
        if auth_resp.status_code != 200:
            log.debug("Pi-hole v6 auth returned %s", auth_resp.status_code)
            return None
        auth_data = auth_resp.json()
        sid = (auth_data.get("session") or {}).get("sid")
        if not sid:
            log.debug("Pi-hole v6 auth: no session SID in response: %s", auth_data)
            return None

        headers = {"sid": sid}

        stats_resp = await client.get(
            f"{self.base}/api/stats/summary", headers=headers)
        stats_resp.raise_for_status()
        raw = stats_resp.json()

        top_resp = await client.get(
            f"{self.base}/api/stats/top_domains", headers=headers,
            params={"blocked": "false", "count": 10})
        top_blocked_resp = await client.get(
            f"{self.base}/api/stats/top_domains", headers=headers,
            params={"blocked": "true", "count": 10})

        top_queries = []
        top_blocked = []
        if top_resp.status_code == 200:
            domains = top_resp.json().get("domains", [])
            top_queries = [{"domain": d.get("domain", ""), "count": d.get("count", 0)}
                           for d in domains[:10]]
        if top_blocked_resp.status_code == 200:
            domains = top_blocked_resp.json().get("domains", [])
            top_blocked = [{"domain": d.get("domain", ""), "count": d.get("count", 0)}
                           for d in domains[:10]]

        # Fetch local DNS records (v6: /api/config/dns/hosts)
        local_dns = []
        try:
            dns_resp = await client.get(
                f"{self.base}/api/config/dns/hosts", headers=headers)
            if dns_resp.status_code == 200:
                entries = dns_resp.json().get("config", {}).get("dns", {}).get("hosts", [])
                for entry in entries:
                    parts = entry.split(None, 1)
                    if len(parts) == 2:
                        local_dns.append({"domain": parts[1], "ip": parts[0]})
        except Exception:
            pass

        # Fetch local CNAME records (v6)
        try:
            cname_resp = await client.get(
                f"{self.base}/api/config/dns/cnameRecords", headers=headers)
            if cname_resp.status_code == 200:
                cnames = cname_resp.json().get("config", {}).get("dns", {}).get("cnameRecords", [])
                for entry in cnames:
                    if isinstance(entry, str) and "," in entry:
                        domain, target = entry.split(",", 1)
                        local_dns.append({"domain": domain, "ip": target, "type": "CNAME"})
        except Exception:
            pass

        try:
            await client.delete(f"{self.base}/api/auth", headers=headers)
        except Exception:
            pass

        return parse_pihole_v6_data(raw, top_queries, top_blocked, local_dns)

    async def _try_v5(self, client: httpx.AsyncClient) -> dict:
        """Attempt Pi-hole v5 API."""
        params: dict = {"summaryRaw": ""}
        if self.api_key:
            params["auth"] = self.api_key

        resp = await client.get(f"{self.base}/admin/api.php", params=params)
        resp.raise_for_status()
        raw = resp.json()

        if not raw or "status" not in raw:
            raise ValueError(f"Unexpected Pi-hole v5 response: {raw}")

        top_queries = []
        top_blocked = []
        if self.api_key:
            tq_params = {"topItems": 10, "auth": self.api_key}
        else:
            tq_params = {"topItems": 10}
        try:
            tq_resp = await client.get(f"{self.base}/admin/api.php", params=tq_params)
            if tq_resp.status_code == 200:
                tq_data = tq_resp.json()
                tq_raw = tq_data.get("top_queries") or {}
                tb_raw = tq_data.get("top_ads") or {}
                top_queries = [{"domain": d, "count": c}
                               for d, c in sorted(tq_raw.items(), key=lambda x: -x[1])][:10]
                top_blocked = [{"domain": d, "count": c}
                               for d, c in sorted(tb_raw.items(), key=lambda x: -x[1])][:10]
        except Exception:
            pass

        # Fetch local DNS records (v5)
        local_dns = []
        if self.api_key:
            try:
                dns_params = {"customdns": "", "action": "get", "auth": self.api_key}
                dns_resp = await client.get(f"{self.base}/admin/api.php", params=dns_params)
                if dns_resp.status_code == 200:
                    dns_data = dns_resp.json()
                    for entry in dns_data.get("data", []):
                        if isinstance(entry, list) and len(entry) >= 2:
                            local_dns.append({"domain": entry[0], "ip": entry[1]})
            except Exception:
                pass
            # Also try CNAME records
            try:
                cname_params = {"customcname": "", "action": "get", "auth": self.api_key}
                cname_resp = await client.get(f"{self.base}/admin/api.php", params=cname_params)
                if cname_resp.status_code == 200:
                    cname_data = cname_resp.json()
                    for entry in cname_data.get("data", []):
                        if isinstance(entry, list) and len(entry) >= 2:
                            local_dns.append({"domain": entry[0], "ip": entry[1], "type": "CNAME"})
            except Exception:
                pass

        return parse_pihole_data(raw, top_queries, top_blocked, local_dns)

    async def fetch_all(self) -> dict:
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=10.0, follow_redirects=True) as client:
            # Try v6 first (check if /api/auth endpoint exists)
            v6_error = None
            try:
                result = await self._try_v6(client)
                if result is not None:
                    return result
            except Exception as exc:
                v6_error = exc
                log.debug("Pi-hole v6 attempt failed: %s", exc)

            # Fall back to v5
            try:
                return await self._try_v5(client)
            except Exception as v5_error:
                # If both fail, report the most relevant error
                if v6_error:
                    raise ValueError(
                        f"Pi-hole v6 failed: {v6_error}; v5 fallback also failed: {v5_error}"
                    ) from v5_error
                raise


    async def health_check(self) -> bool:
        try:
            await self.fetch_all()
            return True
        except Exception:
            return False


# ── Parsers ───────────────────────────────────────────────────────────────────


def parse_pihole_data(raw: dict, top_queries: list, top_blocked: list, local_dns: list | None = None) -> dict:
    queries_today = int(raw.get("dns_queries_today", 0))
    blocked_today = int(raw.get("ads_blocked_today", 0))
    blocked_pct = float(raw.get("ads_percentage_today", 0.0))
    domains_blocked = int(raw.get("domains_being_blocked", 0))
    clients = int(raw.get("unique_clients", 0))
    status = str(raw.get("status", "unknown"))

    reply_types = {}
    for key, val in raw.items():
        if key.startswith("reply_"):
            reply_types[key[6:]] = val

    gravity = raw.get("gravity_last_updated", {})
    gravity_str = ""
    if isinstance(gravity, dict):
        relative = gravity.get("relative", {})
        if relative:
            days = relative.get("days", 0)
            hours = relative.get("hours", 0)
            mins = relative.get("minutes", 0)
            gravity_str = f"{days}d {hours}h {mins}m ago"
        elif gravity.get("absolute"):
            import datetime
            ts = gravity["absolute"]
            try:
                gravity_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                gravity_str = str(ts)

    return {
        "status": status, "queries_today": queries_today,
        "blocked_today": blocked_today, "blocked_pct": round(blocked_pct, 1),
        "domains_blocked": domains_blocked,
        "dns_queries_all_types": int(raw.get("dns_queries_all_types", 0)),
        "reply_types": reply_types, "top_queries": top_queries,
        "top_blocked": top_blocked, "clients": clients,
        "gravity_last_updated": gravity_str, "api_version": 5,
        "local_dns": local_dns or [],
    }


def parse_pihole_v6_data(raw: dict, top_queries: list, top_blocked: list, local_dns: list | None = None) -> dict:
    queries = raw.get("queries", {})
    gravity = raw.get("gravity", {})
    clients = raw.get("clients", {})

    queries_today = int(queries.get("total", 0))
    blocked_today = int(queries.get("blocked", 0))
    blocked_pct = float(queries.get("percent_blocked", 0.0))
    domains_blocked = int(gravity.get("domains_being_blocked", 0))
    unique_clients = int(clients.get("unique", 0))
    status = "enabled" if raw.get("blocking", {}).get("enabled", True) else "disabled"

    return {
        "status": status, "queries_today": queries_today,
        "blocked_today": blocked_today, "blocked_pct": round(blocked_pct, 1),
        "domains_blocked": domains_blocked,
        "dns_queries_all_types": queries_today,
        "reply_types": {}, "top_queries": top_queries,
        "top_blocked": top_blocked, "clients": unique_clients,
        "gravity_last_updated": "", "api_version": 6,
        "local_dns": local_dns or [],
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class PiholeIntegration(BaseIntegration):
    name = "pihole"
    display_name = "Pi-hole"
    icon = "pihole"
    color = "red"
    description = "Monitor Pi-hole DNS filtering (v5 + v6)."

    config_fields = [
        ConfigField(key="host", label="Host URL", field_type="url",
                    placeholder="http://pihole.local"),
        ConfigField(key="api_key", label="API Key / Password", field_type="password",
                    encrypted=True, required=False),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox",
                    required=False, default=True),
    ]

    def _api(self) -> PiholeAPI:
        return PiholeAPI(
            host=self.config["host"],
            api_key=self.config.get("api_key"),
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        return await self._api().health_check()

"""Nginx Proxy Manager integration – proxy hosts, SSL certificates & redirections."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField

log = logging.getLogger(__name__)


# ── API Client ────────────────────────────────────────────────────────────────


class NpmAPI:
    def __init__(self, host: str, email: str, password: str, verify_ssl: bool = False):
        self.base = host.rstrip("/")
        self.email = email
        self.password = password
        self.verify_ssl = verify_ssl
        self.token: str | None = None

    async def _auth(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            f"{self.base}/api/tokens",
            json={"identity": self.email, "secret": self.password},
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data.get("token")
        if not self.token:
            raise ValueError("NPM auth failed: no token in response")

    async def _get(self, client: httpx.AsyncClient, path: str) -> list | dict:
        if not self.token:
            await self._auth(client)
        resp = await client.get(
            f"{self.base}/api/{path}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_all(self) -> dict:
        async with httpx.AsyncClient(
            verify=self.verify_ssl, timeout=15.0, follow_redirects=True,
        ) as client:
            await self._auth(client)

            proxy_hosts = await self._get(client, "nginx/proxy-hosts")
            redir_hosts = await self._get(client, "nginx/redirection-hosts")
            streams = await self._get(client, "nginx/streams")
            dead_hosts = await self._get(client, "nginx/dead-hosts")
            certs = await self._get(client, "nginx/certificates")

            return _parse(proxy_hosts, redir_hosts, streams, dead_hosts, certs)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(
                verify=self.verify_ssl, timeout=10.0, follow_redirects=True,
            ) as client:
                await self._auth(client)
                return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def _days_until(iso_str: str | None) -> int | None:
    if not iso_str:
        return None
    try:
        exp = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return max(0, (exp - datetime.now(timezone.utc)).days)
    except Exception:
        return None


def _parse(
    proxy_hosts: list,
    redir_hosts: list,
    streams: list,
    dead_hosts: list,
    certs: list,
) -> dict:
    now = datetime.now(timezone.utc)

    # ── Proxy hosts ──
    proxies = []
    online_count = 0
    offline_count = 0
    ssl_count = 0

    for h in proxy_hosts:
        domain_names = h.get("domain_names", [])
        enabled = h.get("enabled", 1) == 1
        ssl_forced = bool(h.get("ssl_forced", 0))
        cert_id = h.get("certificate_id", 0)
        forward_host = h.get("forward_host", "")
        forward_port = h.get("forward_port", "")
        forward_scheme = h.get("forward_scheme", "http")

        if enabled:
            online_count += 1
        else:
            offline_count += 1

        if cert_id and cert_id > 0:
            ssl_count += 1

        # Access list info
        access_list_id = h.get("access_list_id", 0)

        proxies.append({
            "id": h.get("id"),
            "domains": domain_names,
            "domain_primary": domain_names[0] if domain_names else "",
            "enabled": enabled,
            "ssl_forced": ssl_forced,
            "certificate_id": cert_id,
            "forward": f"{forward_scheme}://{forward_host}:{forward_port}",
            "has_access_list": access_list_id > 0,
            "advanced_config": bool(h.get("advanced_config", "").strip()),
            "meta": h.get("meta", {}),
        })

    # ── Certificates ──
    cert_list = []
    expiring_soon = 0
    expired = 0

    for c in certs:
        provider = c.get("provider", "other")
        nice_name = c.get("nice_name", "")
        domains = c.get("domain_names", [])
        expires_on = c.get("expires_on")
        days_left = _days_until(expires_on)

        if days_left is not None:
            if days_left <= 0:
                expired += 1
            elif days_left <= 14:
                expiring_soon += 1

        cert_list.append({
            "id": c.get("id"),
            "nice_name": nice_name,
            "provider": provider,
            "domains": domains,
            "expires_on": expires_on,
            "days_left": days_left,
        })

    # Sort by expiry (soonest first)
    cert_list.sort(key=lambda c: c["days_left"] if c["days_left"] is not None else 9999)

    # ── Redirections ──
    redirs = []
    for r in redir_hosts:
        redirs.append({
            "id": r.get("id"),
            "domains": r.get("domain_names", []),
            "forward_url": r.get("forward_domain_name", ""),
            "forward_scheme": r.get("forward_scheme", ""),
            "forward_code": r.get("forward_http_code", 302),
            "enabled": r.get("enabled", 1) == 1,
            "preserve_path": bool(r.get("preserve_path", 0)),
        })

    # ── Streams ──
    stream_list = []
    for s in streams:
        stream_list.append({
            "id": s.get("id"),
            "incoming_port": s.get("incoming_port"),
            "forwarding_host": s.get("forwarding_host", ""),
            "forwarding_port": s.get("forwarding_port"),
            "enabled": s.get("enabled", 1) == 1,
            "tcp": bool(s.get("tcp_forwarding", 1)),
            "udp": bool(s.get("udp_forwarding", 0)),
        })

    # ── 404 hosts ──
    dead_list = []
    for d in dead_hosts:
        dead_list.append({
            "id": d.get("id"),
            "domains": d.get("domain_names", []),
            "enabled": d.get("enabled", 1) == 1,
        })

    return {
        "proxy_hosts": proxies,
        "proxy_count": len(proxies),
        "online_count": online_count,
        "offline_count": offline_count,
        "ssl_host_count": ssl_count,
        "certificates": cert_list,
        "cert_count": len(cert_list),
        "certs_expiring_soon": expiring_soon,
        "certs_expired": expired,
        "redirections": redirs,
        "redir_count": len(redirs),
        "streams": stream_list,
        "stream_count": len(stream_list),
        "dead_hosts": dead_list,
        "dead_count": len(dead_list),
    }


# ── Integration Plugin ────────────────────────────────────────────────────────


class NpmIntegration(BaseIntegration):
    name = "npm"
    display_name = "Nginx Proxy Manager"
    icon = "nginxproxymanager"
    color = "red"
    description = "Monitor proxy hosts, SSL certificates, redirections & streams."

    config_fields = [
        ConfigField(
            key="host",
            label="NPM URL",
            field_type="url",
            placeholder="http://npm.local:81",
            required=True,
        ),
        ConfigField(
            key="email",
            label="Admin Email",
            field_type="text",
            placeholder="admin@example.com",
            required=True,
        ),
        ConfigField(
            key="password",
            label="Password",
            field_type="password",
            encrypted=True,
            required=True,
        ),
        ConfigField(
            key="verify_ssl",
            label="Verify SSL",
            field_type="checkbox",
            required=False,
            default=False,
        ),
    ]

    def _api(self) -> NpmAPI:
        return NpmAPI(
            host=self.config["host"],
            email=self.config["email"],
            password=self.config["password"],
            verify_ssl=self.config.get("verify_ssl", False),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        for cert in data.get("certificates", []):
            days = cert.get("days_left")
            if days is not None and days <= 0:
                alerts.append(Alert(
                    severity="critical",
                    title=f"SSL certificate expired: {cert['nice_name']}",
                    detail=f"Expired on {cert.get('expires_on', 'unknown')}",
                    entity=f"cert: {cert['nice_name']}",
                ))
            elif days is not None and days <= 7:
                alerts.append(Alert(
                    severity="warning",
                    title=f"SSL certificate expiring soon: {cert['nice_name']}",
                    detail=f"{days} days remaining",
                    entity=f"cert: {cert['nice_name']}",
                ))
        return alerts

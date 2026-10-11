"""Isolation smoke test through the HTTP stack (sessions, memberships, API
keys, the WebSocket hub and notifications have their own two-tenant tests in
test_tenant_auth_binding.py).

A second tenant is seeded directly in the database, every row carrying a
canary string. The core resolves every request to tenant 1, so no response
may contain tenant 2's canary — for lists, details, aggregates and the
dashboard — and tenant 2's ids answer 404. This is the seed of the full
route-walking isolation suite (spec, "Test strategy" 2).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from models import (
    Agent, AlertRule, ApiKey, AuditLog, Credential, DiscoveredPort, Incident, IncidentEvent,
    IntegrationConfig, NotificationLog, PingHost, Site, Snapshot, Tenant, TenantSetting,
)
from services.tenancy import system_scope
from tests.test_routers.conftest import make_client

CANARY = "CANARY-B"
B = 2


async def _seed_tenant_b(sf) -> dict:
    async with sf() as s:
        with system_scope("test: seed tenant B"):
            s.add(Tenant(id=B, provider_id=1, name=f"{CANARY}-tenant", slug="b"))
            # The tenant first: the system-scope audit row of the next flush
            # references it (PostgreSQL enforces that FK, SQLite does not).
            await s.flush()
            s.add(Site(id=2, tenant_id=B, name=f"{CANARY}-site"))
            await s.flush()
            s.add(Agent(id=900, tenant_id=B, name=f"{CANARY}-agent", hostname="b-srv",
                        token="b-token", is_probe=True))
            s.add(PingHost(id=900, tenant_id=B, name=f"{CANARY}-host", hostname="10.9.9.9",
                           probe_id=900, site_id=2))
            await s.flush()
            s.add(DiscoveredPort(tenant_id=B, host_id=900, port=22, service=f"{CANARY}-ssh"))
            s.add(Incident(id=900, tenant_id=B, rule="host_down", title=f"{CANARY}-incident",
                           status="open", severity="critical", host_ids="[900]"))
            await s.flush()
            s.add(IncidentEvent(tenant_id=B, incident_id=900, event_type="created",
                                summary=f"{CANARY}-event"))
            s.add(IntegrationConfig(id=900, tenant_id=B, type="proxmox", name=f"{CANARY}-pve",
                                    config_json="{}"))
            s.add(Snapshot(tenant_id=B, entity_type="proxmox", entity_id=900, ok=True,
                           data_json='{"note": "%s-data"}' % CANARY))
            s.add(AlertRule(tenant_id=B, name=f"{CANARY}-rule", source_type="ping",
                            field_path="x", operator="gt"))
            s.add(Credential(tenant_id=B, name=f"{CANARY}-cred", type="snmp_v2c", data_json="x"))
            s.add(AuditLog(tenant_id=B, action=f"{CANARY}-action", timestamp=datetime.utcnow()))
            # Phase 3/4: settings, notification history and API keys.
            s.add(TenantSetting(tenant_id=B, key="site_name", value=f"{CANARY}-site-name"))
            s.add(TenantSetting(tenant_id=B, key="telegram_chat_id", value=f"{CANARY}-chat"))
            s.add(TenantSetting(tenant_id=B, key="ntfy_topic", value=f"{CANARY}-topic"))
            s.add(NotificationLog(tenant_id=B, channel="telegram", title=f"{CANARY}-notification",
                                  message="m", severity="info", status="sent"))
            s.add(ApiKey(tenant_id=B, name=f"{CANARY}-key", key_hash="b-key-hash", prefix="ng_b",
                         role="admin", enabled=True))
            await s.commit()
    # And one host of tenant 1, so the lists are not trivially empty.
    async with sf() as s:
        s.add(PingHost(name="a-host", hostname="10.0.0.1"))
        await s.commit()
    return {"host": 900, "agent": 900, "incident": 900, "integration": 900}


LIST_ROUTES = [
    "/api/v1/status",
    "/api/v1/hosts",
    "/api/v1/agents",
    "/api/v1/integrations",
    "/api/v1/incidents",
    "/api/v1/audit",
    "/api/v1/topology",
    "/api/v2/summary",
    "/api/v2/dashboard",
    "/api/tasks",
    "/api/v2/nav-counts",
    "/api/v1/keys",
    "/settings/json",
    "/settings/ai/config",
    "/settings/api-keys",
    "/settings/notifications/history",
]

DETAIL_ROUTES = [
    "/api/v1/hosts/{host}",
    "/api/v1/agents/{agent}",
    "/api/v1/incidents/{incident}",
    "/api/v1/integrations/{integration}",
]


@pytest.fixture
async def client_and_ids():
    async with make_client() as (client, sf):
        ids = await _seed_tenant_b(sf)
        yield client, ids


async def test_lists_and_aggregates_never_show_another_tenant(client_and_ids):
    client, _ = client_and_ids
    for path in LIST_ROUTES:
        resp = await client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
        assert CANARY not in resp.text, f"{path} leaked tenant B data"
    hosts = (await client.get("/api/v1/hosts")).json()
    names = [h.get("name") for h in (hosts if isinstance(hosts, list) else hosts.get("hosts", []))]
    assert "a-host" in names


async def test_the_canary_is_visible_from_its_own_tenant(client_and_ids, monkeypatch):
    """Control: resolve requests to tenant B and its rows do show up — so the
    test above passes because of the filter, not because of empty routes."""
    import extensions
    from services.tenancy import SingleTenancy

    from services.tenancy import TenantAccess

    class TenantB(SingleTenancy):
        async def resolve_access(self, request, user):
            return TenantAccess(tenant_id=B, role="admin")

    monkeypatch.setattr(extensions.registry, "tenancy_backend", TenantB())
    client, ids = client_and_ids
    for path in ("/api/v1/hosts", "/api/v1/incidents",
                 "/api/v1/incidents/{incident}".format(**ids),
                 "/settings/json", "/settings/notifications/history", "/api/v1/keys"):
        resp = await client.get(path)
        assert resp.status_code == 200 and CANARY in resp.text, path
    assert "a-host" not in (await client.get("/api/v1/hosts")).text


async def test_another_tenants_ids_are_not_found(client_and_ids):
    client, ids = client_and_ids
    for template in DETAIL_ROUTES:
        path = template.format(**ids)
        resp = await client.get(path)
        assert resp.status_code == 404, f"{path}: {resp.status_code}"
        assert CANARY not in resp.text


async def test_mutating_another_tenants_row_changes_nothing():
    async with make_client() as (client, sf):
        ids = await _seed_tenant_b(sf)
        resp = await client.post(f"/api/v1/incidents/{ids['incident']}/resolve")
        assert resp.status_code == 404
        resp = await client.delete(f"/api/v1/hosts/{ids['host']}")
        assert resp.status_code in (404, 405)
        async with sf() as s:
            with system_scope("test: verify tenant B untouched"):
                inc = await s.get(Incident, ids["incident"])
                host = await s.get(PingHost, ids["host"])
        assert inc.status == "open" and host is not None

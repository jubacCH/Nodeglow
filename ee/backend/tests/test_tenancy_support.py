"""Support access (owner decision 8): operators reach a tenant's data only
while the tenant's admin has granted it — time-limited, revocable, every
access audited (``acting_via = support``)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from models import PingHost
from models.audit import AuditLog
from models.tenant import ProviderMembership, SupportGrant
from services.tenancy import system_scope
from tenancy_helpers import tenancy_env

from ee_license_helpers import issue_test_license

CANARY = "CANARY-B"


@pytest.fixture
async def env():
    async with tenancy_env() as e:
        await e.tenant(2, "Beta")
        async with e.sf() as s:
            with system_scope("test: seed B", audit=False):
                s.add(PingHost(id=900, tenant_id=2, name=f"{CANARY}-host", hostname="10.9.9.9",
                               site_id=20))
                await s.commit()
        e.op = await e.user("operator", {1: "admin"}, is_operator=True)
        e.op_session = await e.session(e.op, 1)
        e.b_admin = await e.user("beta-admin", {2: "admin"})
        e.b_session = await e.session(e.b_admin, 2)
        yield e


B_HEADER = {"X-Nodeglow-Tenant": "2"}


async def _grant(env, **body):
    env.login_as(env.b_session)
    r = await env.client.post("/api/support-access",
                              json={"reason": "ticket #4711", "hours": 2, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _access_rows(env) -> list[AuditLog]:
    return await env.audit("support.access")


async def test_operators_have_no_access_by_default(env):
    env.login_as(env.op_session)
    r = await env.client.get("/api/v1/hosts", headers=B_HEADER)
    assert r.status_code == 404 and CANARY not in r.text
    me = (await env.client.get("/api/auth/me")).json()
    assert [t["id"] for t in me["tenants"]] == [1]
    assert (await env.client.post("/api/auth/tenant", json={"tenant_id": 2})).status_code == 404


async def test_a_grant_opens_the_tenant_to_operators_and_every_access_is_audited(env):
    grant = await _grant(env)
    assert grant["status"] == "active" and grant["role"] == "readonly"
    assert grant["granted_by"] == "beta-admin"
    [granted] = await env.audit("support.grant")
    assert granted.tenant_id == 2 and granted.acting_via == "membership"

    env.login_as(env.op_session)
    me = (await env.client.get("/api/auth/me")).json()
    assert {"id": 2, "name": "Beta", "role": "readonly", "via": "support"} in me["tenants"]
    r = await env.client.get("/api/v1/hosts", headers=B_HEADER)
    assert r.status_code == 200 and CANARY in r.text
    # Read-only: no writes.
    r = await env.client.post("/api/v1/hosts", json={"name": "x", "hostname": "x"}, headers=B_HEADER)
    assert r.status_code == 403
    rows = await _access_rows(env)
    assert rows and {(a.tenant_id, a.acting_via, a.user_id) for a in rows} == {(2, "support", env.op)}
    assert any('"path": "/api/v1/hosts"' in a.details for a in rows)
    # The same request again inside the window: no second row for it.
    n = len([a for a in rows if '"GET"' in a.details and "/api/v1/hosts" in a.details])
    await env.client.get("/api/v1/hosts", headers=B_HEADER)
    rows = await _access_rows(env)
    assert len([a for a in rows if '"GET"' in a.details and "/api/v1/hosts" in a.details]) == n
    # Switching into the tenant works and is audited with how.
    r = await env.client.post("/api/auth/tenant", json={"tenant_id": 2})
    assert r.status_code == 200 and r.json()["via"] == "support"
    [switch] = [a for a in await env.audit("auth.tenant_switch") if a.tenant_id == 2]
    assert switch.acting_via == "support"
    # Tenant 1 stays the operator's own (membership).
    assert (await env.client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "1"})).status_code == 200


async def test_editor_grant_writes_are_audited_as_support(env):
    await _grant(env, role="editor")
    env.login_as(env.op_session)
    r = await env.client.post("/api/v1/hosts", json={"name": "via-support", "hostname": "10.1.1.1"},
                              headers=B_HEADER)
    assert r.status_code in (200, 201), r.text
    async with env.sf() as s:
        with system_scope("test", audit=False):
            host = (await s.execute(select(PingHost).where(PingHost.name == "via-support"))).scalar_one()
            writes = (await s.execute(select(AuditLog).where(
                AuditLog.tenant_id == 2, AuditLog.user_id == env.op,
                AuditLog.action != "support.access"))).scalars().all()
    assert host.tenant_id == 2
    assert writes and {a.acting_via for a in writes} == {"support"}


async def test_support_access_cannot_grant_support_access(env):
    await _grant(env, role="admin")
    env.login_as(env.op_session)
    r = await env.client.post("/api/support-access", json={"reason": "extend", "hours": 300},
                              headers=B_HEADER)
    assert r.status_code == 403
    state = (await env.client.get("/api/support-access", headers=B_HEADER)).json()
    assert state["via_support"] is True and state["can_manage"] is False and state["active"]


async def test_banner_for_every_member_history_for_admins(env):
    reader = await env.user("beta-reader", {2: "readonly"})
    env.login_as(await env.session(reader, 2))
    assert (await env.client.get("/api/support-access")).json()["active"] == []
    grant = await _grant(env)
    env.login_as(await env.session(reader, 2))
    state = (await env.client.get("/api/support-access")).json()
    assert [g["id"] for g in state["active"]] == [grant["id"]]
    assert state["history"] == [] and state["can_manage"] is False
    assert (await env.client.post("/api/support-access", json={"reason": "x" * 5})).status_code == 403
    env.login_as(env.b_session)
    state = (await env.client.get("/api/support-access")).json()
    assert state["can_manage"] is True and [g["id"] for g in state["history"]] == [grant["id"]]


async def test_revoke_ends_access_at_once(env):
    grant = await _grant(env)
    env.login_as(env.op_session)
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 200
    env.login_as(env.b_session)
    r = await env.client.delete(f"/api/support-access/{grant['id']}")
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    env.login_as(env.op_session)
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 404
    [row] = await env.audit("support.revoke")
    assert row.tenant_id == 2
    assert (await env.client.delete(f"/api/support-access/{grant['id']}")).status_code == 404


async def test_expiry_ends_access_and_is_audited_once(env):
    from nodeglow_ee.tenancy import jobs, support

    grant = await _grant(env)
    async with env.sf() as s:
        with system_scope("test", audit=False):
            row = await s.get(SupportGrant, grant["id"])
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
            await s.commit()
    env.login_as(env.op_session)
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 404
    assert await jobs.run_support_expiry() == 1
    assert await support.expire_grants() == 0
    [row] = await env.audit("support.expired")
    assert row.tenant_id == 2 and row.target_id == grant["id"]
    env.login_as(env.b_session)
    assert (await env.client.get("/api/support-access")).json()["history"][0]["status"] == "expired"


async def test_an_own_membership_wins_over_support(env):
    await _grant(env, role="admin")
    async with env.sf() as s:
        from models.tenant import TenantMembership

        s.add(TenantMembership(user_id=env.op, tenant_id=2, role="readonly"))
        await s.commit()
    env.login_as(env.op_session)
    me = (await env.client.get("/api/auth/me")).json()
    assert {"id": 2, "name": "Beta", "role": "readonly", "via": "membership"} in me["tenants"]
    await env.client.get("/api/v1/hosts", headers=B_HEADER)
    assert await _access_rows(env) == []


async def test_who_may_use_a_grant(env):
    await _grant(env)
    # A tenant-1 editor who is no operator: nothing.
    nobody = await env.user("t1-editor", {1: "editor"})
    env.login_as(await env.session(nobody, 1))
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 404
    # A member of B's provider (MSP staff) may.
    msp = await env.user("msp-tech", {1: "readonly"})
    async with env.sf() as s:
        s.add(ProviderMembership(user_id=msp, provider_id=1, role="msp_operator"))
        await s.commit()
    env.login_as(await env.session(msp, 1))
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 200


async def test_a_grant_opens_only_its_own_tenant(env):
    await env.tenant(3, "Gamma")
    async with env.sf() as s:
        with system_scope("test", audit=False):
            s.add(PingHost(id=901, tenant_id=3, name="CANARY-C-host", hostname="10.3.3.3", site_id=30))
            await s.commit()
    await _grant(env)
    env.login_as(env.op_session)
    r = await env.client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "3"})
    assert r.status_code == 404
    r = await env.client.get("/api/v1/hosts", headers=B_HEADER)
    assert "CANARY-C" not in r.text


async def test_suspended_tenant_grants_open_nothing(env):
    await _grant(env)
    async with env.sf() as s:
        from models.tenant import Tenant

        (await s.get(Tenant, 2)).status = "suspended"
        await s.commit()
    env.login_as(env.op_session)
    assert (await env.client.get("/api/v1/hosts", headers=B_HEADER)).status_code == 404


async def test_validation_and_licence(env, ee_license):
    env.login_as(env.b_session)
    for body in ({"reason": "", "hours": 2}, {"reason": "ok ok", "hours": 0},
                 {"reason": "ok ok", "hours": 24 * 15}, {"reason": "ok ok", "role": "root"},
                 {"reason": "x" * 501}):
        assert (await env.client.post("/api/support-access", json=body)).status_code == 400, body
    grant = await _grant(env)
    ee_license.set(issue_test_license(days=-30))
    env.login_as(env.b_session)
    r = await env.client.post("/api/support-access", json={"reason": "again", "hours": 1})
    assert r.status_code == 402
    # Revoking is always possible.
    assert (await env.client.delete(f"/api/support-access/{grant['id']}")).status_code == 200


async def test_websocket_revalidation_sees_support_access_end(env):
    """A long-lived stream re-resolves: a revoked grant ends it."""
    from services import tenancy

    grant = await _grant(env)

    class _Conn:
        def __init__(self, cookie):
            self.cookies = {"nodeglow_session": cookie}
            self.headers = {"x-nodeglow-tenant": "2"}
            self.query_params = {}
            self.scope = {"type": "websocket", "path": "/ws/live"}
            self.state = type("S", (), {})()
            self.client = None

    conn = _Conn(env.op_session)
    access = await tenancy.revalidate_access(conn, 2)
    assert access is not None and access.via == "support"
    env.login_as(env.b_session)
    await env.client.delete(f"/api/support-access/{grant['id']}")
    assert await tenancy.revalidate_access(_Conn(env.op_session), 2) is None

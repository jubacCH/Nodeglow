"""Tenant lifecycle (multi-tenancy phase 9, ee): create, suspend, resume,
schedule deletion, plans, licence limits — through the HTTP API with real
auth and the ee tenancy backend.

Runs on SQLite here and, in the PostgreSQL router mode
(``ROUTER_TESTS_ON_PG``), as ``nodeglow_app`` with RLS on.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models import PingHost
from models.settings import Session
from models.tenant import Site, Tenant, TenantMembership
from services import tenancy
from services.tenancy import system_scope, tenant_scope
from tenancy_helpers import tenancy_env

from ee_license_helpers import issue_test_license


@pytest.fixture
async def env(monkeypatch):
    # SQLite has no row-level security: the documented override, and a
    # finished ClickHouse rebuild (step B), unless a test says otherwise.
    monkeypatch.setenv("NODEGLOW_TENANTS_WITHOUT_RLS", "1")
    with patch("services.clickhouse_rebuild.complete", new=AsyncMock(return_value=True)):
        async with tenancy_env() as e:
            op = await e.user("operator", {1: "admin"}, is_operator=True)
            e.op_session = await e.session(op, 1)
            e.login_as(e.op_session)
            yield e


def _create_body(**kw):
    body = {"name": "Acme AG", "admin_username": "acme-admin", "admin_password": "Acme-Passw0rd!"}
    body.update(kw)
    return body


async def test_the_ee_backend_is_active(env):
    from nodeglow_ee.tenancy.backend import MultiTenancy

    assert isinstance(tenancy.backend(), MultiTenancy)


async def test_operator_lists_tenants_with_licence_usage(env):
    r = await env.client.get("/api/tenants")
    assert r.status_code == 200, r.text
    data = r.json()
    assert [t["id"] for t in data["tenants"]] == [1]
    assert data["tenants"][0]["default"] is True
    assert data["usage"]["count"] == 1 and data["usage"]["max"] is None
    assert {p["key"]: p["retention_days"] for p in data["plans"]} == {
        "standard": 30, "professional": 90, "enterprise": 365}
    assert data["deletion_grace_days"] == 30
    me = (await env.client.get("/api/auth/me")).json()
    assert me["user"]["is_operator"] is True and me["tenant"] == {"id": 1, "via": "membership"}


async def test_tenant_admin_who_is_no_operator_is_refused(env):
    uid = await env.user("admin-only", {1: "admin"}, is_operator=False)
    env.login_as(await env.session(uid, 1))
    # In the core rule the admin of tenant 1 is the operator; ee keeps that
    # (the flag mirrors it), so take a tenant-2 admin instead.
    await env.tenant(2, "Beta")
    uid = await env.user("beta-admin", {2: "admin"})
    env.login_as(await env.session(uid, 2))
    for method, path in (("GET", "/api/tenants"), ("POST", "/api/tenants"),
                         ("POST", "/api/tenants/2/suspend"), ("PATCH", "/api/tenants/2"),
                         ("POST", "/api/tenants/2/delete"), ("POST", "/api/tenants/2/resume"),
                         ("POST", "/api/tenants/2/cancel-deletion")):
        r = await env.client.request(method, path, json={})
        assert r.status_code == 403, (method, path, r.status_code)


async def test_create_a_tenant_with_its_site_admin_and_name(env):
    r = await env.client.post("/api/tenants", json=_create_body(plan="professional"))
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "active" and t["plan"] == "professional" and t["slug"] == "acme-ag"
    tid = t["id"]
    async with env.sf() as s:
        with system_scope("test", audit=False):
            sites = (await s.execute(select(Site).where(Site.tenant_id == tid))).scalars().all()
            members = (await s.execute(select(TenantMembership).where(
                TenantMembership.tenant_id == tid))).scalars().all()
    assert [x.name for x in sites] == ["Default"]
    assert [m.role for m in members] == ["admin"]
    # The operator is no member: no access to the new tenant's data.
    r = await env.client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": str(tid)})
    assert r.status_code == 404
    # The new admin signs in and lands in the tenant, named after it.
    env.signed_out()
    r = await env.client.post("/api/auth/login",
                              json={"username": "acme-admin", "password": "Acme-Passw0rd!"})
    assert r.status_code == 200, r.text
    me = (await env.client.get("/api/auth/me")).json()
    assert me["tenant"]["id"] == tid and me["tenants"] == [
        {"id": tid, "name": "Acme AG", "role": "admin", "via": "membership"}]
    settings = (await env.client.get("/settings/json")).json()
    assert settings["site_name"] == "Acme AG"
    # A new host lands in the tenant's default site.
    r = await env.client.post("/api/v1/hosts", json={"name": "fw", "hostname": "10.0.0.1"})
    assert r.status_code in (200, 201), r.text
    async with env.sf() as s:
        with tenant_scope(tid):
            host = (await s.execute(select(PingHost))).scalars().one()
    assert host.site_id == sites[0].id
    # Audited: the operator's record (installation level) and the tenant's.
    rows = await env.audit("tenant.create")
    assert [(a.tenant_id, a.acting_via) for a in rows] == [(None, "operator"), (tid, "operator")]


async def test_existing_user_becomes_the_admin_and_validation(env):
    await env.user("shared", {1: "readonly"})
    r = await env.client.post("/api/tenants", json={"name": "Gamma", "admin_username": "shared"})
    assert r.status_code == 201, r.text
    for body, code in ((_create_body(name=""), 400), (_create_body(plan="gold"), 400),
                       (_create_body(slug="Not A Slug!"), 400),
                       (_create_body(admin_username=""), 400),
                       (_create_body(name="Gamma 2", slug="gamma"), 409),
                       (_create_body(admin_username="nobody", admin_password="weak"), 400)):
        r = await env.client.post("/api/tenants", json=body)
        assert r.status_code == code, (body, r.status_code, r.text)


PG_MODE = bool(os.environ.get("NODEGLOW_TEST_PG_TEMPLATE"))


async def test_the_second_tenant_needs_rls_and_the_clickhouse_rebuild(env, monkeypatch):
    if not PG_MODE:  # on PostgreSQL with RLS on (router mode) there is no RLS problem
        monkeypatch.delenv("NODEGLOW_TENANTS_WITHOUT_RLS")
        r = await env.client.post("/api/tenants", json=_create_body())
        assert r.status_code == 409
        assert "Row-level security" in r.json()["error"]
        assert "NODEGLOW_TENANTS_WITHOUT_RLS" in r.json()["error"]
        monkeypatch.setenv("NODEGLOW_TENANTS_WITHOUT_RLS", "1")
    with patch("services.clickhouse_rebuild.complete", new=AsyncMock(return_value=False)):
        r = await env.client.post("/api/tenants", json=_create_body())
        assert r.status_code == 409 and "rebuild" in r.json()["error"]
        problems = (await env.client.get("/api/tenants")).json()["creation_problems"]
        assert any("rebuild" in p for p in problems)
    with patch("services.clickhouse_rebuild.complete", new=AsyncMock(side_effect=OSError("down"))):
        r = await env.client.post("/api/tenants", json=_create_body())
        assert r.status_code == 409 and "cannot be checked" in r.json()["error"]
    assert (await env.client.post("/api/tenants", json=_create_body())).status_code == 201


async def test_rls_check_on_postgres_reads_the_runtime_state(env, monkeypatch):
    """The PostgreSQL branch: flag off → refused; RLS reported active → fine."""
    from nodeglow_ee.tenancy import lifecycle

    monkeypatch.delenv("NODEGLOW_TENANTS_WITHOUT_RLS")

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Conn:
        def __init__(self, status):
            self.status = status

        async def run_sync(self, fn):
            return self.status

    class _Db:
        def __init__(self, status):
            self._conn = _Conn(status)

        def get_bind(self):
            return _Bind()

        async def connection(self):
            return self._conn

    good = {"user": "nodeglow_app", "superuser": False, "bypassrls": False, "owns_tables": 0,
            "tables": 30, "enabled": 30, "forced": 30}
    monkeypatch.setattr("config.RLS_ENABLED", False)
    assert "NODEGLOW_RLS" in await lifecycle.rls_problem(_Db(good))
    monkeypatch.setattr("config.RLS_ENABLED", True)
    assert await lifecycle.rls_problem(_Db(good)) is None
    assert "not active" in await lifecycle.rls_problem(_Db({**good, "enabled": 29}))
    assert "superuser" in await lifecycle.rls_problem(_Db({**good, "superuser": True}))


@pytest.mark.skipif(not PG_MODE,
                    reason="PostgreSQL router mode only (as nodeglow_app with RLS on)")
async def test_on_postgres_with_rls_the_second_tenant_needs_no_override(env, monkeypatch):
    monkeypatch.delenv("NODEGLOW_TENANTS_WITHOUT_RLS")
    assert (await env.client.get("/api/tenants")).json()["creation_problems"] == []
    assert (await env.client.post("/api/tenants", json=_create_body())).status_code == 201


async def test_max_tenants_from_the_licence(env, ee_license):
    ee_license.set(issue_test_license(max_tenants=2))
    assert (await env.client.post("/api/tenants", json=_create_body())).status_code == 201
    r = await env.client.post("/api/tenants", json=_create_body(name="Two", admin_username="two",
                                                                 slug="two"))
    assert r.status_code == 409 and "allows 2 tenants" in r.json()["error"]
    usage = (await env.client.get("/api/tenants")).json()["usage"]
    assert (usage["count"], usage["max"]) == (2, 2)


async def test_feature_list_of_the_licence(env, ee_license):
    ee_license.set(issue_test_license(features=("ai_assistant",)))
    r = await env.client.post("/api/tenants", json=_create_body())
    assert r.status_code == 402 and "multi-tenancy" in r.json()["error"]
    feats = (await env.client.get("/api/v2/features")).json()
    assert feats["features"]["multi_tenancy"] is False and feats["installed"]["multi_tenancy"] is True


async def test_expired_licence_keeps_monitoring_and_blocks_admin(env, ee_license):
    await env.tenant(2, "Beta")
    uid = await env.user("beta-user", {2: "editor"})
    beta = await env.session(uid, 2)
    ee_license.set(issue_test_license(days=-30))  # past the 14-day grace
    assert (await env.client.post("/api/tenants", json=_create_body())).status_code == 402
    assert (await env.client.post("/api/tenants/2/suspend")).status_code == 402
    assert (await env.client.patch("/api/tenants/2", json={"plan": "enterprise"})).status_code == 402
    assert (await env.client.get("/api/tenants")).status_code == 200  # reading stays
    # Monitoring and the tenant's users are not affected.
    env.login_as(beta)
    assert (await env.client.get("/api/v1/hosts")).status_code == 200
    assert 2 in await tenancy.backend().active_tenant_ids()


async def test_suspend_and_resume(env):
    await env.tenant(2, "Beta")
    uid = await env.user("beta-user", {1: "readonly", 2: "editor"})
    beta = await env.session(uid, 2)
    r = await env.client.post("/api/tenants/2/suspend")
    assert r.status_code == 200 and r.json()["status"] == "suspended" and r.json()["suspended_at"]
    assert 2 not in await tenancy.backend().active_tenant_ids()
    async with env.sf() as s:
        sess = (await s.execute(select(Session).where(Session.user_id == uid))).scalar_one()
    assert sess.active_tenant_id is None  # falls back to the user's other tenant
    env.login_as(beta)
    assert (await env.client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "2"})).status_code == 404
    me = (await env.client.get("/api/auth/me")).json()
    assert me["tenant"]["id"] == 1 and [t["id"] for t in me["tenants"]] == [1]
    env.login_as(env.op_session)
    assert (await env.client.post("/api/tenants/2/suspend")).status_code == 409
    assert (await env.client.post("/api/tenants/2/resume")).json()["status"] == "active"
    env.login_as(beta)
    assert (await env.client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "2"})).status_code == 200
    rows = await env.audit("tenant.suspend")
    assert {a.tenant_id for a in rows} == {None, 2}


async def test_the_default_tenant_cannot_be_suspended_or_deleted(env):
    assert (await env.client.post("/api/tenants/1/suspend")).status_code == 409
    r = await env.client.post("/api/tenants/1/delete", json={"confirm": "NODEGLOW"})
    assert r.status_code == 409
    assert (await env.client.post("/api/tenants/99/suspend")).status_code == 404


async def test_deletion_needs_the_typed_name_and_suspends_for_30_days(env):
    await env.tenant(2, "Beta Corp")
    for confirm in (None, "", "beta corp", "Beta"):
        r = await env.client.post("/api/tenants/2/delete", json={"confirm": confirm})
        assert r.status_code == 400 and r.json()["code"] == "confirmation_mismatch"
    r = await env.client.post("/api/tenants/2/delete", json={"confirm": "Beta Corp"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "deleting"
    due = datetime.strptime(t["delete_after"], "%Y-%m-%dT%H:%M:%SZ")
    assert timedelta(days=29, hours=23) < due - datetime.utcnow() <= timedelta(days=30)
    assert 2 not in await tenancy.backend().active_tenant_ids()
    assert (await env.client.post("/api/tenants/2/delete", json={"confirm": "Beta Corp"})).status_code == 409
    assert (await env.client.post("/api/tenants/2/resume")).status_code == 409
    [installation, own] = await env.audit("tenant.delete_scheduled")
    assert installation.tenant_id is None and own.tenant_id == 2
    assert t["delete_after"] in installation.details


async def test_cancelling_a_deletion_works_without_a_licence(env, ee_license):
    await env.tenant(2, "Beta")
    assert (await env.client.post("/api/tenants/2/delete", json={"confirm": "Beta"})).status_code == 200
    ee_license.clear()
    r = await env.client.post("/api/tenants/2/cancel-deletion")
    assert r.status_code == 200 and r.json()["status"] == "suspended" and r.json()["delete_after"] is None
    assert (await env.client.post("/api/tenants/2/cancel-deletion")).status_code == 409


async def test_a_started_purge_cannot_be_cancelled(env):
    await env.tenant(2, "Beta", status="deleting")
    async with env.sf() as s:
        t = await s.get(Tenant, 2)
        t.delete_after, t.purge_started_at = datetime.utcnow(), datetime.utcnow()
        await s.commit()
    r = await env.client.post("/api/tenants/2/cancel-deletion")
    assert r.status_code == 409 and r.json()["code"] == "purge_started"


# ── plans ────────────────────────────────────────────────────────────────────

async def test_plan_caps_the_tenants_retention_settings(env):
    from tests.conftest import put_settings

    await env.tenant(2, "Beta", plan="professional")
    async with env.sf() as s:
        await put_settings(s, tenant_id=2, integration_retention_days="80",
                           incident_event_retention_days="0", template_retention_days="20")
    uid = await env.user("beta-admin", {2: "admin"})
    beta = await env.session(uid, 2)
    env.login_as(beta)
    caps = (await env.client.get("/settings/json")).json()["retention_caps"]
    assert caps["integration_retention_days"] == 90 and caps["incident_event_retention_days"] == 90

    # The operator moves the tenant to the 30-day tier: stored values above
    # it (and "keep forever") come down, values below stay.
    # Lowering stored values (the cleanup then deletes history for good)
    # needs an explicit confirmation; without it nothing changes.
    env.login_as(env.op_session)
    r = await env.client.patch("/api/tenants/2", json={"plan": "standard"})
    assert r.status_code == 409 and r.json()["code"] == "retention_would_lower", r.text
    assert r.json()["retention_lowered"] == {
        "integration_retention_days": {"from": 80, "to": 30},
        "incident_event_retention_days": {"from": 0, "to": 30},
    }
    async with env.sf() as s:
        assert (await s.get(Tenant, 2)).plan == "professional"
    assert await env.audit("tenant.update") == []
    r = await env.client.patch("/api/tenants/2", json={"plan": "standard",
                                                       "confirm_retention_lowering": True})
    assert r.status_code == 200 and r.json()["plan"] == "standard" and r.json()["retention_days"] == 30
    from models.settings import get_setting
    from services import tenant_jobs

    async with env.sf() as s:
        with tenant_scope(2):
            assert await get_setting(s, "integration_retention_days") == "30"
            assert await get_setting(s, "incident_event_retention_days") == "30"
            assert await get_setting(s, "template_retention_days") == "20"
            assert await tenant_jobs.retention_days(s, "integration_retention_days", 7) == 30
            assert await tenant_jobs.retention_cap(s, "template_retention_days") == 30
        # Tenant 1 has no plan: no plan cap.
        with tenant_scope(1):
            assert await tenant_jobs.retention_cap(s, "template_retention_days") is None
    [row_installation, row_tenant] = await env.audit("tenant.update")
    assert "retention_lowered" in row_tenant.details

    # The tenant admin can only choose within the plan.
    env.login_as(beta)
    r = await env.client.post("/settings/save", data={
        "site_name": "Beta", "integration_retention": "90", "incident_event_retention": "365",
        "ping_retention": "30", "proxmox_retention": "60",
    })
    assert r.status_code in (200, 302, 303), r.text
    async with env.sf() as s:
        with tenant_scope(2):
            assert await get_setting(s, "integration_retention_days") == "30"
            assert await get_setting(s, "incident_event_retention_days") == "30"
            assert await get_setting(s, "proxmox_retention_days") == "30"
    # The installation cap still applies when it is lower.
    async with env.sf() as s:
        await put_settings(s, integration_retention_days_max="14")
        with tenant_scope(2):
            assert await tenant_jobs.retention_cap(s, "integration_retention_days") == 14


async def test_rename_and_unknown_plan(env):
    await env.tenant(2, "Beta")
    r = await env.client.patch("/api/tenants/2", json={"name": "Beta AG"})
    assert r.status_code == 200 and r.json()["name"] == "Beta AG"
    assert (await env.client.patch("/api/tenants/2", json={"plan": "gold"})).status_code == 400
    assert (await env.client.patch("/api/tenants/2", json={"name": " "})).status_code == 400
    from models.settings import get_setting

    async with env.sf() as s:
        with tenant_scope(2):
            assert await get_setting(s, "site_name") == "Beta AG"


# ── login, switcher ──────────────────────────────────────────────────────────

async def test_login_with_several_tenants_starts_in_the_last_used(env):
    await env.tenant(2, "Beta")
    await env.tenant(3, "Gamma")
    uid = await env.user("multi", {2: "admin", 3: "readonly"})
    env.signed_out()
    login = {"username": "multi", "password": "Corr3ct-Horse!"}
    r = await env.client.post("/api/auth/login", json=login)
    assert r.status_code == 200, r.text  # the core refuses: several, none chosen
    assert (await env.client.get("/api/auth/me")).json()["tenant"]["id"] == 2  # lowest
    assert (await env.client.post("/api/auth/tenant", json={"tenant_id": 3})).status_code == 200
    env.signed_out()
    assert (await env.client.post("/api/auth/login", json=login)).status_code == 200
    assert (await env.client.get("/api/auth/me")).json()["tenant"]["id"] == 3  # last used
    async with env.sf() as s:
        # The first session switched to 3, the second started there.
        assert [x.active_tenant_id for x in (await s.execute(
            select(Session).where(Session.user_id == uid))).scalars()] == [3, 3]


async def test_switcher_lists_only_the_users_tenants(env):
    """Isolation of the switcher: /api/auth/me names the user's tenants and
    nothing else; switching into another tenant is 404."""
    await env.tenant(2, "Beta")
    await env.tenant(3, "CANARY-C")
    uid = await env.user("ab", {1: "admin", 2: "readonly"})
    env.login_as(await env.session(uid, 1))
    r = await env.client.get("/api/auth/me")
    tenants = r.json()["tenants"]
    assert [(t["id"], t["role"], t["via"]) for t in tenants] == [
        (1, "admin", "membership"), (2, "readonly", "membership")]
    assert tenants[1]["name"] == "Beta"
    assert "CANARY-C" not in r.text
    assert (await env.client.post("/api/auth/tenant", json={"tenant_id": 3})).status_code == 404
    r = await env.client.post("/api/auth/tenant", json={"tenant_id": 2})
    assert r.json() == {"ok": True, "tenant_id": 2, "role": "readonly", "via": "membership"}
    assert (await env.client.get("/api/auth/me")).json()["user"]["role"] == "readonly"

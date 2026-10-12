"""Phase 9 hardening: what a second tenant makes reachable must stay inside it.

* a tenant admin cannot reset the credentials of a user who also reaches
  other tenants, the provider or the installation (users are global);
* the installation's AI provider, endpoint and keys are the operator's;
* support access manages no identities (users, passwords, API keys, install
  tokens) — nothing it does outlives the grant as a login;
* a role from support access never makes anyone an installation operator;
* the default tenant gets no plan, and a plan that lowers stored retention
  needs a confirmation;
* the retention plan cap fails closed in the settings form;
* without any support grant, resolving access runs no grant query.

Runs on SQLite here and in the PostgreSQL router mode (``ROUTER_TESTS_ON_PG``).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models.api_key import ApiKey
from models.settings import User
from models.tenant import ProviderMembership, Tenant, TenantMembership
from services.tenancy import system_scope, tenant_scope
from tenancy_helpers import PW, tenancy_env

NEW_PW = "N3w-Passw0rd-Long!"


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setenv("NODEGLOW_TENANTS_WITHOUT_RLS", "1")
    with patch("services.clickhouse_rebuild.complete", new=AsyncMock(return_value=True)):
        async with tenancy_env() as e:
            await e.tenant(2, "Acme")
            e.op = await e.user("operator", {1: "admin"}, is_operator=True)
            e.op_session = await e.session(e.op, 1)
            e.acme_admin = await e.user("acme-admin", {2: "admin"})
            e.acme_session = await e.session(e.acme_admin, 2)
            yield e


async def _login_ok(env, username: str, password: str) -> bool:
    env.signed_out()
    r = await env.client.post("/api/auth/login", json={"username": username, "password": password})
    return r.status_code == 200


async def _grant(env, role: str, tenant_session: str, tenant_id: int, hours: int = 1) -> dict:
    env.login_as(tenant_session)
    r = await env.client.post("/api/support-access", json={"reason": "ticket", "hours": hours,
                                                           "role": role},
                              headers={"X-Nodeglow-Tenant": str(tenant_id)})
    assert r.status_code == 201, r.text
    return r.json()


# ── 1. Shared identities ─────────────────────────────────────────────────────

async def test_tenant_admin_cannot_reset_a_shared_users_password(env):
    # The operator is also a member of Acme (an existing user as Acme's admin).
    async with env.sf() as s:
        s.add(TenantMembership(user_id=env.op, tenant_id=2, role="admin"))
        await s.commit()
    env.login_as(env.acme_session)
    r = await env.client.patch(f"/api/users/{env.op}", json={"password": NEW_PW})
    assert r.status_code == 403 and r.json()["code"] == "foreign_identity", r.text
    r = await env.client.post(f"/users/{env.op}/password", data={"password": NEW_PW})
    assert r.status_code == 403
    assert await _login_ok(env, "operator", PW)
    assert not await _login_ok(env, "operator", NEW_PW)


@pytest.mark.parametrize("reach", ["other_tenant", "provider", "operator_flag"])
async def test_every_kind_of_outside_access_protects_the_credentials(env, reach):
    uid = await env.user("shared", {2: "editor"}, is_operator=(reach == "operator_flag"))
    async with env.sf() as s:
        if reach == "other_tenant":
            await env.tenant(3, "Gamma")
            s.add(TenantMembership(user_id=uid, tenant_id=3, role="readonly"))
        elif reach == "provider":
            s.add(ProviderMembership(user_id=uid, provider_id=1, role="msp_operator"))
        await s.commit()
    env.login_as(env.acme_session)
    r = await env.client.patch(f"/api/users/{uid}", json={"password": NEW_PW})
    assert r.status_code == 403, (reach, r.text)
    # The role in Acme is Acme's to change.
    r = await env.client.patch(f"/api/users/{uid}", json={"role": "readonly"})
    assert r.status_code == 200, r.text


async def test_tenant_admin_still_resets_its_own_users(env):
    uid = await env.user("acme-tech", {2: "editor"})
    env.login_as(env.acme_session)
    r = await env.client.patch(f"/api/users/{uid}", json={"password": NEW_PW})
    assert r.status_code == 200, r.text
    assert await _login_ok(env, "acme-tech", NEW_PW)


async def test_removing_a_provider_member_from_a_tenant_keeps_the_account(env):
    uid = await env.user("msp-tech", {2: "readonly"})
    async with env.sf() as s:
        s.add(ProviderMembership(user_id=uid, provider_id=1, role="msp_operator"))
        await s.commit()
    env.login_as(env.acme_session)
    r = await env.client.delete(f"/api/users/{uid}")
    assert r.status_code == 200, r.text
    async with env.sf() as s:
        assert await s.get(User, uid) is not None
        assert await s.get(TenantMembership, (uid, 2)) is None


async def test_operator_resets_any_password(env):
    async with env.sf() as s:
        s.add(TenantMembership(user_id=env.acme_admin, tenant_id=1, role="readonly"))
        await s.commit()
    env.login_as(env.op_session)
    r = await env.client.patch(f"/api/users/{env.acme_admin}", json={"password": NEW_PW})
    assert r.status_code == 200, r.text


# ── 2. The installation's AI provider ────────────────────────────────────────

async def _installation_setting(env, key: str):
    from models.settings import get_setting

    async with env.sf() as s:
        with tenant_scope(1):
            return await get_setting(s, key, "")


async def test_tenant_admin_cannot_change_the_installation_ai_provider(env):
    env.login_as(env.acme_session)
    cfg = (await env.client.get("/settings/ai/config")).json()
    assert cfg["provider_editable"] is False
    for form in ({"claude_api_key": "sk-ant-attacker"},
                 {"ai_provider": "openai_compatible", "ai_openai_base_url": "https://evil.example/v1",
                  "ai_openai_model": "x"},
                 {"ai_openai_api_key": "attacker"},
                 {"ai_anthropic_model": "claude-other"}):
        r = await env.client.post("/settings/ai/save", data=form, headers={"accept": "application/json"})
        assert r.status_code == 403 and r.json()["code"] == "operator_required", (form, r.text)
    assert await _installation_setting(env, "claude_api_key") == ""
    assert await _installation_setting(env, "ai_provider") in ("", "anthropic")
    assert await _installation_setting(env, "ai_openai_base_url") == ""
    # The tenant's own choices (redaction) and the unchanged provider values
    # the form sends back are fine.
    r = await env.client.post("/settings/ai/save", headers={"accept": "application/json"}, data={
        "ai_provider": cfg["ai_provider"], "ai_anthropic_model": cfg["ai_anthropic_model"],
        "ai_redact_hostnames": "1",
    })
    assert r.status_code == 200, r.text
    async with env.sf() as s:
        from models.settings import get_setting

        with tenant_scope(2):
            assert await get_setting(s, "ai_redact_hostnames") == "1"
    assert await _installation_setting(env, "ai_anthropic_model") == ""


async def test_operator_sets_the_installation_ai_key(env):
    env.login_as(env.op_session)
    assert (await env.client.get("/settings/ai/config")).json()["provider_editable"] is True
    r = await env.client.post("/settings/ai/save", data={"claude_api_key": "sk-ant-operator"},
                              headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    assert await _installation_setting(env, "claude_api_key") != ""


# ── 3. Support access manages no identities ──────────────────────────────────

ACME = {"X-Nodeglow-Tenant": "2"}


async def test_support_access_cannot_manage_identities(env):
    await _grant(env, "admin", env.acme_session, 2)
    env.login_as(env.op_session)
    me = (await env.client.get("/api/auth/me", headers=ACME)).json()
    assert me["tenant"] == {"id": 2, "via": "support"} and me["user"]["role"] == "admin"
    calls = [
        ("POST", "/api/users", {"json": {"username": "backdoor", "password": NEW_PW, "role": "admin"}}),
        ("POST", "/users/add", {"data": {"username": "backdoor2", "password": NEW_PW, "role": "admin"}}),
        ("PATCH", f"/api/users/{env.acme_admin}", {"json": {"password": NEW_PW}}),
        ("PATCH", f"/api/users/{env.acme_admin}", {"json": {"role": "readonly"}}),
        ("POST", f"/users/{env.acme_admin}/password", {"data": {"password": NEW_PW}}),
        ("POST", f"/users/{env.acme_admin}/role", {"data": {"role": "readonly"}}),
        ("DELETE", f"/api/users/{env.acme_admin}", {}),
        ("POST", f"/users/{env.acme_admin}/delete", {}),
        ("POST", "/settings/api-keys/create", {"json": {"name": "k", "role": "admin"}}),
        ("POST", "/api/v1/keys", {"json": {"name": "k", "role": "admin"}}),
        ("POST", "/api/agents/install-tokens", {"json": {"note": "x"}}),
    ]
    for method, path, kw in calls:
        r = await env.client.request(method, path, headers=ACME, **kw)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    async with env.sf() as s:
        names = set((await s.execute(select(User.username))).scalars().all())
        with system_scope("test: keys", audit=False):
            keys = (await s.execute(select(ApiKey).where(ApiKey.tenant_id == 2))).scalars().all()
        assert (await s.get(TenantMembership, (env.acme_admin, 2))).role == "admin"
    assert "backdoor" not in names and "backdoor2" not in names and keys == []
    assert await _login_ok(env, "acme-admin", PW)
    # Reading is still possible (the support admin sees who is there).
    env.login_as(env.op_session)
    assert (await env.client.get("/api/users", headers=ACME)).status_code == 200


# Every identity- and credential-managing route, by its template. The
# coverage test below fails when a new one appears that is not refused to
# support access (and listed here).
IAM_ROUTES = {
    ("POST", "/api/users"), ("POST", "/users/add"),
    ("PATCH", "/api/users/{user_id}"), ("DELETE", "/api/users/{user_id}"),
    ("POST", "/users/{user_id}/password"), ("POST", "/users/{user_id}/role"),
    ("POST", "/users/{user_id}/delete"),
    ("POST", "/settings/api-keys/create"), ("DELETE", "/settings/api-keys/{key_id}"),
    ("POST", "/api/v1/keys"), ("DELETE", "/api/v1/keys/{key_id}"),
    ("POST", "/api/agents/install-tokens"), ("DELETE", "/api/agents/install-tokens/{token_id}"),
}
# Mutating routes the patterns match that are not tenant IAM.
NOT_IAM = {
    ("POST", "/users/me/password"),  # one's own password, with the current one
}


def test_every_iam_route_is_covered():
    import re

    from main import app

    pattern = re.compile(r"(^/api/users|^/users|api-keys|^/api/v1/keys|install-tokens)")
    found = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or ():
            if method in ("POST", "PATCH", "PUT", "DELETE") and pattern.search(path):
                found.add((method, path))
    assert found - NOT_IAM == IAM_ROUTES


async def test_support_access_cannot_revoke_keys_or_tokens(env):
    await _grant(env, "admin", env.acme_session, 2)
    env.login_as(env.op_session)
    for method, path in (("DELETE", "/settings/api-keys/1"), ("DELETE", "/api/v1/keys/1"),
                         ("DELETE", "/api/agents/install-tokens/1")):
        r = await env.client.request(method, path, headers=ACME)
        assert r.status_code == 403, (method, path, r.status_code)


async def test_the_tenants_own_admin_still_manages_identities(env):
    await _grant(env, "admin", env.acme_session, 2)
    env.login_as(env.acme_session)
    r = await env.client.post("/api/users", json={"username": "acme-new", "password": NEW_PW})
    assert r.status_code == 200, r.text
    r = await env.client.post("/settings/api-keys/create", json={"name": "k", "role": "readonly"})
    assert r.status_code == 200, r.text


# ── 4. Support access never makes an operator ────────────────────────────────

async def test_admin_grant_on_the_default_tenant_makes_no_operator(env):
    msp = await env.user("msp-tech", {})
    async with env.sf() as s:
        s.add(ProviderMembership(user_id=msp, provider_id=1, role="msp_operator"))
        await s.commit()
    await _grant(env, "admin", env.op_session, 1)
    env.login_as(await env.session(msp, 1))
    me = (await env.client.get("/api/auth/me")).json()
    assert me["tenant"] == {"id": 1, "via": "support"} and me["user"]["role"] == "admin"
    assert me["user"]["is_operator"] is False
    assert (await env.client.get("/api/tenants")).status_code == 403
    assert (await env.client.post("/api/tenants", json={"name": "X"})).status_code == 403
    assert (await env.client.post("/settings/ldap/save", data={})).status_code == 403


def test_is_operator_ignores_support_roles():
    from nodeglow_ee.tenancy.backend import MultiTenancy
    from services.tenancy import SingleTenancy

    class U:
        is_operator = False

    for b in (SingleTenancy(), MultiTenancy()):
        assert b.is_operator(U(), "admin", 1) is True
        assert b.is_operator(U(), "admin", 1, via="membership") is True
        assert b.is_operator(U(), "admin", 1, via="support") is False
    U.is_operator = True
    assert MultiTenancy().is_operator(U(), "readonly", 2, via="support") is True


# ── 5. The default tenant and plan changes ───────────────────────────────────

async def test_default_tenant_gets_no_plan(env):
    env.login_as(env.op_session)
    r = await env.client.patch("/api/tenants/1", json={"plan": "standard"})
    assert r.status_code == 409 and r.json()["code"] == "default_tenant", r.text
    async with env.sf() as s:
        assert (await s.get(Tenant, 1)).plan is None
    # "No plan" is accepted (and is a no-op here); renaming still works.
    r = await env.client.patch("/api/tenants/1", json={"plan": None, "name": "Production"})
    assert r.status_code == 200 and r.json()["plan"] is None and r.json()["name"] == "Production"


async def test_a_plan_can_be_removed_again(env):
    env.login_as(env.op_session)
    r = await env.client.patch("/api/tenants/2", json={"plan": None})
    assert r.status_code == 200 and r.json()["plan"] is None, r.text
    r = await env.client.patch("/api/tenants/2", json={"plan": "professional"})
    assert r.status_code == 200 and r.json()["plan"] == "professional", r.text


# ── 6. The plan cap fails closed in the form ─────────────────────────────────

async def test_settings_save_refuses_when_the_plan_cannot_be_read(env, monkeypatch):
    import extensions
    from models.settings import get_setting
    from services import tenant_jobs

    async def broken(db, tenant_id, key):
        raise RuntimeError("plugin bug")

    monkeypatch.setattr(extensions.registry, "retention_policy", broken)
    env.login_as(env.acme_session)
    r = await env.client.post("/settings/save", data={
        "site_name": "Changed", "integration_retention": "90", "incident_event_retention": "365",
        "ping_retention": "30", "proxmox_retention": "60",
    })
    assert r.status_code == 503 and r.json()["code"] == "retention_policy_unavailable", r.text
    async with env.sf() as s:
        with tenant_scope(2):
            assert await get_setting(s, "incident_event_retention_days", None) is None
            assert await get_setting(s, "site_name", None) != "Changed"
            # The cleanup keeps the stored value (within the installation cap).
            from tests.conftest import put_settings

            await put_settings(s, tenant_id=2, integration_retention_days="20")
            assert await tenant_jobs.retention_days(s, "integration_retention_days", 7) == 20
            with pytest.raises(tenant_jobs.RetentionPolicyError):
                await tenant_jobs.retention_cap(s, "integration_retention_days")


async def test_retention_cap_without_a_tenant_raises(env):
    from services import tenant_jobs
    from services.tenancy import TenantContextError

    # E.g. a job that forgot its per-tenant scope: an error, never "no plan".
    async with env.sf() as s:
        with system_scope("test: no single tenant", audit=False):
            with pytest.raises(TenantContextError):
                await tenant_jobs.retention_cap(s, "integration_retention_days")
            with pytest.raises(TenantContextError):
                await tenant_jobs.retention_days(s, "integration_retention_days", 7)


# ── 7. No grant query while there is no grant ────────────────────────────────

async def test_no_grant_lookup_per_request_without_grants(env):
    from sqlalchemy import event

    from nodeglow_ee.tenancy import support

    engine = env.sf.kw["bind"].sync_engine
    statements: list[str] = []

    def spy(conn, cursor, statement, *args):
        if "support_grants" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        support.invalidate_grants()
        env.login_as(env.acme_session)
        for _ in range(5):
            assert (await env.client.get("/api/auth/me")).status_code == 200
            assert (await env.client.get("/api/v1/hosts")).status_code == 200
        assert len(statements) == 1, statements  # one load, then the cached (empty) list
        # A grant made in this process is seen at once.
        await _grant(env, "readonly", env.acme_session, 2)
        env.login_as(env.op_session)
        me = (await env.client.get("/api/auth/me")).json()
        assert {t["id"]: t["via"] for t in me["tenants"]} == {1: "membership", 2: "support"}
    finally:
        event.remove(engine, "before_cursor_execute", spy)

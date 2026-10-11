"""Regressions from the review of the phase 3/4 branch."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from sqlalchemy import select

from models import Tenant
from models.settings import Session, Setting, User, _hash_token
from models.tenant import TenantMembership, TenantSetting
from services import tenancy
from services.tenancy import system_scope

PW = "Corr3ct-Horse!"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from services import shared_state

    shared_state.reset()
    yield
    shared_state.reset()


async def _user(sf, username, memberships, role="admin", is_operator=None):
    async with sf() as s:
        u = User(username=username, role=role, auth_source="local",
                 is_operator=(role == "admin") if is_operator is None else is_operator,
                 password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode())
        s.add(u)
        await s.flush()
        for tid, r in memberships.items():
            s.add(TenantMembership(user_id=u.id, tenant_id=tid, role=r))
        await s.commit()
        return u.id


async def _session(sf, uid, tenant_id):
    token = secrets.token_hex(32)
    async with sf() as s:
        s.add(Session(token=_hash_token(token), user_id=uid, active_tenant_id=tenant_id,
                      expires_at=datetime.utcnow() + timedelta(days=1)))
        await s.commit()
    return token


async def _tenant_b(sf):
    async with sf() as s:
        with system_scope("test: tenant B", audit=False):
            s.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
            await s.commit()


# ── 2) a demoted admin is no longer the operator ────────────────────────────

async def test_demoted_admin_loses_operator_rights(auth_client):
    client, sf = auth_client
    boss = await _user(sf, "boss", {1: "admin"})
    ex = await _user(sf, "ex", {1: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, boss, 1))
    assert (await client.patch(f"/api/users/{ex}", json={"role": "editor"})).status_code == 200
    async with sf() as s:
        u = await s.get(User, ex)
        assert u.role == "editor" and u.is_operator is False
    client.cookies.set("nodeglow_session", await _session(sf, ex, 1))
    with patch("routers.update._sidecar_post", new_callable=AsyncMock, return_value=(202, {"ok": True})):
        assert (await client.post("/api/update/apply")).status_code == 403
    assert (await client.post("/settings/ldap/save", data={"ldap_enabled": "0"})).status_code == 403
    # Promoted again: operator again.
    client.cookies.set("nodeglow_session", await _session(sf, boss, 1))
    assert (await client.patch(f"/api/users/{ex}", json={"role": "admin"})).status_code == 200
    async with sf() as s:
        assert (await s.get(User, ex)).is_operator is True


async def test_a_stale_operator_flag_grants_nothing(auth_client):
    """The flag is ee's; in the core only the default tenant's admin role counts."""
    client, sf = auth_client
    uid = await _user(sf, "stale", {1: "editor"}, role="editor", is_operator=True)
    client.cookies.set("nodeglow_session", await _session(sf, uid, 1))
    assert (await client.post("/settings/ldap/save", data={"ldap_enabled": "0"})).status_code == 403


async def test_admin_of_another_tenant_is_not_the_operator(auth_client):
    client, sf = auth_client
    await _tenant_b(sf)
    uid = await _user(sf, "b-admin", {2: "admin"}, role="readonly", is_operator=False)
    client.cookies.set("nodeglow_session", await _session(sf, uid, 2))
    assert (await client.get("/api/v1/hosts")).status_code == 200
    assert (await client.post("/settings/ldap/save", data={"ldap_enabled": "0"})).status_code == 403


# ── 1/4) restoring data from before 042/043 ─────────────────────────────────

async def test_users_role_wins_for_pre_043_data(db):
    from services.memberships import sync_from_users_role

    db.add_all([User(id=10, username="a", password_hash="x", role="readonly"),
                User(id=11, username="b", password_hash="x", role="admin"),
                User(id=12, username="c", password_hash="x", role="editor")])
    db.add(TenantMembership(user_id=10, tenant_id=1, role="admin"))  # stale 039 copy
    db.add(TenantMembership(user_id=11, tenant_id=1, role="admin"))
    await db.commit()
    assert await sync_from_users_role(db) == 2  # 10 changed, 12 created
    await db.commit()
    rows = (await db.execute(select(TenantMembership.user_id, TenantMembership.role)
                             .order_by(TenantMembership.user_id))).all()
    assert [tuple(r) for r in rows] == [(10, "readonly"), (11, "admin"), (12, "editor")]
    ops = (await db.execute(select(User.id).where(User.is_operator).order_by(User.id))).scalars().all()
    assert ops == [11]


async def test_pre_042_restore_takes_the_backups_settings(db):
    from models.settings import adopt_legacy_tenant_settings, get_setting

    # Current tenant settings, then the restored (pre-split) rows land in `settings`.
    db.add(TenantSetting(key="site_name", value="Current"))
    db.add(TenantSetting(key="notify_enabled", value="1"))
    await db.commit()
    db.add(Setting(key="site_name", value="Restored"))
    db.add(Setting(key="syslog_port", value="1514"))
    await db.commit()
    assert await adopt_legacy_tenant_settings(db, 1, replace=True)
    await db.commit()
    assert await get_setting(db, "site_name") == "Restored"
    assert await get_setting(db, "notify_enabled", "0") == "0"  # not in the backup: gone
    assert await db.get(Setting, "site_name") is None
    assert (await db.get(Setting, "syslog_port")).value == "1514"  # installation key stays


# ── 5) system status sees the tenant's channels ─────────────────────────────

async def test_system_status_reports_tenant_channels():
    from tests.conftest import put_settings
    from tests.test_routers.conftest import make_client

    async with make_client() as (client, sf):
        async with sf() as s:
            await put_settings(s, telegram_bot_token="t", telegram_chat_id="c", ntfy_enabled="1",
                               ntfy_topic="alerts")
        data = (await client.get("/api/system/status")).json()
    ch = data["operational"]["notification_channels"]
    assert ch["telegram"] is True and ch["ntfy"] is True and ch["discord"] is False


# ── 6) long-lived streams re-check access; caches are LRU ───────────────────

async def test_revalidate_access_notices_revocation_and_switch(auth_client):
    client, sf = auth_client
    await _tenant_b(sf)
    uid = await _user(sf, "watcher", {1: "admin", 2: "admin"})
    token = await _session(sf, uid, 1)

    class _Conn:
        def __init__(self):
            self.headers, self.query_params = {}, {}
            self.scope = {"type": "websocket"}
            self.cookies = {"nodeglow_session": token}
            self.state = type("S", (), {})()

    conn = _Conn()
    assert (await tenancy.revalidate_access(conn, 1)).role == "admin"
    async with sf() as s:  # tenant switch in another tab
        (await s.get(Session, _hash_token(token))).active_tenant_id = 2
        await s.commit()
    assert await tenancy.revalidate_access(conn, 1) is None
    assert (await tenancy.revalidate_access(conn, 2)).tenant_id == 2
    async with sf() as s:  # membership revoked
        await s.delete(await s.get(TenantMembership, (uid, 2)))
        await s.commit()
    assert await tenancy.revalidate_access(conn, 2) is None
    async with sf() as s:  # logout
        await s.delete(await s.get(Session, _hash_token(token)))
        await s.commit()
    assert await tenancy.revalidate_access(conn, 1) is None


async def test_syslog_stream_ends_when_access_changes(monkeypatch):
    from tests.test_routers.conftest import make_client

    monkeypatch.setattr(tenancy, "STREAM_REVALIDATE_SECONDS", 0.0)
    async with make_client() as (client, _sf):
        with patch.object(tenancy, "revalidate_access", new=AsyncMock(return_value=None)) as rv:
            r = await client.get("/syslog/stream")
    assert r.status_code == 200 and rv.await_count == 1


def test_topology_and_dashboard_caches_keep_more_than_a_handful_of_tenants():
    from services import dashboard_v2, topology

    assert topology.TOPOLOGY_CACHE_MAX >= 256 and dashboard_v2.TTL_CACHE_MAX >= 1024

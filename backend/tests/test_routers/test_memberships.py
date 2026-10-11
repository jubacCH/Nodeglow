"""Memberships are written by every path that creates or changes a user
(setup wizard, users API, LDAP login, backup restore) — phase 4."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models.settings import User
from models.tenant import Tenant, TenantMembership
from services import memberships as ms
from services.ldap_auth import LdapUser
from services.tenancy import system_scope


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from services import shared_state

    shared_state.reset()
    yield
    shared_state.reset()


async def _tenant_b(db):
    with system_scope("test: tenant B", audit=False):
        db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
        await db.commit()


async def test_grant_mirrors_users_role_only_for_the_default_tenant(db):
    await _tenant_b(db)
    u = User(username="u", password_hash="x", role="readonly")
    db.add(u)
    await db.flush()
    await ms.grant(db, u, 2, "admin")
    assert u.role == "readonly"  # tenant B's role is not mirrored
    await ms.grant(db, u, 1, "editor")
    assert u.role == "editor"
    await db.commit()
    assert await ms.memberships_for(db, u.id) == {1: "editor", 2: "admin"}
    # Only active tenants count.
    (await db.get(Tenant, 2)).status = "suspended"
    await db.commit()
    assert await ms.memberships_for(db, u.id) == {1: "editor"}
    assert await ms.revoke(db, u.id, 1) is True
    assert await ms.revoke(db, u.id, 1) is False


async def test_backfill_gives_memberless_users_their_old_role(db):
    db.add_all([User(id=10, username="a", password_hash="x", role="editor"),
                User(id=11, username="b", password_hash="x", role=None),
                User(id=12, username="c", password_hash="x", role="readonly")])
    db.add(TenantMembership(user_id=12, tenant_id=1, role="admin"))
    await db.commit()
    assert await ms.backfill_memberships(db) == 2
    await db.commit()
    rows = (await db.execute(select(TenantMembership.user_id, TenantMembership.role)
                             .order_by(TenantMembership.user_id))).all()
    assert [tuple(r) for r in rows] == [(10, "editor"), (11, "admin"), (12, "admin")]
    assert await ms.backfill_memberships(db) == 0


async def test_ldap_login_maintains_the_membership(db):
    from models.base import encrypt_value
    from models.settings import set_setting
    from routers.auth import _try_ldap_login

    await set_setting(db, "ldap_enabled", "1")
    await set_setting(db, "ldap_server", "ldaps://dc.lan")
    await set_setting(db, "ldap_bind_password", encrypt_value("svc"))
    fake = AsyncMock(return_value=LdapUser(username="zoe", display_name="Zoe", role="editor"))
    with patch("services.ldap_auth.authenticate_ldap", new=fake):
        user, created = await _try_ldap_login(db, "zoe", "pw")
    await db.commit()
    assert created and (await db.get(TenantMembership, (user.id, 1))).role == "editor"
    fake.return_value = LdapUser(username="zoe", display_name="Zoe", role="admin")
    with patch("services.ldap_auth.authenticate_ldap", new=fake):
        user, created = await _try_ldap_login(db, "zoe", "pw")
    await db.commit()
    assert not created and (await db.get(TenantMembership, (user.id, 1))).role == "admin"
    assert user.role == "admin"


async def test_setup_wizard_creates_the_operator_and_its_membership(auth_client):
    from tests.conftest import put_settings

    client, sf = auth_client
    async with sf() as s:
        await put_settings(s, setup_complete="false")
    r = await client.post("/setup/complete", data={
        "site_name": "ACME", "username": "root", "password": "Very-Str0ng-Passw0rd!",
    })
    assert r.status_code == 303, r.text
    async with sf() as s:
        user = (await s.execute(select(User).where(User.username == "root"))).scalar_one()
        assert user.is_operator is True and user.role == "admin"
        assert (await s.get(TenantMembership, (user.id, 1))).role == "admin"
        assert (await s.get(Tenant, 1)).name == "ACME"
    r = await client.post("/api/auth/login", json={"username": "root", "password": "Very-Str0ng-Passw0rd!"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"

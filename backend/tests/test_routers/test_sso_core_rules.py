"""Core rules for single sign-on users (schema 052; the sign-in itself is ee).

* Usernames ending in "#<digits>" are reserved for single sign-on users
  ("jdoe@acme.ch#7"): local accounts and directory logins of that shape are
  refused, so the namespaces cannot collide.
* A single sign-on user has no password here: the password login refuses
  them even if somebody set a hash.
"""
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest

import services.shared_state as ss
from models.identity import is_reserved_username

PW = "Corr3ct-Horse!"


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


@pytest.mark.parametrize("name,reserved", [
    ("jdoe@acme.ch#7", True), ("x#123", True), (" padded#4 ", True),
    ("admin", False), ("jdoe#", False), ("team#a1", False), ("#7x", False), ("", False),
])
def test_reserved_shape(name, reserved):
    assert is_reserved_username(name) is reserved


async def test_users_api_refuses_reserved_names(client):
    c = client
    r = await c.post("/api/users", json={"username": "jdoe@acme.ch#7", "password": PW, "role": "readonly"})
    assert r.status_code == 400 and "reserved" in r.json()["error"]
    r = await c.post("/users/add", data={"username": "x#3", "password": PW, "role": "readonly"})
    assert r.status_code == 400
    r = await c.post("/api/users", json={"username": "jdoe@acme.ch", "password": PW, "role": "readonly"})
    assert r.status_code == 200, r.text


async def _seed(sf, username: str, auth_source: str) -> None:
    from database import User
    from models.tenant import TenantMembership

    async with sf() as db:
        user = User(username=username, password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode(),
                    role="admin", auth_source=auth_source)
        db.add(user)
        await db.flush()
        db.add(TenantMembership(user_id=user.id, tenant_id=1, role="admin"))
        await db.commit()


async def test_password_login_refuses_single_sign_on_users(auth_client):
    client, sf = auth_client
    await _seed(sf, "jdoe@acme.ch#7", "oidc")
    r = await client.post("/api/auth/login", json={"username": "jdoe@acme.ch#7", "password": PW})
    assert r.status_code == 401 and "nodeglow_session" not in r.cookies
    await _seed(sf, "local-admin", "local")
    r = await client.post("/api/auth/login", json={"username": "local-admin", "password": PW})
    assert r.status_code == 200


async def test_a_directory_login_of_the_reserved_shape_never_reaches_the_directory(auth_client):
    from routers import auth as auth_mod

    _client, sf = auth_client
    directory = AsyncMock(side_effect=AssertionError("the directory must not be asked"))
    config = AsyncMock(return_value=type("Cfg", (), {"server": "ldap://dc"})())
    with patch.object(auth_mod, "_get_ldap_config", config), \
            patch("services.ldap_auth.authenticate_ldap", directory):
        async with sf() as db:
            assert await auth_mod._try_ldap_login(db, "jdoe@acme.ch#7", PW) == (None, False)
    directory.assert_not_called()

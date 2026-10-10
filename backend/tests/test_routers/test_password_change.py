"""Changing one's own password requires the current one.

Both self-service paths accepted a new password with nothing but a valid
session — so a stolen session cookie or an unlocked screen was enough to take
the account over permanently. An admin resetting ANOTHER user's password
still needs no old password.
"""
import bcrypt
import pytest

from tests.test_routers.conftest import make_client


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


class Me:
    id = 1
    username = "admin"
    role = "admin"


class Viewer:
    id = 3
    username = "viewer"
    role = "readonly"


async def _seed(sf):
    from database import User
    async with sf() as db:
        db.add(User(id=1, username="admin", password_hash=_hash("Old-Passw0rd!"), role="admin"))
        db.add(User(id=2, username="bob", password_hash=_hash("Bob-Passw0rd!"), role="editor"))
        db.add(User(id=3, username="viewer", password_hash=_hash("View-Passw0rd!"), role="readonly"))
        await db.commit()


async def _hash_of(sf, uid):
    from database import User
    async with sf() as db:
        return (await db.get(User, uid)).password_hash


NEW = "Brand-New-Passw0rd!"


@pytest.fixture
async def me():
    async with make_client(fake_user=Me()) as (ac, sf):
        await _seed(sf)
        yield ac, sf


async def test_api_self_change_without_current_password_is_refused(me):
    ac, sf = me
    before = await _hash_of(sf, 1)
    resp = await ac.patch("/api/users/1", json={"password": NEW})
    assert resp.status_code == 403
    assert resp.json()["code"] == "current_password_required"
    assert await _hash_of(sf, 1) == before


async def test_api_self_change_with_wrong_current_password_is_refused(me):
    ac, sf = me
    resp = await ac.patch("/api/users/1", json={"password": NEW, "current_password": "nope"})
    assert resp.status_code == 403


async def test_api_self_change_with_current_password_works(me):
    ac, sf = me
    resp = await ac.patch("/api/users/1", json={"password": NEW, "current_password": "Old-Passw0rd!"})
    assert resp.status_code == 200, resp.text
    assert bcrypt.checkpw(NEW.encode(), (await _hash_of(sf, 1)).encode())


async def test_admin_resets_other_user_without_old_password(me):
    ac, sf = me
    resp = await ac.patch("/api/users/2", json={"password": NEW})
    assert resp.status_code == 200, resp.text
    assert bcrypt.checkpw(NEW.encode(), (await _hash_of(sf, 2)).encode())


async def test_form_self_change_requires_current_password():
    async with make_client(fake_user=Viewer()) as (ac, sf):
        await _seed(sf)
        before = await _hash_of(sf, 3)
        resp = await ac.post("/users/me/password", data={"password": NEW}, follow_redirects=False)
        assert resp.status_code == 403
        assert await _hash_of(sf, 3) == before

        resp = await ac.post("/users/me/password",
                             data={"password": NEW, "current_password": "View-Passw0rd!"},
                             follow_redirects=False)
        assert resp.status_code == 303, resp.text  # readonly may change their own
        assert bcrypt.checkpw(NEW.encode(), (await _hash_of(sf, 3)).encode())

"""The per-username lockout can no longer lock the real user out.

Five wrong passwords for "admin" every 15 minutes used to keep the admin out
for good — from every browser. Now unknown clients are soft-locked, while a
browser that signed in to the account before (device cookie) still gets in.
"""
import bcrypt
import pytest

import services.shared_state as ss
from routers import auth as auth_mod

PW = "Corr3ct-Horse!"


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


async def _seed_admin(sf):
    from database import User
    async with sf() as db:
        db.add(User(username="admin", password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode(),
                    role="admin", auth_source="local"))
        await db.commit()


async def _login(client, password):
    return await client.post("/api/auth/login", json={"username": "admin", "password": password})


def _device_cookie(client):
    name = auth_mod._device_cookie_name("admin")
    return name, client.cookies.get(name)


async def test_attacker_cannot_lock_out_a_known_browser(auth_client):
    client, sf = auth_client
    await _seed_admin(sf)

    # The admin signed in from this browser before → device cookie.
    resp = await _login(client, PW)
    assert resp.status_code == 200
    name, device = _device_cookie(client)
    assert device

    # An attacker (no device cookie) burns the account's budget.
    client.cookies.delete(name)
    for _ in range(auth_mod._LOCKOUT_ATTEMPTS):
        assert (await _login(client, "wrong")).status_code == 401

    # Unknown clients are now soft-locked — even with the right password.
    resp = await _login(client, PW)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0

    # The admin's own browser still gets in.
    client.cookies.set(name, device)
    resp = await _login(client, PW)
    assert resp.status_code == 200, resp.text

    # …and that success did NOT reset the attacker's lock.
    client.cookies.delete(name)
    assert (await _login(client, PW)).status_code == 429


async def test_forged_device_cookie_is_ignored(auth_client):
    client, sf = auth_client
    await _seed_admin(sf)
    for _ in range(auth_mod._LOCKOUT_ATTEMPTS):
        await _login(client, "wrong")
    client.cookies.set(auth_mod._device_cookie_name("admin"), "abc.0000")
    assert (await _login(client, PW)).status_code == 429


async def test_device_cookie_has_its_own_failure_budget(auth_client):
    client, sf = auth_client
    await _seed_admin(sf)
    assert (await _login(client, PW)).status_code == 200
    # Wrong passwords from the trusted browser itself: the account locks for
    # strangers, then the device budget runs out too.
    for _ in range(auth_mod._DEVICE_MAX_FAILURES):
        assert (await _login(client, "wrong")).status_code == 401
    assert (await _login(client, PW)).status_code == 429


async def test_success_without_lock_clears_counter(auth_client):
    client, sf = auth_client
    await _seed_admin(sf)
    for _ in range(auth_mod._LOCKOUT_ATTEMPTS - 1):
        await _login(client, "wrong")
    assert (await _login(client, PW)).status_code == 200
    async with sf() as db:
        assert await auth_mod._get_failed_attempts(db, "admin") == []


def test_lock_is_progressive_over_a_day():
    now = 100_000.0
    # 4 failures in the last 15 min: not locked.
    assert auth_mod._lock_remaining([now - 10] * 4, now) == 0
    # 5 in 15 min: locked until the oldest of them ages out.
    rem = auth_mod._lock_remaining([now - 100] * 5, now)
    assert 0 < rem <= auth_mod._LOCKOUT_WINDOW
    # 20 spread over the day (never 5 within 15 min): long lock.
    spread = [now - 3600 * i - 1 for i in range(20)]
    rem = auth_mod._lock_remaining(spread, now)
    assert rem > auth_mod._LOCKOUT_WINDOW


def test_username_case_does_not_bypass_the_counter():
    assert auth_mod._lockout_key("Admin") == auth_mod._lockout_key("admin ")
    assert auth_mod._device_cookie_name("ADMIN") == auth_mod._device_cookie_name("admin")

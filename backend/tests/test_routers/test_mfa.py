"""Two-factor sign-in: setup, the login step, recovery codes, admin reset."""
import time

import pytest
from sqlalchemy import select

from database import User
from services import totp
from tests.test_routers.test_tenant_auth_binding import (  # noqa: F401 - fixtures
    A, PW, _login, _reset_rate_limits, _user, env,
)

PASSWORD = PW


async def _secret_of(sf, username: str) -> str:
    from models.base import decrypt_value

    async with sf() as s:
        user = (await s.execute(select(User).where(User.username == username))).scalar_one()
        return decrypt_value(user.totp_secret)


async def _enable(client, sf, username: str) -> list[str]:
    started = await client.post("/api/account/mfa/setup")
    assert started.status_code == 200, started.text
    assert started.json()["otpauth_uri"].startswith("otpauth://totp/Nodeglow%3A")
    secret = await _secret_of(sf, username)
    assert secret == started.json()["secret"]
    done = await client.post("/api/account/mfa/enable",
                             json={"code": totp.code_for(secret, totp.counter_at())})
    assert done.status_code == 200, done.text
    return done.json()["recovery_codes"]


def test_totp_matches_the_rfc_6238_test_vector():
    # RFC 6238 appendix B, SHA-1, secret "12345678901234567890", T = 59 s.
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_for(secret, 59 // 30) == "287082"  # last six of 94287082
    assert totp.verify(secret, "287082", when=59) == 1
    assert totp.verify(secret, "287082", when=59, last_counter=1) is None, "no replay"
    assert totp.verify(secret, "000000", when=59) is None


async def test_setup_needs_a_valid_code_and_shows_recovery_codes_once(env):
    client, sf = env
    await _user(sf, "admin1", {A: "admin"})
    assert (await _login(client, "admin1")).status_code == 200
    assert (await client.get("/api/account/mfa")).json() == {
        "enabled": False, "enabled_at": None, "recovery_codes_left": 0}

    await client.post("/api/account/mfa/setup")
    wrong = await client.post("/api/account/mfa/enable", json={"code": "000000"})
    assert wrong.status_code == 400
    assert (await client.get("/api/account/mfa")).json()["enabled"] is False

    codes = await _enable(client, sf, "admin1")
    assert len(codes) == totp.RECOVERY_CODES and len(set(codes)) == len(codes)
    status = (await client.get("/api/account/mfa")).json()
    assert status["enabled"] is True and status["recovery_codes_left"] == totp.RECOVERY_CODES
    async with sf() as s:
        user = (await s.execute(select(User).where(User.username == "admin1"))).scalar_one()
        assert all(code not in (user.totp_recovery or "") for code in codes), "stored as hashes"


async def test_sign_in_asks_for_the_code_after_the_password(env):
    client, sf = env
    await _user(sf, "admin1", {A: "admin"})
    assert (await _login(client, "admin1")).status_code == 200
    recovery = await _enable(client, sf, "admin1")
    secret = await _secret_of(sf, "admin1")
    client.cookies.clear()

    def creds(**extra):
        return {"username": "admin1", "password": PASSWORD, **extra}

    # Password alone: no session, and the client learns a code is needed.
    r = await client.post("/api/auth/login", json=creds())
    assert r.status_code == 401 and r.json()["mfa_required"] is True
    assert "nodeglow_session" not in r.cookies
    # A wrong password never reveals that the account has a second factor.
    r = await client.post("/api/auth/login", json={"username": "admin1", "password": "nope-nope-nope"})
    assert r.status_code == 401 and "mfa_required" not in r.json()

    assert (await client.post("/api/auth/login", json=creds(totp="000000"))).status_code == 401

    # The step after the one used during setup, so it is not a replay.
    code = totp.code_for(secret, totp.counter_at() + 1)
    ok = await client.post("/api/auth/login", json=creds(totp=code))
    assert ok.status_code == 200, ok.text
    client.cookies.clear()
    again = await client.post("/api/auth/login", json=creds(totp=code))
    assert again.status_code == 401, "a code works once"

    # A recovery code signs in, once.
    ok = await client.post("/api/auth/login", json=creds(totp=recovery[0]))
    assert ok.status_code == 200, ok.text
    assert (await client.get("/api/account/mfa")).json()["recovery_codes_left"] == totp.RECOVERY_CODES - 1
    client.cookies.clear()
    assert (await client.post("/api/auth/login", json=creds(totp=recovery[0]))).status_code == 401


async def test_turning_it_off_needs_a_code_and_an_admin_can_reset_it(env):
    client, sf = env
    await _user(sf, "admin1", {A: "admin"})
    victim = await _user(sf, "editor1", {A: "editor"})
    assert (await _login(client, "admin1")).status_code == 200
    recovery = await _enable(client, sf, "admin1")

    assert (await client.post("/api/account/mfa/disable", json={"code": "000000"})).status_code == 403
    assert (await client.get("/api/account/mfa")).json()["enabled"] is True
    assert (await client.post("/api/account/mfa/disable", json={"code": recovery[1]})).status_code == 200
    assert (await client.get("/api/account/mfa")).json()["enabled"] is False

    # Admin reset for somebody who lost their phone.
    async with sf() as s:
        from datetime import datetime

        from models.base import encrypt_value
        user = await s.get(User, victim)
        user.totp_secret = encrypt_value(totp.new_secret())
        user.totp_enabled_at = datetime.utcnow()
        await s.commit()
    listed = {u["username"]: u for u in (await client.get("/api/users")).json()}
    assert listed["editor1"]["mfa_enabled"] is True
    assert (await client.post(f"/api/users/{victim}/mfa/reset")).status_code == 200
    async with sf() as s:
        user = await s.get(User, victim)
        assert user.totp_secret is None and user.totp_enabled_at is None


async def test_mfa_routes_need_a_session(env):
    client, _sf = env
    for method, path in (("get", "/api/account/mfa"), ("post", "/api/account/mfa/setup"),
                         ("post", "/api/account/mfa/enable"), ("post", "/api/account/mfa/disable")):
        r = await getattr(client, method)(path)
        assert r.status_code in (401, 403), (path, r.status_code)


@pytest.mark.parametrize("skew", [-30, 0, 30])
def test_a_code_from_the_neighbouring_step_is_accepted(skew):
    secret = totp.new_secret()
    now = time.time()
    assert totp.verify(secret, totp.code_for(secret, totp.counter_at(now + skew)), when=now) is not None

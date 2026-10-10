"""Cookie-authenticated mutations of /api/v1/* need the CSRF token too.

Before, every /api/v1 request skipped CSRF and only SameSite=Strict on the
session cookie kept a forged cross-site POST from acting as the admin.
"""
from tests.test_routers.conftest import make_client


class AdminUser:
    id = 1
    username = "admin"
    role = "admin"


async def test_cookie_session_post_without_csrf_token_is_rejected():
    async with make_client(fake_user=AdminUser()) as (ac, _sf):
        ac.cookies.set("nodeglow_session", "session-token")
        ac.headers.pop("x-csrf-token", None)
        resp = await ac.post("/api/v1/rules/999/toggle")
        assert resp.status_code == 403
        assert resp.json()["error"] == "CSRF validation failed"

        resp = await ac.request("DELETE", "/api/v1/hosts/999")
        assert resp.status_code == 403


async def test_cookie_session_post_with_wrong_csrf_token_is_rejected():
    async with make_client(fake_user=AdminUser()) as (ac, _sf):
        ac.cookies.set("nodeglow_session", "session-token")
        resp = await ac.post("/api/v1/rules/999/toggle", headers={"x-csrf-token": "forged.0000"})
        assert resp.status_code == 403


async def test_cookie_session_post_with_csrf_token_reaches_handler():
    async with make_client(fake_user=AdminUser()) as (ac, _sf):
        ac.cookies.set("nodeglow_session", "session-token")
        resp = await ac.post("/api/v1/rules/999/toggle")  # fixture sends the token
        assert resp.status_code == 404


async def test_api_key_request_stays_exempt_from_csrf():
    async with make_client(fake_user=AdminUser()) as (ac, _sf):
        ac.cookies.set("nodeglow_session", "session-token")
        ac.headers.pop("x-csrf-token", None)
        resp = await ac.post("/api/v1/rules/999/toggle", headers={"X-API-Key": "junk"})
        assert resp.status_code == 401  # reached the key check, not a CSRF 403


async def test_no_session_no_key_is_unauthorized_not_csrf(auth_client):
    ac, _sf = auth_client
    ac.headers.pop("x-csrf-token", None)
    resp = await ac.post("/api/v1/rules/999/toggle")
    assert resp.status_code == 401

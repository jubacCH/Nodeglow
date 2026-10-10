"""Read-only means read-only on every path, and API keys never ride on a session.

* The middleware used to enforce the read-only role only for ``/api/*``. The
  frontend also proxies ``/rules/*`` (and the backend serves ``/hosts/*``),
  where ``POST /rules/add`` or ``POST /hosts/{id}/toggle`` went through for a
  read-only user.
* A request carrying ``X-API-Key`` skipped CSRF but was still authenticated by
  its session cookie — so a cross-site form post with a junk header would have
  acted as the logged-in victim. Now such a request is authenticated only by
  the key.
"""
import re
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from tests.test_routers.conftest import make_client


class ReadonlyUser:
    id = 7
    username = "viewer"
    role = "readonly"


class EditorUser:
    id = 8
    username = "ed"
    role = "editor"


# Mutations a read-only user may still make (own account only). Routes under
# the middleware's auth-skip prefixes never see a session role at all.
READONLY_ALLOWED = {("POST", "/users/me/password")}
_SKIP_PREFIXES = ("/api/agent/", "/api/auth/", "/setup", "/install/", "/agents/download/", "/static")


def _mutating_routes(app):
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS", "GET"}):
            yield m, r.path


@pytest.fixture
async def readonly_client():
    async with make_client(fake_user=ReadonlyUser()) as (ac, _sf):
        yield ac


async def test_readonly_cannot_mutate_anything(readonly_client):
    from main import app

    failures = []
    checked = 0
    for method, path in _mutating_routes(app):
        if (method, path) in READONLY_ALLOWED or path.startswith(_SKIP_PREFIXES):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        resp = await readonly_client.request(method, url, json={}, follow_redirects=False)
        checked += 1
        if resp.status_code != 403:
            failures.append(f"{method} {path} -> {resp.status_code}")
    assert checked > 40
    assert not failures, "read-only user could reach mutating routes:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("path", [
    "/rules/add", "/rules/1/edit",
    "/hosts/add", "/hosts/1/edit", "/hosts/1/toggle",
    "/hosts/1/maintenance", "/hosts/1/check",
])
async def test_readonly_blocked_on_non_api_paths(readonly_client, path):
    resp = await readonly_client.post(path, data={"name": "x", "hostname": "10.0.0.1"},
                                      follow_redirects=False)
    assert resp.status_code == 403


async def test_readonly_may_still_change_own_password_route(readonly_client):
    """Allowed through the middleware (the route itself then validates)."""
    resp = await readonly_client.post("/users/me/password", data={}, follow_redirects=False)
    assert resp.status_code != 403


def _all_dependency_calls(dependant):
    for d in dependant.dependencies:
        if d.call is not None:
            yield d.call
        yield from _all_dependency_calls(d)


@pytest.mark.parametrize("router_mod,method,path", [
    ("rules", "POST", "/rules/add"),
    ("rules", "POST", "/rules/{rule_id}/edit"),
    ("rules", "POST", "/api/v1/rules/{rule_id}/toggle"),
    ("rules", "POST", "/api/v1/rules/{rule_id}/delete"),
    ("ping", "POST", "/hosts/add"),
    ("ping", "POST", "/hosts/api/create"),
    ("ping", "POST", "/hosts/{host_id}/edit"),
    ("ping", "POST", "/hosts/api/{host_id}/delete"),
    ("ping", "POST", "/hosts/{host_id}/toggle"),
    ("ping", "POST", "/hosts/{host_id}/maintenance"),
    ("ping", "POST", "/hosts/api/{host_id}/maintenance"),
    ("ping", "POST", "/hosts/{host_id}/check"),
    ("ping", "PATCH", "/hosts/api/{host_id}/discovered-ports/{port_id}"),
    ("ping", "POST", "/hosts/api/{host_id}/scan-ports"),
])
def test_mutating_routes_declare_editor(router_mod, method, path):
    """The role check is on the route, not only in the middleware."""
    import importlib
    from routers.api_v1 import require_editor, require_admin

    mod = importlib.import_module(f"routers.{router_mod}")
    route = next(r for r in mod.router.routes
                 if isinstance(r, APIRoute) and r.path == path and method in r.methods)
    calls = set(_all_dependency_calls(route.dependant))
    assert calls & {require_editor, require_admin}, f"{method} {path} has no editor gate"


async def test_editor_passes_editor_gate():
    async with make_client(fake_user=EditorUser()) as (ac, _sf):
        resp = await ac.post("/api/v1/rules/999/toggle")
        assert resp.status_code == 404  # reached the handler


async def test_api_key_header_ignores_session_cookie():
    """With X-API-Key present the session is never consulted: a junk key plus
    a valid session (and no CSRF token) must not act as the session user."""
    from unittest.mock import AsyncMock

    async with make_client(fake_user=EditorUser()) as (ac, _sf):
        import database
        spy = database.get_current_user  # patched AsyncMock returning EditorUser
        assert isinstance(spy, AsyncMock)
        spy.reset_mock()
        ac.headers.pop("x-csrf-token", None)
        resp = await ac.post("/api/integration/proxmox/create",
                             headers={"X-API-Key": "junk"}, json={"name": "x"})
        assert resp.status_code == 401
        resp = await ac.post("/api/v1/rules/1/toggle", headers={"X-API-Key": "junk"})
        assert resp.status_code == 401
        assert spy.await_count == 0, "session lookup ran for an API-key request"


async def test_audit_records_api_key_actor(db):
    from models.api_key import ApiKey
    from models.audit import AuditLog
    from services.audit import log_action
    from sqlalchemy import select

    key = ApiKey(id=5, name="grafana", key_hash="h", prefix="ng_abc12", role="editor", enabled=True)
    req = SimpleNamespace(state=SimpleNamespace(current_user=None, api_key=key),
                          client=SimpleNamespace(host="10.1.2.3"))
    await log_action(db, req, "rule.toggle", "rule", 1, "r1")
    await db.commit()
    row = (await db.execute(select(AuditLog))).scalar_one()
    assert row.username == "apikey:ng_abc12:grafana"
    assert row.user_id is None
    assert row.ip_address == "10.1.2.3"


async def test_audit_prefers_session_user(db):
    from models.audit import AuditLog
    from services.audit import log_action
    from sqlalchemy import select

    req = SimpleNamespace(state=SimpleNamespace(current_user=EditorUser(), api_key=None),
                          client=None)
    await log_action(db, req, "x")
    await db.commit()
    row = (await db.execute(select(AuditLog))).scalar_one()
    assert (row.user_id, row.username) == (8, "ed")

"""Every route must refuse a caller that has neither a session nor an API key.

The rules router once served ``GET /api/v1/rules`` and the toggle/delete
endpoints to anyone: the HTTP middleware lets unauthenticated ``/api/v1/*``
traffic through because that router "has its own API-key auth", and the rules
routes simply never declared it. Nothing noticed, because the router tests run
with authentication patched out.

This test walks ``app.routes`` with authentication ENABLED and asserts that
each route, called anonymously, answers 401/403 or redirects to the login page.
A new route is covered automatically; the only way to publish an anonymous
route is to add it to ``PUBLIC_ROUTES`` below, on purpose and in review.

The CSRF cookie and header are valid in this client, so a 403 can never be a
CSRF rejection standing in for a missing auth check.
"""
import re

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount

# Routes that are intentionally reachable without a session or API key.
# Keep this list short and justify every entry.
PUBLIC_ROUTES = {
    # Liveness / readiness probes and Prometheus scrape (no data beyond counters).
    ("GET", "/health"),
    ("GET", "/livez"),
    ("GET", "/readyz"),
    ("GET", "/metrics"),
    # Login / logout / "who am I" — the entry points of authentication itself.
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    # First-run wizard. Refuses to do anything once setup is complete.
    ("GET", "/setup"),
    ("GET", "/setup/status"),
    ("POST", "/setup/complete"),
    # Agent protocol: authenticated by install token / agent bearer token.
    ("POST", "/api/agent/enroll"),
    ("POST", "/api/agent/report"),
    ("POST", "/api/agent/logs"),
    ("GET", "/api/agent/version/{platform}"),
    ("GET", "/api/agent/update-public-key"),
    # Installers require a valid per-install token in the query string.
    ("GET", "/install/linux"),
    ("GET", "/install/windows"),
    # Agent binaries are public artefacts (signed updates verify them).
    ("GET", "/agents/download/{platform}"),
}

# Whole path prefixes that are public (static assets, websocket handshakes
# that authenticate themselves before accepting).
PUBLIC_PREFIXES = ("/static",)


def _concrete_path(path: str) -> str:
    # {param} and {param:path} → a plausible value
    return re.sub(r"\{[^}]+\}", "1", path)


def _http_routes(app):
    for route in app.routes:
        if isinstance(route, Mount) or isinstance(route, APIWebSocketRoute):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def _is_login_redirect(resp) -> bool:
    if resp.status_code not in (301, 302, 303, 307, 308):
        return False
    return resp.headers.get("location", "").rstrip("/").endswith("/login")


def test_public_allowlist_has_no_stale_entries():
    """An allowlist entry for a route that no longer exists is dead weight —
    and a trap: it would silently cover a future route of the same name."""
    from main import app

    existing = set(_http_routes(app))
    stale = PUBLIC_ROUTES - existing
    # /livez and /readyz are added together with the auth fixes; everything
    # else must exist verbatim.
    assert not stale, f"PUBLIC_ROUTES lists routes that do not exist: {sorted(stale)}"


async def test_every_route_rejects_anonymous_callers(auth_client):
    client, _sf = auth_client
    from main import app

    failures = []
    checked = 0
    for method, path in _http_routes(app):
        if (method, path) in PUBLIC_ROUTES or path.startswith(PUBLIC_PREFIXES):
            continue
        url = _concrete_path(path)
        kwargs = {}
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            kwargs["json"] = {}
        try:
            resp = await client.request(method, url, follow_redirects=False, **kwargs)
        except Exception as exc:  # a crash is not an auth decision
            failures.append(f"{method} {path}: raised {type(exc).__name__}: {exc}")
            continue
        checked += 1
        if resp.status_code in (401, 403) or _is_login_redirect(resp):
            continue
        failures.append(f"{method} {path} -> {resp.status_code}")

    assert checked > 50, f"only {checked} routes checked — enumeration is broken"
    assert not failures, "Routes reachable without authentication:\n  " + "\n  ".join(failures)


async def test_every_api_v1_route_checks_the_key_itself(auth_client):
    """The middleware passes any /api/v1/* request that carries an X-API-Key
    header on to the route, which must validate the key. Send a bogus key:
    a route without an auth dependency answers 200/404, one with it 401.

    This is the check that catches a rules-style route with no dependency
    even though the middleware would refuse a request with no header at all.
    """
    client, _sf = auth_client
    from main import app

    failures = []
    checked = 0
    for method, path in _http_routes(app):
        if not path.startswith("/api/v1/"):
            continue
        kwargs = {"headers": {"X-API-Key": "ng_not_a_real_key"}}
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            kwargs["json"] = {}
        resp = await client.request(method, _concrete_path(path), follow_redirects=False, **kwargs)
        checked += 1
        if resp.status_code not in (401, 403):
            failures.append(f"{method} {path} -> {resp.status_code}")

    assert checked > 30, f"only {checked} /api/v1 routes checked"
    assert not failures, "API v1 routes that do not validate the API key:\n  " + "\n  ".join(failures)


async def test_api_v1_without_key_is_rejected_by_middleware(auth_client):
    """Defence in depth: /api/v1/* without session and without X-API-Key is
    refused before any route code runs, even for a route that forgot its
    dependency."""
    client, _sf = auth_client
    resp = await client.get("/api/v1/this-route-does-not-exist")
    assert resp.status_code == 401


async def test_invalid_api_key_is_rejected(auth_client):
    client, _sf = auth_client
    resp = await client.get("/api/v1/rules", headers={"X-API-Key": "ng_bogus"})
    assert resp.status_code == 401

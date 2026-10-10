"""Health probes answer with status codes a monitor can act on.

``/health`` returned HTTP 200 with ``{"status": "error"}`` when Postgres was
down, so anything that only looks at the status code — Docker's HEALTHCHECK,
uptime monitors, load balancers — saw a healthy service.
"""
from unittest.mock import AsyncMock, patch


async def test_livez_is_always_ok(auth_client):
    client, _sf = auth_client
    resp = await client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok_when_dependencies_answer(auth_client):
    client, _sf = auth_client
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_readyz_503_when_clickhouse_down(auth_client):
    client, _sf = auth_client
    with patch("services.clickhouse_client.query_scalar",
               new=AsyncMock(side_effect=RuntimeError("ch down"))):
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["clickhouse"] == "connection failed"
    assert body["db"] == "connected"


async def test_readyz_503_when_postgres_down(auth_client):
    client, _sf = auth_client
    with patch("main._check_postgres", new=AsyncMock(return_value=False)):
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["db"] == "connection failed"


async def test_health_keeps_its_body_and_reports_outage_as_503(auth_client):
    client, _sf = auth_client
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "connected"}

    with patch("main.AsyncSessionLocal", side_effect=OSError("connection refused")):
        resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "db": "connection failed"}


async def test_probes_need_no_session(auth_client):
    client, _sf = auth_client
    for path in ("/livez", "/readyz", "/health"):
        resp = await client.get(path, follow_redirects=False)
        assert resp.status_code in (200, 503), path

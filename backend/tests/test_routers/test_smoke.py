"""Smoke tests – verify key routes return 200 and don't crash."""
import pytest
from unittest.mock import AsyncMock, patch


async def test_health(client):
    """Health endpoint bypasses middleware and returns ok."""
    with patch("main.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_login_api_exists(client):
    """Login API endpoint exists (GET not allowed, POST required)."""
    resp = await client.get("/api/auth/login")
    assert resp.status_code == 405  # Method Not Allowed (POST only)


async def test_dashboard_api(client):
    """Dashboard JSON API returns 200 with empty data."""
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_hosts_status_api(client):
    """Hosts status JSON API returns a list."""
    resp = await client.get("/hosts/api/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_incidents_api(client):
    """Incidents v1 API returns a list."""
    resp = await client.get("/api/v1/incidents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_syslog_api(client):
    """Syslog v1 API returns paged result envelope."""
    resp = await client.get("/api/v1/syslog")
    assert resp.status_code == 200


async def test_settings_json(client):
    """Settings JSON endpoint returns config dict for admin."""
    resp = await client.get("/settings/json")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_integrations_v1_api(client):
    """Integrations v1 API returns a list."""
    resp = await client.get("/api/v1/integrations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_unknown_integration_fields_404(client):
    """Unknown integration type returns 404 from fields API."""
    resp = await client.get("/api/integration/nonexistent/fields")
    assert resp.status_code == 404


# ── Subnet Scanner ──────────────────────────────────────────────────────────


async def test_subnet_scanner_page_data(client):
    """Subnet scanner page data API returns JSON."""
    resp = await client.get("/api/subnet-scanner/page-data")
    assert resp.status_code == 200


async def test_subnet_scanner_scan_invalid_cidr(client):
    """Scan with invalid CIDR returns 400."""
    resp = await client.post(
        "/api/subnet-scanner/scan",
        json={"cidr": "not-a-cidr"},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_subnet_scanner_scan_too_large(client):
    """Scan with a /8 returns 400 (too large)."""
    resp = await client.post(
        "/api/subnet-scanner/scan",
        json={"cidr": "10.0.0.0/8"},
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["error"].lower()


async def test_subnet_scanner_create_schedule(client):
    """Create and delete a scheduled scan."""
    resp = await client.post(
        "/api/subnet-scanner/schedules",
        json={"cidr": "192.168.1.0/28", "name": "Test", "interval_m": 60},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data

    # Delete it
    resp = await client.delete(f"/api/subnet-scanner/schedules/{data['id']}")
    assert resp.status_code == 200


async def test_subnet_scanner_schedule_min_interval(client):
    """Schedule with interval < 5 min returns 400."""
    resp = await client.post(
        "/api/subnet-scanner/schedules",
        json={"cidr": "192.168.1.0/24", "interval_m": 1},
    )
    assert resp.status_code == 400


# ── Hosts API ───────────────────────────────────────────────────────────────


async def test_hosts_search_empty(client):
    """Host search with empty query returns empty list."""
    resp = await client.get("/hosts/api/search?q=")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_hosts_search_short_query(client):
    """Host search with 1-char query returns empty (min 2 chars)."""
    resp = await client.get("/hosts/api/search?q=a")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_hosts_search_no_results(client):
    """Host search returns empty when no hosts match."""
    resp = await client.get("/hosts/api/search?q=nonexistent-host-xyz")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Agents / Users API ──────────────────────────────────────────────────────


async def test_agents_api(client):
    """Agents v1 API returns a list."""
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_users_api(client):
    """Users API returns a list for admin."""
    resp = await client.get("/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

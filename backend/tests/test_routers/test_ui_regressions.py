"""Regressions found by clicking through the UI against production data."""
from datetime import datetime
from unittest.mock import patch

from models.alert_rule import AlertRule
from models.incident import Incident

from tests.test_routers.conftest import make_client


async def test_rules_list_with_a_triggered_rule():
    # The list read r.last_triggered, which does not exist: any stored rule
    # turned GET /api/v1/rules into a 500.
    async with make_client() as (client, session_factory):
        async with session_factory() as db:
            db.add(AlertRule(
                name="CPU high", source_type="proxmox", field_path="cpu",
                operator="gt", threshold="90",
                last_triggered_at=datetime(2026, 10, 10, 8, 30),
            ))
            await db.commit()

        resp = await client.get("/api/v1/rules")

    assert resp.status_code == 200
    rules = resp.json()
    assert rules[0]["name"] == "CPU high"
    assert rules[0]["last_triggered_at"].startswith("2026-10-10 08:30")


async def test_host_detail_reports_uptime_per_window():
    # The response read h24/d7/d30 from a 24h-only result keyed uptime_pct,
    # so the three uptime tiles always showed "--".
    from models.ping import PingHost

    async def fake_uptime(host_ids, hours=24):
        pct = {24: 99.5, 168: 98.0, 720: 97.25}[hours]
        return {hid: {"total": 10, "ok": 9, "uptime_pct": pct} for hid in host_ids}

    async with make_client() as (client, session_factory):
        async with session_factory() as db:
            host = PingHost(name="web", hostname="web.example", check_type="icmp")
            db.add(host)
            await db.commit()
            host_id = host.id

        with patch("services.clickhouse_client.get_ping_uptime", side_effect=fake_uptime):
            resp = await client.get(f"/api/v1/hosts/{host_id}")

    assert resp.status_code == 200
    assert resp.json()["uptime"] == {"h24": 99.5, "d7": 98.0, "d30": 97.25}


async def test_integration_certs_use_latest_successful_snapshot_only():
    import json

    from models.integration import Snapshot
    from routers.ssl_monitor import _get_integration_certs

    def npm(name):
        return json.dumps({"certificates": [{"nice_name": name, "days_left": 30, "domains": [name]}]})

    async with make_client() as (_client, session_factory):
        async with session_factory() as db:
            db.add(Snapshot(entity_type="npm", entity_id=1, ok=True, data_json=npm("old.example"),
                            timestamp=datetime(2026, 10, 10, 8, 0)))
            db.add(Snapshot(entity_type="npm", entity_id=1, ok=True, data_json=npm("new.example"),
                            timestamp=datetime(2026, 10, 10, 8, 1)))
            db.add(Snapshot(entity_type="npm", entity_id=1, ok=False, data_json=None,
                            timestamp=datetime(2026, 10, 10, 8, 2)))
            db.add(Snapshot(entity_type="npm", entity_id=2, ok=True, data_json=npm("other.example"),
                            timestamp=datetime(2026, 10, 10, 8, 0)))
            await db.commit()

            certs = await _get_integration_certs(db)

    assert sorted(c["name"] for c in certs) == ["new.example", "other.example"]


async def test_dashboard_counts_every_active_incident():
    # The tile was len() of a query limited to 5.
    async with make_client() as (client, session_factory):
        async with session_factory() as db:
            for i in range(7):
                db.add(Incident(rule=f"rule-{i}", title=f"Incident {i}", severity="warning",
                                status="open" if i % 2 else "acknowledged",
                                created_at=datetime(2026, 10, 10, 8, i)))
            db.add(Incident(rule="old", title="Resolved", severity="warning", status="resolved",
                            created_at=datetime(2026, 10, 9, 8, 0)))
            await db.commit()

        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    assert resp.json()["active_incidents"] == 7

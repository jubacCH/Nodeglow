"""Last visit per user and the change feed (/api/v2/me/seen, /api/v2/changes)."""
import json
from datetime import datetime, timedelta

from tests.test_routers.conftest import make_client


class EditorUser:
    id = 2
    username = "editor"
    role = "editor"


async def test_seen_round_trip_returns_previous():
    async with make_client() as (client, _sf):
        assert (await client.get("/api/v2/me/seen")).json() == {"dashboard_seen_at": None}
        first = (await client.post("/api/v2/me/seen")).json()
        assert first["previous_seen_at"] is None
        assert first["dashboard_seen_at"].endswith("Z")
        second = (await client.post("/api/v2/me/seen")).json()
        assert second["previous_seen_at"] == first["dashboard_seen_at"]
        assert (await client.get("/api/v2/me/seen")).json()["dashboard_seen_at"] == second["dashboard_seen_at"]


async def _seed(sf, now):
    from models.agent import Agent
    from models.audit import AuditLog
    from models.discovered_port import DiscoveredPort
    from models.incident import Incident, IncidentEvent
    from models.maintenance import MaintenanceWindow
    from models.ping import PingHost

    async with sf() as db:
        old = Incident(rule="r", title="old", severity="warning", status="resolved",
                       created_at=now - timedelta(days=3), resolved_at=now - timedelta(days=3))
        opened = Incident(rule="multi_host_down", title="SW down", severity="critical", status="open",
                          created_at=now - timedelta(hours=2))
        resolved = Incident(rule="port_error", title="HTTP 503 intranet", severity="warning",
                            status="resolved", created_at=now - timedelta(hours=5),
                            resolved_at=now - timedelta(hours=1))
        db.add_all([old, opened, resolved])
        await db.flush()
        db.add(IncidentEvent(incident_id=opened.id, event_type="acknowledged",
                             summary="Acknowledged by admin", timestamp=now - timedelta(minutes=30)))
        host = PingHost(name="NAS-ZH-01", hostname="10.0.0.20", source="unifi",
                        created_at=now - timedelta(hours=3))
        manual = PingHost(name="old-host", hostname="10.0.0.21", created_at=now - timedelta(days=9))
        db.add_all([host, manual])
        await db.flush()
        db.add(DiscoveredPort(host_id=host.id, port=443, first_seen=now - timedelta(hours=1)))
        db.add(AuditLog(timestamp=now - timedelta(minutes=40), action="agent.version_change",
                        target_type="agent", target_id=3, target_name="SRV-RDS-01",
                        details=json.dumps({"from": "0.4.1", "to": "0.4.2"})))
        db.add(AuditLog(timestamp=now - timedelta(minutes=20), action="settings.update",
                        target_type="setting", username="admin"))
        db.add(AuditLog(timestamp=now - timedelta(minutes=10), action="auth.login", username="admin"))
        db.add(MaintenanceWindow(name="NAS firmware update", kind="once",
                                 starts_at=now - timedelta(minutes=32), ends_at=now + timedelta(minutes=28),
                                 host_ids=json.dumps([host.id])))
        db.add(Agent(name="probe-bern-01", token="p" * 64, is_probe=True, probe_interval_seconds=60,
                     last_seen=now - timedelta(minutes=11), created_at=now - timedelta(days=30)))
        await db.commit()


async def test_change_feed_covers_every_derivable_type():
    now = datetime.utcnow()
    async with make_client() as (client, sf):
        await _seed(sf, now)
        since = (now - timedelta(hours=6)).isoformat() + "Z"
        body = (await client.get(f"/api/v2/changes?since={since}")).json()
        c = body["counts"]
        assert c["incident_opened"] == 2  # SW down + HTTP 503 (old one is outside)
        assert c["incident_resolved"] == 1
        assert c["incident_acknowledged"] == 1
        assert c["host_added"] == 1
        assert c["port_discovered"] == 1
        assert c["agent_updated"] == 1
        assert c["maintenance_started"] == 1
        assert c["probe_silent"] == 1
        assert c["config_change"] == 1  # settings.update; login is excluded
        assert body["total"] == sum(c.values())
        types = [i["type"] for i in body["items"]]
        assert len(types) == body["total"]
        # Newest first.
        ats = [i["at"] for i in body["items"]]
        assert ats == sorted(ats, reverse=True)
        added = next(i for i in body["items"] if i["type"] == "host_added")
        assert added["detail"]["discovered"] is True
        upd = next(i for i in body["items"] if i["type"] == "agent_updated")
        assert upd["detail"]["to"] == "0.4.2"


async def test_change_feed_paginates_and_filters():
    now = datetime.utcnow()
    async with make_client() as (client, sf):
        await _seed(sf, now)
        since = (now - timedelta(hours=6)).isoformat() + "Z"
        page1 = (await client.get(f"/api/v2/changes?since={since}&limit=3")).json()
        page2 = (await client.get(f"/api/v2/changes?since={since}&limit=3&offset=3")).json()
        assert len(page1["items"]) == 3 and page1["has_more"] is True
        assert {i["at"] + i["type"] for i in page1["items"]}.isdisjoint(
            {i["at"] + i["type"] for i in page2["items"]})
        only = (await client.get(f"/api/v2/changes?since={since}&types=incident_opened")).json()
        assert set(only["counts"]) == {"incident_opened"}
        assert (await client.get(f"/api/v2/changes?since={since}&types=nope")).status_code == 400
        assert (await client.get("/api/v2/changes?since=garbage")).status_code == 400


async def test_config_changes_are_admin_only():
    now = datetime.utcnow()
    async with make_client(fake_user=EditorUser()) as (client, sf):
        await _seed(sf, now)
        since = (now - timedelta(hours=6)).isoformat() + "Z"
        body = (await client.get(f"/api/v2/changes?since={since}")).json()
        assert "config_change" not in body["counts"]

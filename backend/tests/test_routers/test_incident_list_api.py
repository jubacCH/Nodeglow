"""/api/v1/incidents: multi-status filter, total, pagination, affected hosts.

The alerts tab only ever saw the 50 most recently updated incidents of a
single status. Open + acknowledged are now one request, with a total and an
offset, and each incident names its recorded hosts.
"""
import json
from datetime import datetime, timedelta

from tests.test_routers.conftest import make_client


async def _seed(sf, n_open=3, n_ack=2, n_resolved=4):
    from models.incident import Incident
    from models.ping import PingHost

    now = datetime.utcnow()
    async with sf() as db:
        h1 = PingHost(name="SW-ZH-CORE-02", hostname="10.0.0.2")
        h17 = PingHost(name="PC-17", hostname="10.0.0.17")
        db.add_all([h1, h17])
        await db.flush()
        i = 0
        for status, n in (("open", n_open), ("acknowledged", n_ack), ("resolved", n_resolved)):
            for _ in range(n):
                i += 1
                db.add(Incident(
                    rule="multi_host_down" if i % 2 else "port_error",
                    title=f"incident {i}", severity="critical" if i % 3 == 0 else "warning",
                    status=status, created_at=now - timedelta(minutes=i),
                    updated_at=now - timedelta(minutes=i),
                    host_ids=json.dumps([h1.id, h17.id]) if i == 1 else (json.dumps([h17.id]) if i == 2 else None),
                ))
        await db.commit()
        return h1.id, h17.id


async def test_multi_status_filter_with_total_and_pagination():
    async with make_client() as (client, sf):
        await _seed(sf)
        resp = await client.get("/api/v1/incidents?status=open,acknowledged&limit=2")
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "5"
        page1 = resp.json()
        assert len(page1) == 2
        assert {i["status"] for i in page1} <= {"open", "acknowledged"}

        resp = await client.get("/api/v1/incidents?status=open,acknowledged&limit=2&offset=4&envelope=true")
        body = resp.json()
        assert body["total"] == 5 and body["offset"] == 4 and len(body["items"]) == 1
        assert body["has_more"] is False

        # Old single-value usage still works and stays a bare list.
        resp = await client.get("/api/v1/incidents?status=resolved")
        assert isinstance(resp.json(), list) and len(resp.json()) == 4

        assert (await client.get("/api/v1/incidents?status=bogus")).status_code == 400
        assert len((await client.get("/api/v1/incidents?status=all")).json()) == 9


async def test_incidents_carry_affected_hosts_and_filter_by_host():
    async with make_client() as (client, sf):
        h1, h17 = await _seed(sf)
        items = {i["title"]: i for i in (await client.get("/api/v1/incidents?limit=100")).json()}
        first = items["incident 1"]
        assert first["host_ids"] == sorted([h1, h17])
        assert first["host_count"] == 2
        assert {h["name"] for h in first["hosts"]} == {"SW-ZH-CORE-02", "PC-17"}
        assert all("state" in h for h in first["hosts"])
        # Not recorded → null, never an empty list.
        assert items["incident 5"]["host_ids"] is None
        assert items["incident 5"]["hosts"] is None

        by_host = (await client.get(f"/api/v1/incidents?host_id={h1}")).json()
        assert [i["title"] for i in by_host] == ["incident 1"]
        by_host17 = (await client.get(f"/api/v1/incidents?host_id={h17}")).json()
        assert sorted(i["title"] for i in by_host17) == ["incident 1", "incident 2"]


async def test_severity_sort_and_multi_severity():
    async with make_client() as (client, sf):
        await _seed(sf)
        items = (await client.get("/api/v1/incidents?sort=severity&limit=100")).json()
        sev = [i["severity"] for i in items]
        assert sev == sorted(sev, key=lambda s: {"critical": 0, "warning": 1}[s])
        crit = (await client.get("/api/v1/incidents?severity=critical,info")).json()
        assert {i["severity"] for i in crit} == {"critical"}


async def test_incident_detail_lists_hosts_with_state():
    from models.incident import Incident

    async with make_client() as (client, sf):
        await _seed(sf)
        async with sf() as db:
            from sqlalchemy import select
            inc = (await db.execute(select(Incident).where(Incident.title == "incident 1"))).scalar_one()
        detail = (await client.get(f"/api/v1/incidents/{inc.id}")).json()
        assert detail["host_count"] == 2
        assert len(detail["hosts"]) == 2
        assert detail["acknowledged"] is False


async def test_host_name_filter_still_works():
    from models.incident import Incident, IncidentEvent

    async with make_client() as (client, sf):
        async with sf() as db:
            inc = Incident(rule="r", title="t", severity="warning", status="open")
            db.add(inc)
            await db.flush()
            db.add(IncidentEvent(incident_id=inc.id, event_type="host_down", summary="nas01 down"))
            db.add(IncidentEvent(incident_id=inc.id, event_type="host_down", summary="nas01 still down"))
            await db.commit()
        resp = await client.get("/api/v1/incidents?host_name=nas01")
        assert len(resp.json()) == 1
        assert resp.headers["X-Total-Count"] == "1"

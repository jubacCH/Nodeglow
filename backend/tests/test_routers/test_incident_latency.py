"""GET /api/v2/incidents/{id}/latency (concept F, phase 3, PR 3)."""
import json
from datetime import datetime, timedelta

from tests.test_routers.conftest import make_client
from tests.test_routers.test_dashboard_v2 import fake_clickhouse


async def _incident(sf, rule="multi_host_down", hosts=(1, 2), minutes_ago=25):
    from models.incident import Incident
    from models.ping import PingHost

    async with sf() as db:
        rows = [PingHost(name=f"SRV-{i}", hostname=f"10.0.0.{i}") for i in range(len(hosts or ()))]
        db.add_all(rows)
        await db.flush()
        inc = Incident(rule=rule, title="Network issue", severity="critical", status="open",
                       host_ids=json.dumps([r.id for r in rows]) if hosts is not None else None,
                       created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
        db.add(inc)
        await db.commit()
        return inc.id, [r.id for r in rows]


def _points():
    minute = datetime.utcnow().replace(second=0, microsecond=0)
    return [
        {"minute": minute - timedelta(minutes=40), "median_ms": 0.3, "max_ms": 0.5, "ok": 4, "failed": 0},
        {"minute": minute - timedelta(minutes=5), "median_ms": 14.2, "max_ms": 40.0, "ok": 2, "failed": 2},
        {"minute": minute - timedelta(minutes=4), "median_ms": None, "max_ms": None, "ok": 0, "failed": 4},
    ]


async def test_latency_and_loss_of_an_incident():
    async with make_client() as (client, sf):
        iid, host_ids = await _incident(sf)
        with fake_clickhouse(latency=_points()):
            resp = await client.get(f"/api/v2/incidents/{iid}/latency")
    assert resp.status_code == 200
    data = resp.json()
    assert data["incident_id"] == iid and data["host_ids"] == host_ids
    assert data["host_names"] == ["SRV-0", "SRV-1"]
    assert data["window_hours"] == 2
    assert data["median_before_ms"] == 0.3 and data["median_since_ms"] == 14.2
    assert [p["loss_pct"] for p in data["points"]] == [0.0, 50.0, 100.0]
    assert data["points"][2]["median_ms"] is None, "no successful check, no latency"


async def test_rules_without_a_chart_and_missing_hosts_answer_204():
    async with make_client() as (client, sf):
        port, _ = await _incident(sf, rule="port_error")
        nohosts, _ = await _incident(sf, hosts=None)
        with fake_clickhouse(latency=_points()):
            assert (await client.get(f"/api/v2/incidents/{port}/latency")).status_code == 204
            assert (await client.get(f"/api/v2/incidents/{nohosts}/latency")).status_code == 204


async def test_unknown_incident_and_window_limits():
    async with make_client() as (client, sf):
        iid, _ = await _incident(sf)
        with fake_clickhouse(latency=_points()):
            assert (await client.get("/api/v2/incidents/99999/latency")).status_code == 404
            assert (await client.get(f"/api/v2/incidents/{iid}/latency?hours=25")).status_code == 422
            assert (await client.get(f"/api/v2/incidents/{iid}/latency?hours=0")).status_code == 422
            assert (await client.get(f"/api/v2/incidents/{iid}/latency?hours=6")).json()["window_hours"] == 6


async def test_the_dashboard_chart_only_follows_reachability_incidents():
    """The worst open incident is a port error: it has no chart, the next one does."""
    async with make_client() as (client, sf):
        await _incident(sf, rule="port_error", minutes_ago=5)
        iid, _ = await _incident(sf, rule="upstream_failure", minutes_ago=30)
        with fake_clickhouse(latency=_points()):
            data = (await client.get("/api/v2/dashboard")).json()
    assert data["latency"]["incident_id"] == iid
    assert data["latency"]["points"][1]["loss_pct"] == 50.0

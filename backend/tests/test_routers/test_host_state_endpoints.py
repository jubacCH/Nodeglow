"""Hosts behind a silent probe are unknown in every list, not only in the topology.

UX audit F-01/F-34: /api/v1/hosts, /hosts/api/status and /api/dashboard read
the newest check row and showed "online" for hosts whose probe had gone quiet.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from tests.test_routers.conftest import make_client


async def _seed(sf):
    from models.agent import Agent
    from models.ping import PingHost

    async with sf() as db:
        probe = Agent(name="probe-bern-01", hostname="probe-bern-01", token="p" * 64,
                      is_probe=True, probe_interval_seconds=60,
                      last_seen=datetime.utcnow() - timedelta(minutes=11))
        db.add(probe)
        await db.flush()
        behind = PingHost(name="NAS-BE-01", hostname="10.2.0.5", probe_id=probe.id)
        core = PingHost(name="fw-zh-01", hostname="10.0.0.1")
        db.add_all([behind, core])
        await db.commit()
        return behind.id, core.id


@contextmanager
def _latest(ids):
    now = datetime.utcnow()
    rows = {hid: {"host_id": hid, "success": 1, "latency_ms": 1.5,
                  "timestamp": now - timedelta(seconds=20)} for hid in ids}
    with patch("services.clickhouse_client.get_latest_ping_per_host",
               new=AsyncMock(return_value=rows)):
        yield


async def test_api_v1_hosts_reports_unknown_behind_silent_probe():
    async with make_client() as (client, sf):
        behind, core = await _seed(sf)
        with _latest([behind, core]):
            hosts = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()}
            assert hosts[behind]["state"] == "unknown"
            assert hosts[behind]["status"] == "unknown"  # was "online"
            assert "probe-bern-01 silent" in hosts[behind]["state_reason"]
            assert hosts[behind]["observed_at"] is not None
            assert hosts[core]["state"] == "up"
            assert hosts[core]["status"] == "online"

            only_unknown = (await client.get("/api/v1/hosts?state=unknown,down")).json()
            assert [h["id"] for h in only_unknown] == [behind]
            assert (await client.get("/api/v1/hosts?state=bogus")).status_code == 400

            detail = (await client.get(f"/api/v1/hosts/{behind}")).json()
            assert detail["state"] == "unknown"
            assert detail["health_score"] == 0.8


async def test_hosts_api_status_is_probe_aware():
    async with make_client() as (client, sf):
        behind, core = await _seed(sf)
        with _latest([behind, core]):
            rows = {h["id"]: h for h in (await client.get("/hosts/api/status")).json()}
        assert rows[behind]["online"] is None  # was True
        assert rows[behind]["state"] == "unknown"
        assert rows[core]["online"] is True
        assert rows[core]["state"] == "up"


async def test_old_dashboard_counts_do_not_count_unobserved_hosts_as_online():
    async with make_client() as (client, sf):
        behind, core = await _seed(sf)
        with _latest([behind, core]):
            data = (await client.get("/api/dashboard")).json()
        assert data["online_count"] == 1
        assert data["host_state_counts"]["unknown"] == 1
        assert data["host_state_counts"]["up"] == 1
        stats = {s["host"]["id"]: s for s in data["host_stats"]}
        assert stats[behind]["online"] is None
        assert stats[behind]["state"] == "unknown"
        assert data["host_state_reasons"]["unknown"][0]["count"] == 1


async def test_topology_nodes_carry_state_and_edge_provenance():
    from models.ping import PingHost

    async with make_client() as (client, sf):
        async with sf() as db:
            sw = PingHost(name="sw", hostname="10.0.0.2")
            db.add(sw)
            await db.flush()
            db.add(PingHost(name="pc", hostname="10.0.0.3", parent_id=sw.id))
            await db.commit()
        data = (await client.get("/api/v1/topology")).json()
        assert all("state" in n for n in data["nodes"])
        assert data["edges"][0]["provenance"] == "manual"

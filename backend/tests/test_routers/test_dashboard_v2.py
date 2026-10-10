"""GET /api/v2/dashboard and GET /api/v2/summary (concept E3)."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from tests.test_routers.conftest import make_client

SECTIONS = ("summary", "health", "internet", "incidents", "topology", "groups", "upcoming",
            "latency", "syslog", "availability", "since_last_visit")


@pytest.fixture(autouse=True)
def _fresh_caches():
    from services import dashboard_v2, predictions
    from services.topology import invalidate_topology_cache

    invalidate_topology_cache()
    dashboard_v2.clear_caches()
    predictions._disk_pred_cache = None
    predictions._agent_pred_cache = None
    yield
    invalidate_topology_cache()
    dashboard_v2.clear_caches()
    predictions._disk_pred_cache = None
    predictions._agent_pred_cache = None


@contextmanager
def fake_clickhouse(latest=None, latency=None, syslog=None, availability=None, recent=0):
    """Dispatch ClickHouse queries by shape; anything else returns []."""
    async def query(sql, params=None):
        if "argMax(success" in sql:
            ids = set((params or {}).get("hids") or [])
            return [dict(r) for hid, r in (latest or {}).items() if not ids or hid in ids]
        if "toStartOfMinute" in sql:
            return latency or []
        if "toStartOfFifteenMinutes" in sql:
            return syslog or []
        if "uniqExact(host_id)" in sql:
            return availability or []
        return []

    async def scalar(sql, params=None):
        return recent

    with patch("services.clickhouse_client.query", side_effect=query), \
         patch("services.clickhouse_client.query_scalar", side_effect=scalar):
        yield


def _row(hid, ok=True, latency=1.0, age_s=20):
    return {"host_id": hid, "_ts": datetime.utcnow() - timedelta(seconds=age_s),
            "success": 1 if ok else 0, "latency_ms": latency, "host_name": ""}


async def test_empty_install_returns_every_section_without_errors():
    async with make_client() as (client, _sf):
        with fake_clickhouse():
            resp = await client.get("/api/v2/dashboard")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["errors"] == []
        for s in SECTIONS:
            assert s in data
        assert data["internet"] is None  # no speedtest/unifi integration: null, not zeros
        assert data["latency"] is None
        assert data["availability"]["status"] == "no_data"
        assert data["availability"]["pct"] is None
        assert data["health"]["totals"]["hosts"] == 0
        assert data["since_last_visit"]["fallback"] is True
        assert data["groups"][0]["kind"] == "direct"


async def _scenario(sf):
    from models.agent import Agent
    from models.incident import Incident
    from models.integration import IntegrationConfig, Snapshot
    from models.maintenance import MaintenanceWindow
    from models.ping import PingHost

    now = datetime.utcnow()
    async with sf() as db:
        probe = Agent(name="probe-bern-01", token="p" * 64, is_probe=True, probe_interval_seconds=60,
                      last_seen=now - timedelta(minutes=11), created_at=now - timedelta(days=40))
        db.add(probe)
        db.add(Agent(name="SRV-RDS-01", token="a" * 64, last_seen=now))
        await db.flush()
        fw = PingHost(name="fw-zh-01", hostname="10.0.0.1")
        db.add(fw)
        await db.flush()
        sw = PingHost(name="SW-ZH-CORE-02", hostname="10.0.0.2", parent_id=fw.id)
        db.add(sw)
        await db.flush()
        pcs = [PingHost(name=f"PC-{i}", hostname=f"10.0.1.{i}", parent_id=sw.id) for i in range(3)]
        nas = PingHost(name="NAS-ZH-01", hostname="10.0.0.20")
        web = PingHost(name="web-01", hostname="https://web.example", check_type="https",
                       ssl_expiry_days=9, port_error=True)
        bern = [PingHost(name=f"BE-{i}", hostname=f"10.2.0.{i}", probe_id=probe.id) for i in range(2)]
        off = PingHost(name="old", hostname="10.9.9.9", enabled=False)
        db.add_all(pcs + [nas, web, off] + bern)
        await db.flush()
        inc = Incident(rule="upstream_failure", title="Upstream failure: 3 hosts affected",
                       severity="critical", status="open", created_at=now - timedelta(minutes=25),
                       host_ids=json.dumps(sorted([sw.id] + [p.id for p in pcs])))
        warn = Incident(rule="port_error", title="web-01: HTTPS failed", severity="warning",
                        status="acknowledged", created_at=now - timedelta(minutes=6),
                        host_ids=json.dumps([web.id]))
        done = Incident(rule="port_error", title="HTTP 503 on intranet", severity="warning",
                        status="resolved", created_at=now - timedelta(minutes=50),
                        resolved_at=now - timedelta(minutes=5))
        db.add_all([inc, warn, done])
        db.add(MaintenanceWindow(name="NAS firmware update", kind="once",
                                 starts_at=now - timedelta(minutes=30), ends_at=now + timedelta(minutes=30),
                                 host_ids=json.dumps([nas.id])))
        st = IntegrationConfig(type="speedtest", name="Speedtest", config_json="{}")
        db.add(st)
        await db.flush()
        for i in range(30):
            db.add(Snapshot(entity_type="speedtest", entity_id=st.id, ok=True,
                            timestamp=now - timedelta(hours=30 - i),
                            data_json=json.dumps({"download_mbps": 900 + i, "upload_mbps": 98,
                                                  "ping_ms": 6, "server_name": "Init7"})))
        broken = IntegrationConfig(type="pihole", name="pihole", config_json="{}")
        db.add(broken)
        await db.flush()
        db.add(Snapshot(entity_type="pihole", entity_id=broken.id, ok=False, error="timeout"))
        await db.commit()
        return {"fw": fw.id, "sw": sw.id, "pcs": [p.id for p in pcs], "nas": nas.id, "web": web.id,
                "bern": [b.id for b in bern], "inc": inc.id, "warn": warn.id, "probe": probe.id}


def _latest_for(ids):
    latest = {ids["fw"]: _row(ids["fw"]), ids["sw"]: _row(ids["sw"], ok=False),
              ids["nas"]: _row(ids["nas"]), ids["web"]: _row(ids["web"])}
    for p in ids["pcs"]:
        latest[p] = _row(p, ok=False)
    for b in ids["bern"]:
        latest[b] = _row(b)  # fresh-looking rows, but the probe is silent
    return latest


async def test_scenario_dashboard_sections():
    async with make_client() as (client, sf):
        ids = await _scenario(sf)
        minute = datetime.utcnow().replace(second=0, microsecond=0)
        latency = [{"minute": minute - timedelta(minutes=40), "median_ms": 0.3, "max_ms": 0.5, "ok": 4, "failed": 0},
                   {"minute": minute - timedelta(minutes=5), "median_ms": 14.2, "max_ms": 40.0, "ok": 2, "failed": 2}]
        syslog = [{"bucket": minute - timedelta(minutes=30), "cnt": 150, "errors": 4},
                  {"bucket": minute - timedelta(minutes=15), "cnt": 690, "errors": 20}]
        with fake_clickhouse(latest=_latest_for(ids), latency=latency, syslog=syslog,
                             availability=[{"total": 100000, "ok": 99940, "hosts": 9}], recent=690):
            data = (await client.get("/api/v2/dashboard")).json()

        assert data["errors"] == [], data["errors"]
        h = data["health"]
        assert h["counts"]["down"] == 4
        assert h["counts"]["unknown"] == 2  # behind the silent probe — never "up"
        assert h["counts"]["maintenance"] == 1
        assert h["counts"]["warning"] == 1
        assert h["counts"]["up"] == 1
        assert h["totals"]["hosts"] == 9 and h["totals"]["disabled"] == 1
        assert h["totals"]["with_current_data"] == 6
        assert h["totals"]["integrations"] == 2 and h["totals"]["integrations_error"] == 1
        assert {"text": "behind SW-ZH-CORE-02", "count": 3} in h["reasons"]["down"]
        assert "probe-bern-01 silent" in h["reasons"]["unknown"][0]["text"]

        inet = data["internet"]
        assert inet["source"] == "speedtest"
        assert len(inet["history"]) == 24
        assert inet["latest"]["download_mbps"] == 929
        assert inet["latest"]["latency_ms"] == 6
        assert inet["wan"] is None  # no UniFi: not derivable, not invented

        inc = data["incidents"]
        assert inc["counts"]["open"] == 2 and inc["counts"]["critical"] == 1
        assert inc["counts"]["acknowledged"] == 1
        assert [i["id"] for i in inc["items"]] == [ids["inc"], ids["warn"]]
        assert inc["items"][0]["host_count"] == 4
        assert inc["items"][1]["acknowledged"] is True
        assert len(inc["per_day"]) == 14 and inc["per_day"][-1]["count"] == 3
        assert [r["title"] for r in inc["resolved_today"]] == ["HTTP 503 on intranet"]

        topo = data["topology"]
        sw = next(p for p in topo["parents"] if p["id"] == ids["sw"])
        assert sw["child_count"] == 3 and sw["affected"] is True
        assert sw["worst_state"] == "down"
        assert len(sw["children"]) == 3
        assert sw["link_provenance"] == {"manual": 3}
        assert topo["roots"][0]["id"] == ids["fw"]

        groups = {(g["kind"], g["name"]): g for g in data["groups"]}
        assert groups[("direct", "Direct")]["host_count"] == 7
        bern = groups[("probe", "probe-bern-01")]
        assert bern["fresh"] is False and bern["by_state"] == {"unknown": 2}

        kinds = [(u["kind"], u.get("phase")) for u in data["upcoming"]["items"]]
        assert ("maintenance", "active") in kinds
        assert ("certificate", None) in kinds

        lat = data["latency"]
        assert lat["incident_id"] == ids["inc"]
        assert lat["median_before_ms"] == 0.3 and lat["median_since_ms"] == 14.2
        assert lat["points"][1]["failed"] == 2

        sl = data["syslog"]
        assert sl["total_24h"] == 840 and sl["errors_24h"] == 24
        assert sl["current_per_min"] == 46.0
        assert sl["usual"] is None  # no learned baselines yet

        av = data["availability"]
        assert av["pct"] == 99.94 and av["target_pct"] == 99.9
        assert av["status"] == "above_target"
        assert av["budget_minutes"] == 43.2
        assert av["downtime_minutes"] == 25.9

        assert data["summary"]["incidents"]["open"] == 2
        assert data["summary"]["probes"]["stale"] == 1


async def test_summary_matches_dashboard_counts():
    async with make_client() as (client, sf):
        ids = await _scenario(sf)
        with fake_clickhouse(latest=_latest_for(ids)):
            summary = (await client.get("/api/v2/summary")).json()
            dash = (await client.get("/api/v2/dashboard")).json()
        assert summary["incidents"] == dash["summary"]["incidents"]
        assert summary["hosts"]["by_state"] == dash["health"]["counts"]
        assert summary["integrations"]["error"] == 1
        assert summary["probes"]["stale"] == 1
        assert summary["hosts"]["attention"] == 4 + 1 + 2  # down + warning + unknown


async def test_availability_target_setting():
    from models.settings import Setting

    async with make_client() as (client, sf):
        async with sf() as db:
            db.add(Setting(key="availability_target", value="99.99"))
            from models.ping import PingHost
            db.add(PingHost(name="a", hostname="10.0.0.1"))
            await db.commit()
        with fake_clickhouse(availability=[{"total": 1000, "ok": 999, "hosts": 1}]):
            av = (await client.get("/api/v2/dashboard")).json()["availability"]
        assert av["target_pct"] == 99.99
        assert av["status"] == "below_target"


async def test_since_last_visit_uses_the_stored_visit():
    from models.incident import Incident

    async with make_client() as (client, sf):
        with fake_clickhouse():
            async with sf() as db:
                db.add(Incident(rule="r", title="before the visit", severity="warning", status="open",
                                created_at=datetime.utcnow() - timedelta(minutes=5)))
                await db.commit()
            await client.post("/api/v2/me/seen")
            async with sf() as db:
                db.add(Incident(rule="r", title="new one", severity="warning", status="open",
                                created_at=datetime.utcnow()))
                await db.commit()
            data = (await client.get("/api/v2/dashboard")).json()
        assert data["previous_seen_at"] is not None
        slv = data["since_last_visit"]
        assert slv["fallback"] is False
        assert slv["counts"]["incident_opened"] == 1
        assert slv["items"][0]["title"].endswith("new one")


async def test_one_failing_section_does_not_fail_the_page():
    async with make_client() as (client, _sf):
        with fake_clickhouse(), patch("services.dashboard_v2.section_syslog",
                                      side_effect=RuntimeError("clickhouse down")):
            resp = await client.get("/api/v2/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["syslog"] is None
        assert data["errors"] == [{"section": "syslog", "error": "RuntimeError"}]
        assert data["health"] is not None


async def test_unifi_wan_and_speedtest_fallback():
    from models.integration import IntegrationConfig, Snapshot

    async with make_client() as (client, sf):
        async with sf() as db:
            cfg = IntegrationConfig(type="unifi", name="UDM", config_json="{}")
            db.add(cfg)
            await db.flush()
            db.add(Snapshot(entity_type="unifi", entity_id=cfg.id, ok=True, data_json=json.dumps({
                "wan": {"status": "ok", "latency": 7},
                "speedtest": [{"timestamp": "2026-10-10 14:00", "download_mbps": 940.0,
                               "upload_mbps": 99.0, "latency_ms": 6}],
                "devices": [], "clients": [],
            })))
            await db.commit()
        with fake_clickhouse():
            inet = (await client.get("/api/v2/dashboard")).json()["internet"]
        assert inet["source"] == "unifi"
        assert inet["wan"]["status"] == "up" and inet["wan"]["latency_ms"] == 7
        assert inet["latest"]["download_mbps"] == 940.0


async def test_upcoming_lists_disk_predictions_from_the_cache():
    from services import predictions

    predictions.store_predictions("agent", {
        "agent-3:D:": {"agent_id": 3, "agent_name": "SRV-FILE-01", "hostname": "SRV-FILE-01",
                       "pool_name": "D:", "current_pct": 91.0, "trend_pct_per_day": 1.8,
                       "days_until_full": 5, "confidence": 0.9, "source": "Agent: SRV-FILE-01"},
        "agent-3:C:": {"agent_id": 3, "agent_name": "SRV-FILE-01", "pool_name": "C:",
                       "current_pct": 40.0, "days_until_full": 400, "confidence": 0.9},
        "agent-4:E:": {"agent_id": 4, "agent_name": "noisy", "pool_name": "E:",
                       "current_pct": 70.0, "days_until_full": 3, "confidence": 0.1},
    })
    async with make_client() as (client, _sf):
        with fake_clickhouse():
            up = (await client.get("/api/v2/dashboard")).json()["upcoming"]
    disks = [i for i in up["items"] if i["kind"] == "disk"]
    assert [d["title"] for d in disks] == ["SRV-FILE-01 · D: 91.0 %"]
    assert disks[0]["days"] == 5 and disks[0]["estimated_due"] is True
    assert up["agent_disk_predictions_ready"] is True


async def test_prediction_refresh_job_fills_both_caches():
    from unittest.mock import AsyncMock

    from services import predictions

    with patch.object(predictions, "predict_disk_full", new=AsyncMock(return_value={"a": {}})), \
         patch.object(predictions, "predict_agent_disks", new=AsyncMock(return_value={"b": {}})):
        await predictions.refresh_cache(None)
    assert predictions.cached_predictions("integration", 60) == {"a": {}}
    assert predictions.cached_predictions("agent", 60) == {"b": {}}


async def test_query_count_does_not_grow_with_hosts_or_incidents():
    """No N+1: the number of SQL statements is the same for 5 and 120 hosts."""
    from models.incident import Incident
    from models.ping import PingHost

    async def run(n):
        from services import dashboard_v2, predictions
        from services.topology import invalidate_topology_cache

        invalidate_topology_cache()
        dashboard_v2.clear_caches()
        predictions._disk_pred_cache = None
        async with make_client() as (client, sf):
            async with sf() as db:
                hosts = [PingHost(name=f"h{i}", hostname=f"10.1.{i // 250}.{i % 250}") for i in range(n)]
                db.add_all(hosts)
                await db.flush()
                for i in range(0, n, 5):
                    db.add(Incident(rule="port_error", title=f"i{i}", severity="warning", status="open",
                                    host_ids=json.dumps([hosts[i].id])))
                await db.commit()
            statements = []
            engine = sf.kw["bind"].sync_engine

            def count(*_a, **_k):
                statements.append(1)

            event.listen(engine, "before_cursor_execute", count)
            try:
                with fake_clickhouse(latest={h.id: _row(h.id) for h in hosts}):
                    resp = await client.get("/api/v2/dashboard")
            finally:
                event.remove(engine, "before_cursor_execute", count)
            assert resp.status_code == 200 and resp.json()["errors"] == []
            return len(statements)

    small = await run(5)
    large = await run(120)
    assert large == small, (small, large)

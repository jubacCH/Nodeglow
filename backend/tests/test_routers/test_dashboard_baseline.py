"""The anomaly baseline is parsed once per snapshot, not once per request.

Even sampled, the baseline meant ~120 snapshots and 11 MB of JSON fetched and
parsed on every dashboard load (~100 ms on production). Snapshot rows never
change, so the extracted guest metrics are cached per snapshot id.
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from models.integration import IntegrationConfig, Snapshot
from routers import dashboard
from routers.dashboard import extract_guest_metrics, load_guest_baseline
from tests.test_routers.conftest import make_client

NOW = datetime(2026, 10, 10, 12, 0)


@pytest.fixture(autouse=True)
def _empty_cache():
    dashboard._baseline_cache.clear()
    yield
    dashboard._baseline_cache.clear()


def _payload(cpu: float, mem: float = 1.0) -> dict:
    return {
        "nodes": [{"node": "pve1"}],
        "vms": [{"id": 100, "name": "vm-a", "type": "qemu", "node": "pve1", "running": True,
                 "cpu_pct": cpu, "mem_used_gb": mem, "mem_total_gb": 8}],
        "containers": [{"id": 200, "name": "ct-b", "type": "lxc", "node": "pve1", "running": True,
                        "cpu_pct": 1.0, "mem_used_gb": 0.5, "mem_total_gb": 2}],
    }


def _snap(cluster_id: int, ts: datetime, cpu: float) -> Snapshot:
    return Snapshot(entity_type="proxmox", entity_id=cluster_id, ok=True,
                    timestamp=ts, data_json=json.dumps(_payload(cpu)))


def _payload_queries(db) -> list[tuple[str, tuple]]:
    """Record every SELECT that reads data_json, with its parameters."""
    seen: list[tuple[str, tuple]] = []

    @event.listens_for(db.bind.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "data_json" in statement:
            seen.append((statement, tuple(params or ())))

    return seen


def test_extract_guest_metrics_covers_vms_and_containers():
    data = {
        "vms": [{"id": 1, "cpu_pct": 5.0, "mem_used_gb": 2.0}, {"name": "no id"}],
        "containers": [{"id": 2}],
    }
    assert extract_guest_metrics(data) == [(1, 5.0, 2.0), (2, 0, 0)]
    assert extract_guest_metrics({}) == []


async def test_series_is_oldest_first_per_guest(db):
    for i in range(10):
        db.add(_snap(1, NOW - timedelta(minutes=60 - i), cpu=float(i)))
    await db.commit()

    series = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)

    assert series[100]["cpu"] == [float(i) for i in range(10)]
    assert series[100]["mem"] == [1.0] * 10
    assert series[200]["cpu"] == [1.0] * 10


async def test_second_call_does_not_reload_payloads(db):
    for i in range(10):
        db.add(_snap(1, NOW - timedelta(minutes=60 - i), cpu=float(i)))
    await db.commit()
    queries = _payload_queries(db)

    first = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)
    assert len(queries) == 1
    second = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)

    assert len(queries) == 1, "cached snapshots must not be fetched again"
    assert second == first


async def test_only_new_snapshots_are_fetched(db):
    for i in range(10):
        db.add(_snap(1, NOW - timedelta(minutes=60 - i), cpu=float(i)))
    await db.commit()
    await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)

    newer = _snap(1, NOW + timedelta(minutes=1), cpu=50.0)
    db.add(newer)
    await db.commit()
    queries = _payload_queries(db)

    series = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW + timedelta(minutes=5))

    assert series[100]["cpu"][-1] == 50.0
    assert len(queries) == 1
    assert list(queries[0][1]) == [newer.id], "only the new snapshot is loaded"


async def test_snapshots_leaving_the_window_are_evicted(db):
    for i in range(10):
        db.add(_snap(1, NOW - timedelta(minutes=60 - i), cpu=float(i)))
    await db.commit()
    await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)
    assert len(dashboard._baseline_cache[1]) == 10

    # Window now only covers the last five.
    series = await load_guest_baseline(db, 1, NOW - timedelta(minutes=55), NOW)

    assert series[100]["cpu"] == [5.0, 6.0, 7.0, 8.0, 9.0]
    assert len(dashboard._baseline_cache[1]) == 5


async def test_a_reused_id_with_another_timestamp_is_refetched(db):
    """A fresh database (restore, tests) can hand out an id the cache has seen."""
    snap = _snap(1, NOW - timedelta(minutes=10), cpu=3.0)
    db.add(snap)
    await db.commit()
    dashboard._baseline_cache[1] = {snap.id: (NOW - timedelta(days=3), [(100, 99.0, 9.0)])}

    series = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)

    assert series[100]["cpu"] == [3.0]


async def test_clusters_are_cached_separately(db):
    db.add(_snap(1, NOW - timedelta(minutes=10), cpu=3.0))
    db.add(_snap(2, NOW - timedelta(minutes=10), cpu=7.0))
    await db.commit()

    one = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)
    two = await load_guest_baseline(db, 2, NOW - timedelta(hours=24), NOW)

    assert one[100]["cpu"] == [3.0]
    assert two[100]["cpu"] == [7.0]


async def test_sample_is_capped_at_the_budget(db):
    for i in range(dashboard.BASELINE_SAMPLE_BUDGET * 2):
        db.add(_snap(1, NOW - timedelta(minutes=600 - i), cpu=float(i)))
    await db.commit()

    series = await load_guest_baseline(db, 1, NOW - timedelta(hours=24), NOW)

    cpu = series[100]["cpu"]
    assert len(cpu) <= dashboard.BASELINE_SAMPLE_BUDGET
    # The newest points stay exact and contiguous.
    newest = dashboard.BASELINE_SAMPLE_BUDGET * 2 - 1
    assert cpu[-dashboard.BASELINE_KEEP_NEWEST:] == [
        float(v) for v in range(newest - dashboard.BASELINE_KEEP_NEWEST + 1, newest + 1)
    ]


async def test_dashboard_anomaly_is_stable_across_requests():
    """End to end: a CPU spike is reported, and the cached second request agrees."""
    now = datetime.utcnow()
    async with make_client() as (client, session_factory):
        async with session_factory() as db:
            cfg = IntegrationConfig(type="proxmox", name="pve", config_json="{}")
            db.add(cfg)
            await db.flush()
            # Quiet baseline, then three sustained high readings and the spike.
            for i in range(20):
                db.add(_snap(cfg.id, now - timedelta(minutes=60 - i), cpu=5.0))
            for i in range(3):
                db.add(_snap(cfg.id, now - timedelta(minutes=10 - i), cpu=80.0))
            db.add(_snap(cfg.id, now - timedelta(minutes=1), cpu=90.0))
            await db.commit()

        first = await client.get("/api/dashboard")
        second = await client.get("/api/dashboard")

    assert first.status_code == 200 and second.status_code == 200
    cpu_anomalies = [a for a in first.json()["anomalies"] if a["metric"] == "CPU"]
    assert [a["name"] for a in cpu_anomalies] == ["vm-a"]
    assert cpu_anomalies[0]["current"] == 90.0
    assert first.json()["anomalies"] == second.json()["anomalies"]
    assert first.json()["storage_pools"] == second.json()["storage_pools"]

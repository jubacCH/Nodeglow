"""Rollup readers: level choice, SQL shape and empty-bucket handling."""
from __future__ import annotations

from datetime import datetime

import pytest

from services import clickhouse_client as ch
from services import rollups
from tests.test_services.test_clickhouse_helpers import (  # noqa: F401
    _FakeQuery,
    _tenant_context,
)

T0 = datetime(2026, 11, 1, 12, 0, 0)


@pytest.fixture
def fake_query(monkeypatch):
    q = _FakeQuery()
    monkeypatch.setattr(ch, "query", q)
    return q


@pytest.fixture
def ready_all(monkeypatch):
    async def ready(source, c=None):
        return True
    monkeypatch.setattr(rollups, "ready", ready)


@pytest.fixture
def ready_none(monkeypatch):
    async def ready(source, c=None):
        return False
    monkeypatch.setattr(rollups, "ready", ready)


@pytest.mark.parametrize("hours,level", [(1, "raw"), (48, "raw"), (49, "5m"), (720, "5m"),
                                         (721, "1h"), (9600, "1h"), (9601, "1d")])
def test_pick_level(hours, level):
    assert rollups.pick_level(hours) == level


async def test_effective_level_follows_readiness(ready_all, monkeypatch):
    assert await rollups.effective_level("ping_checks", 100) == "5m"
    assert await rollups.effective_level("ping_checks", 10) == "raw"

    async def no(source, c=None):
        return False
    monkeypatch.setattr(rollups, "ready", no)
    assert await rollups.effective_level("ping_checks", 100) == "raw"
    assert await rollups.effective_level("ping_checks", 20000) == "raw"


async def test_ping_series_reads_5m_with_tenant_filter(fake_query, ready_all):
    res = await rollups.ping_series(4, hours=24 * 7)
    sql = fake_query.calls[0][0]
    assert "FROM ping_checks_5m" in sql and ch.TENANT_FILTER in sql
    assert "quantileTDigestMerge(0.95)(lat_p95)" in sql.replace(" ", "")
    assert "GROUP BY bucket" in sql
    assert res.level == "5m" and res.bucket_seconds == 300


async def test_series_falls_back_to_raw_when_level_not_ready(fake_query, ready_none):
    res = await rollups.agent_series(2, hours=24 * 7)
    assert res.level == "raw" and "FROM agent_metrics " in fake_query.calls[0][0]
    assert res.bucket_seconds == ch.bandwidth_bucket_seconds(24 * 7)


async def test_raw_level_uses_chart_bucket_width(fake_query, ready_all):
    res = await rollups.ping_series(4, hours=6)
    assert res.level == "raw" and res.bucket_seconds == 300
    assert "FROM ping_checks " in fake_query.calls[0][0]
    assert fake_query.calls[0][1]["b"] == 300


async def test_bandwidth_total_uses_rollup_beyond_48h(fake_query, ready_all):
    await ch.get_bandwidth_total_history_ch(hours=24 * 90)
    sql, params = fake_query.calls[0]
    assert "FROM bandwidth_metrics_1h" in sql
    assert params["b"] == 3600


async def test_bandwidth_total_stays_raw_when_not_ready(fake_query, ready_none):
    await ch.get_bandwidth_total_history_ch(hours=24 * 90)
    assert "bandwidth_metrics_" not in fake_query.calls[0][0]


async def test_ping_series_empty_bucket_is_none_not_nan(fake_query, ready_all):
    nan = float("nan")
    fake_query.returns([
        {"bucket": T0, "checks": 3, "ok": 0, "lat_n": 0, "latency_avg": None,
         "latency_min": None, "latency_max": None, "latency_p95": nan},
        {"bucket": T0, "checks": 4, "ok": 3, "lat_n": 3, "latency_avg": 5.0,
         "latency_min": 4.0, "latency_max": 6.0, "latency_p95": 5.9},
    ])
    res = await rollups.ping_series(1, hours=100)
    assert res.points[0] == {"timestamp": T0, "checks": 3, "ok": 0, "latency_avg": None,
                             "latency_min": None, "latency_max": None, "latency_p95": None}
    assert res.points[1]["latency_p95"] == 5.9 and res.points[1]["latency_avg"] == 5.0


async def test_agent_series_empty_metric_is_none(fake_query, ready_all):
    nan = float("nan")
    row = {"bucket": T0}
    for m in ("cpu_pct", "mem_pct", "disk_pct", "load_1"):
        n = 0 if m == "mem_pct" else 2
        row.update({f"{m}_n": n, f"{m}_avg": 1.0, f"{m}_min": 0.5, f"{m}_max": 2.0,
                    f"{m}_p95": nan if n == 0 else 1.9})
    fake_query.returns([row])
    res = await rollups.agent_series(1, hours=100)
    p = res.points[0]
    assert p["mem_pct_p95"] is None and p["mem_pct_avg"] is None and p["mem_pct_max"] is None
    assert p["cpu_pct_p95"] == 1.9 and "cpu_pct_n" not in p
    assert "FROM agent_metrics_5m" in fake_query.calls[0][0]


async def test_bandwidth_series_keys(fake_query, ready_all):
    fake_query.returns([{"bucket": T0, "rx_rate_avg": 1.5, "tx_rate_avg": 2.5,
                         "rx_rate_max": 3, "tx_rate_max": 4}])
    res = await rollups.bandwidth_series("agent", "7", "total", hours=24 * 30)
    assert res.points == [{"timestamp": T0, "rx_rate_avg": 1.5, "tx_rate_avg": 2.5,
                           "rx_rate_max": 3, "tx_rate_max": 4}]
    assert "FROM bandwidth_metrics_5m" in fake_query.calls[0][0]

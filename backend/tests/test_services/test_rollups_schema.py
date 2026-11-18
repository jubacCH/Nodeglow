from datetime import datetime

import pytest

from services import rollups


def test_nine_tables():
    assert len(rollups.ROLLUP_TABLES) == 9
    assert "agent_metrics_1h" in rollups.ROLLUP_TABLES


@pytest.mark.parametrize("level,days", [("5m", 90), ("1h", 400), ("1d", 1825)])
def test_table_ddl_ttl_and_sort_key(level, days):
    ddl = rollups.table_ddl("ping_checks", rollups.level(level))
    assert "ORDER BY (tenant_id, host_id, bucket)" in " ".join(ddl.split())
    assert f"INTERVAL {days} DAY" in ddl
    assert "AggregatingMergeTree" in ddl
    assert "tenant_id UInt32," in " ".join(ddl.split())  # no DEFAULT


def test_view_filters_on_cutoff():
    cut = datetime(2026, 11, 19, 0, 0)
    ddl = " ".join(rollups.view_ddl("bandwidth_metrics", rollups.level("5m"), cut).split())
    assert "TO bandwidth_metrics_5m" in ddl
    assert "timestamp >= toDateTime64('2026-11-19 00:00:00', 3, 'UTC')" in ddl
    assert "FROM bandwidth_metrics " in ddl


def test_latency_aggregates_only_successful_checks():
    sel = rollups.select_aggregates("ping_checks", rollups.level("1h")).replace(" ", "")
    assert "quantileTDigestStateIf(0.95)(latency_ms,success=1)" in sel


def test_level_unknown_key_raises():
    with pytest.raises(KeyError):
        rollups.level("2h")


def test_daily_partitions_by_year():
    ddl = rollups.table_ddl("agent_metrics", rollups.level("1d"))
    assert "PARTITION BY toYear(bucket)" in ddl
    assert "cpu_pct_p95" in ddl and "load_1_max" in ddl

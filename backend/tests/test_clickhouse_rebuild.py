"""Step-B rebuild (services/clickhouse_rebuild.py): the decisions, without a server.

The copy itself — partition-wise, resumable, counts preserved, the exchange —
runs against a real ClickHouse in tests/test_live_clickhouse.py.
"""
from __future__ import annotations

import pytest

from services import clickhouse_rebuild as rb
from services.clickhouse_rebuild import Part, Progress, plan


def _parts(*ranges, rows=10):
    return [Part(f"p_{a}_{b}_0", a, b, rows, 100) for a, b in ranges]


# ── plan(): what a partition needs ───────────────────────────────────────────


def test_never_copied_partition_is_copied_in_full():
    assert plan(_parts((1, 1)), None, None, None) == "full"
    assert plan(_parts((1, 1)), Progress(), 0, 0) == "full"
    assert plan([], Progress(), 0, 0) == "in_sync"


def test_unchanged_partition_is_in_sync_even_after_merges():
    # Copied blocks 1..5; since then parts 1..3 and 4..5 were merged: same rows.
    assert plan(_parts((1, 5)), Progress(5, 50), 50, 50) == "in_sync"


def test_parts_above_the_copied_block_are_appended():
    assert plan(_parts((1, 5), (6, 6), (7, 9)), Progress(5, 50), 50, 50) == "append"


def test_a_part_merged_across_the_boundary_forces_a_full_copy():
    # Blocks 4..6 mix copied (<=5) and new (6) rows: cannot be split.
    assert plan(_parts((1, 3), (4, 6)), Progress(5, 50), 30, 50) == "full"


def test_rows_that_no_longer_add_up_force_a_full_copy():
    # TTL or a DELETE removed rows from the copied blocks.
    assert plan(_parts((1, 5)), Progress(5, 50), 42, 50) == "full"
    # The copy itself is not what was recorded (a copy that read parts merged
    # away under it, or a server restart re-enabled TTL on the work table).
    assert plan(_parts((1, 5)), Progress(5, 50), 50, 49) == "full"


def test_after_the_exchange_the_live_table_count_is_not_consulted():
    assert plan(_parts((1, 5), (6, 6)), Progress(5, 50), 50, None) == "append"


# ── The rebuilt engine clause ────────────────────────────────────────────────


def test_rebuilt_engine_prepends_tenant_id():
    engine = (
        "MergeTree PARTITION BY toYYYYMMDD(timestamp) ORDER BY (host_id, timestamp) "
        "TTL toDateTime(timestamp) + toIntervalDay(30) "
        "SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1"
    )
    assert rb.rebuilt_engine(engine) == (
        "MergeTree PARTITION BY toYYYYMMDD(timestamp) ORDER BY (tenant_id, host_id, timestamp) "
        "TTL toDateTime(timestamp) + toIntervalDay(30) "
        "SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1"
    )


def test_rebuilt_engine_keeps_row_level_ttl_and_nested_parentheses():
    engine = (
        "MergeTree PARTITION BY toYYYYMM(timestamp) ORDER BY (severity, source_ip, toDate(timestamp)) "
        "TTL toDateTime(timestamp) + toIntervalDay(1) WHERE (severity = 7) AND (noise_score >= 80), "
        "toDateTime(timestamp) + toIntervalDay(90) WHERE severity IN (0, 1, 2, 3) "
        "SETTINGS index_granularity = 8192, ttl_only_drop_parts = 0"
    )
    out = rb.rebuilt_engine(engine)
    assert "ORDER BY (tenant_id, severity, source_ip, toDate(timestamp)) TTL" in out
    assert out.endswith("WHERE severity IN (0, 1, 2, 3) SETTINGS index_granularity = 8192, ttl_only_drop_parts = 0")


def test_rebuilt_engine_single_column_key_and_refusals():
    assert rb.rebuilt_engine("MergeTree ORDER BY host_id SETTINGS index_granularity = 8192") == (
        "MergeTree ORDER BY (tenant_id, host_id) SETTINGS index_granularity = 8192"
    )
    with pytest.raises(rb.RebuildRefused):
        rb.rebuilt_engine("MergeTree PRIMARY KEY host_id ORDER BY (host_id, timestamp)")
    with pytest.raises(rb.RebuildRefused):
        rb.rebuilt_engine("Memory")


def test_column_section_drops_the_step_a_default():
    query = (
        "CREATE TABLE nodeglow.ping_checks (`timestamp` DateTime64(3, 'UTC'), `host_id` UInt32, "
        "`probe_id` UInt32 DEFAULT 0, `tenant_id` UInt32 DEFAULT 1, "
        "INDEX idx_x host_id TYPE minmax GRANULARITY 1) ENGINE = MergeTree "
        "PARTITION BY toYYYYMMDD(timestamp) ORDER BY (host_id, timestamp)"
    )
    assert rb.column_section(query) == (
        "(`timestamp` DateTime64(3, 'UTC'), `host_id` UInt32, `probe_id` UInt32 DEFAULT 0, "
        "`tenant_id` UInt32, INDEX idx_x host_id TYPE minmax GRANULARITY 1)"
    )
    assert rb.column_section("garbage") is None


def test_leads_with_tenant():
    assert rb.leads_with_tenant("tenant_id, host_id, timestamp")
    assert not rb.leads_with_tenant("host_id, timestamp")
    assert not rb.leads_with_tenant("tenant_id_x, host_id")
    assert not rb.leads_with_tenant("")


# ── Preflight and the job ────────────────────────────────────────────────────


class _NoClient:
    async def command(self, *a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("preflight must refuse before any DDL")


async def test_preflight_refuses_without_twice_the_free_disk(monkeypatch):
    async def info(c, table):
        if table == "ping_checks":
            return {"engine": "MergeTree", "db_engine": "Atomic",
                    "engine_full": "MergeTree ORDER BY (host_id, timestamp)",
                    "sorting_key": "host_id, timestamp"}
        return None

    async def has_column(c, table, col):
        return True

    async def size(c, table):
        return 1000

    async def free(c, table):
        return 1999
    monkeypatch.setattr(rb, "table_info", info)
    monkeypatch.setattr(rb, "_has_column", has_column)
    monkeypatch.setattr(rb, "table_bytes", size)
    monkeypatch.setattr(rb, "free_bytes", free)
    with pytest.raises(rb.RebuildRefused, match="needs twice"):
        await rb.rebuild_table(_NoClient(), "ping_checks")


@pytest.mark.parametrize("override, reason", [
    ({"engine": "ReplicatedMergeTree"}, "only MergeTree"),
    ({"db_engine": "Ordinary"}, "EXCHANGE"),
])
async def test_preflight_refuses_unsupported_tables(monkeypatch, override, reason):
    async def info(c, table):
        if table == "ping_checks":
            return {"engine": "MergeTree", "db_engine": "Atomic",
                    "engine_full": "MergeTree ORDER BY (host_id, timestamp)",
                    "sorting_key": "host_id, timestamp", **override}
        return None
    monkeypatch.setattr(rb, "table_info", info)
    with pytest.raises(rb.RebuildRefused, match=reason):
        await rb.rebuild_table(_NoClient(), "ping_checks")


async def test_preflight_refuses_without_step_a(monkeypatch):
    async def info(c, table):
        return {"engine": "MergeTree", "db_engine": "Atomic", "engine_full": "",
                "sorting_key": "host_id, timestamp"} if table == "ping_checks" else None

    async def has_column(c, table, col):
        return False
    monkeypatch.setattr(rb, "table_info", info)
    monkeypatch.setattr(rb, "_has_column", has_column)
    with pytest.raises(rb.RebuildRefused, match="step A"):
        await rb.rebuild_table(_NoClient(), "ping_checks")


async def test_rebuilt_and_fresh_tables_are_left_alone(monkeypatch):
    async def info(c, table):
        return {"sorting_key": "tenant_id, host_id, timestamp"} if table == "ping_checks" else None
    monkeypatch.setattr(rb, "table_info", info)
    assert await rb.rebuild_table(_NoClient(), "ping_checks") == "done"
    assert await rb.rebuild_table(_NoClient(), "agent_metrics") == "missing"


async def test_run_never_raises(monkeypatch):
    calls = []

    async def client():
        return object()

    async def progress_table(c):
        return None

    async def rebuild(c, table, **kw):
        calls.append(table)
        if table == "ping_checks":
            raise rb.RebuildRefused("no disk")
        if table == "agent_metrics":
            raise RuntimeError("boom")
        return "done"
    monkeypatch.setattr(rb, "_admin_client", client)
    monkeypatch.setattr(rb, "_ensure_progress_table", progress_table)
    monkeypatch.setattr(rb, "rebuild_table", rebuild)
    out = await rb.run()
    assert out["ping_checks"] == "refused: no disk"
    assert out["agent_metrics"].startswith("error: boom")
    assert out["syslog_messages"] == "done"
    assert calls == list(rb.ch.TENANT_TABLES)


async def test_run_is_off_when_disabled_and_quiet_without_clickhouse(monkeypatch, caplog):
    monkeypatch.setattr(rb, "MODE", "off")
    assert await rb.run() == {}

    monkeypatch.setattr(rb, "MODE", "auto")

    async def unreachable():
        raise ConnectionError("down")
    monkeypatch.setattr(rb, "_admin_client", unreachable)
    assert await rb.run() == {}
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

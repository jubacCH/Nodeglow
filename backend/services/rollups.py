"""Rollup schema as data: 5m / 1h / 1d aggregate tables and their views.

Every raw table (ping_checks, agent_metrics, bandwidth_metrics) is rolled up
into three AggregatingMergeTree tables. The DDL and the SELECT used by both
the materialized views and the backfill are generated from the specs below.

This module must not import ``services.clickhouse_client`` at module level:
that module imports ``ROLLUP_TABLES`` from here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Level:
    key: str
    seconds: int
    ttl_days: int


LEVELS: tuple[Level, ...] = (
    Level("5m", 300, 90),
    Level("1h", 3600, 400),
    Level("1d", 86400, 1825),
)


@dataclass(frozen=True)
class Agg:
    """One aggregate column: its name, storage type and the SELECT expression."""

    name: str
    type: str
    expr: str


@dataclass(frozen=True)
class Source:
    """A raw table: entity key columns (name, type) and its aggregate columns."""

    keys: tuple[str, ...]
    key_types: tuple[str, ...]
    aggregates: tuple[Agg, ...]


_SUM_U64 = "SimpleAggregateFunction(sum, UInt64)"
_SUM_F64 = "SimpleAggregateFunction(sum, Float64)"
_MIN_F32 = "SimpleAggregateFunction(min, Nullable(Float32))"
_MAX_F32 = "SimpleAggregateFunction(max, Nullable(Float32))"
_P95_F32 = "AggregateFunction(quantileTDigest(0.95), Float32)"

_AGENT_METRICS = ("cpu_pct", "mem_pct", "disk_pct", "load_1")


def _agent_aggregates() -> tuple[Agg, ...]:
    aggs: list[Agg] = []
    for m in _AGENT_METRICS:
        aggs += [
            Agg(f"{m}_sum", _SUM_F64, f"toFloat64(ifNull(sum({m}), 0))"),
            Agg(f"{m}_n", _SUM_U64, f"countIf({m} IS NOT NULL)"),
            Agg(f"{m}_min", _MIN_F32, f"min({m})"),
            Agg(f"{m}_max", _MAX_F32, f"max({m})"),
            Agg(f"{m}_p95", _P95_F32, f"quantileTDigestStateIf(0.95)(assumeNotNull({m}), isNotNull({m}))"),
        ]
    return tuple(aggs)


_LAT_OK = "success = 1 AND latency_ms IS NOT NULL"

SOURCES: dict[str, Source] = {
    "ping_checks": Source(
        keys=("host_id",),
        key_types=("UInt32",),
        aggregates=(
            Agg("checks", _SUM_U64, "count()"),
            Agg("ok", _SUM_U64, "countIf(success = 1)"),
            # Latency only counts successful checks.
            Agg("lat_sum", _SUM_F64, f"toFloat64(sumIf(ifNull(latency_ms, 0), {_LAT_OK}))"),
            Agg("lat_n", _SUM_U64, f"countIf({_LAT_OK})"),
            Agg("lat_min", _MIN_F32, "minIf(latency_ms, success = 1)"),
            Agg("lat_max", _MAX_F32, "maxIf(latency_ms, success = 1)"),
            Agg("lat_p95", _P95_F32, "quantileTDigestStateIf(0.95)(latency_ms, success = 1)"),
        ),
    ),
    "agent_metrics": Source(
        keys=("agent_id",),
        key_types=("UInt32",),
        aggregates=_agent_aggregates(),
    ),
    "bandwidth_metrics": Source(
        keys=("source_type", "source_id", "interface_name"),
        key_types=("LowCardinality(String)", "String", "LowCardinality(String)"),
        aggregates=(
            Agg("rx_rate_sum", _SUM_U64, "sum(rx_rate_bps)"),
            Agg("tx_rate_sum", _SUM_U64, "sum(tx_rate_bps)"),
            Agg("rate_n", _SUM_U64, "count()"),
            Agg("rx_rate_max", "SimpleAggregateFunction(max, UInt64)", "max(rx_rate_bps)"),
            Agg("tx_rate_max", "SimpleAggregateFunction(max, UInt64)", "max(tx_rate_bps)"),
            Agg("rx_bytes_max", "SimpleAggregateFunction(max, UInt64)", "max(rx_bytes)"),
            Agg("tx_bytes_max", "SimpleAggregateFunction(max, UInt64)", "max(tx_bytes)"),
        ),
    ),
}


def level(key: str) -> Level:
    for lv in LEVELS:
        if lv.key == key:
            return lv
    raise KeyError(key)


def rollup_table(source: str, level: str) -> str:
    return f"{source}_{level}"


ROLLUP_TABLES: tuple[str, ...] = tuple(
    rollup_table(s, lv.key) for s in SOURCES for lv in LEVELS
)


def table_ddl(source: str, level: Level) -> str:
    spec = SOURCES[source]
    cols = ["tenant_id UInt32", "bucket DateTime('UTC')"]
    cols += [f"{k} {t}" for k, t in zip(spec.keys, spec.key_types)]
    cols += [f"{a.name} {a.type}" for a in spec.aggregates]
    body = ",\n        ".join(cols)
    part = "toYear(bucket)" if level.key == "1d" else "toYYYYMM(bucket)"
    order = ", ".join(("tenant_id", *spec.keys, "bucket"))
    return f"""
    CREATE TABLE IF NOT EXISTS {rollup_table(source, level.key)}
    (
        {body}
    )
    ENGINE = AggregatingMergeTree()
    PARTITION BY {part}
    ORDER BY ({order})
    TTL bucket + INTERVAL {level.ttl_days} DAY
    SETTINGS ttl_only_drop_parts = 1
    """


def select_aggregates(source: str, level: Level) -> str:
    """SELECT ... GROUP BY body shared by the view and the backfill (no WHERE)."""
    spec = SOURCES[source]
    keys = ", ".join(spec.keys)
    cols = [
        "tenant_id",
        f"toStartOfInterval(timestamp, INTERVAL {level.seconds} SECOND, 'UTC') AS bucket",
        *spec.keys,
        *(f"{a.expr} AS {a.name}" for a in spec.aggregates),
    ]
    body = ",\n        ".join(cols)
    return f"""
    SELECT
        {body}
    FROM {source}
    GROUP BY tenant_id, {keys}, bucket
    """


def view_ddl(source: str, level: Level, cutoff: datetime) -> str:
    """Materialized view feeding one rollup table from rows at or after ``cutoff``."""
    table = rollup_table(source, level.key)
    cut = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    select = select_aggregates(source, level)
    # Insert the WHERE between FROM and GROUP BY.
    select = select.replace(
        f"FROM {source}\n",
        f"FROM {source}\n    WHERE timestamp >= toDateTime64('{cut}', 3, 'UTC')\n",
    )
    return f"""
    CREATE MATERIALIZED VIEW IF NOT EXISTS {table}_mv TO {table} AS
    {select}
    """


async def existing_rollup_tables(client) -> tuple[str, ...]:
    """The rollup tables present in the current database, in ROLLUP_TABLES order."""
    result = await client.query(
        "SELECT name FROM system.tables "
        "WHERE database = currentDatabase() AND name IN {names:Array(String)}",
        parameters={"names": list(ROLLUP_TABLES)},
    )
    present = {row[0] for row in result.result_rows}
    return tuple(t for t in ROLLUP_TABLES if t in present)

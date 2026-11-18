"""Rollup schema as data: 5m / 1h / 1d aggregate tables and their views.

Every raw table (ping_checks, agent_metrics, bandwidth_metrics) is rolled up
into three AggregatingMergeTree tables. The DDL and the SELECT used by both
the materialized views and the backfill are generated from the specs below.

This module must not import ``services.clickhouse_client`` at module level:
that module imports ``ROLLUP_TABLES`` from here.
"""
from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


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


def column_names(source: str) -> tuple[str, ...]:
    """Rollup table columns in table order (also the SELECT column order)."""
    spec = SOURCES[source]
    return ("tenant_id", "bucket", *spec.keys, *(a.name for a in spec.aggregates))


def select_aggregates(source: str, level: Level, where: str | None = None) -> str:
    """SELECT ... GROUP BY body shared by the view and the backfill.

    ``where`` (a bare condition, no keyword) goes between FROM and GROUP BY.
    """
    spec = SOURCES[source]
    keys = ", ".join(spec.keys)
    cols = [
        "tenant_id",
        f"toStartOfInterval(timestamp, INTERVAL {level.seconds} SECOND, 'UTC') AS bucket",
        *spec.keys,
        *(f"{a.expr} AS {a.name}" for a in spec.aggregates),
    ]
    body = ",\n        ".join(cols)
    where_sql = f"\n    WHERE {where}" if where else ""
    return f"""
    SELECT
        {body}
    FROM {source}{where_sql}
    GROUP BY tenant_id, {keys}, bucket
    """


def _utc(dt: datetime) -> datetime:
    """An aware datetime in UTC; a naive one is taken as UTC already."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cut(cutoff: datetime) -> str:
    """The cutoff as a ``YYYY-MM-DD HH:MM:SS.mmm`` UTC string (view and backfill)."""
    return _utc(cutoff).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def cutoff_expr(cutoff: datetime) -> str:
    """SQL literal for the cutoff, compared against the raw ``timestamp``."""
    return f"toDateTime64('{_cut(cutoff)}', 3, 'UTC')"


def view_ddl(source: str, level: Level, cutoff: datetime) -> str:
    """Materialized view feeding one rollup table from rows at or after ``cutoff``."""
    table = rollup_table(source, level.key)
    select = select_aggregates(source, level, where=f"timestamp >= {cutoff_expr(cutoff)}")
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


async def _present(client, names: list[str]) -> set[str]:
    """Which of ``names`` exist as tables or views in the current database."""
    result = await client.query(
        "SELECT name FROM system.tables "
        "WHERE database = currentDatabase() AND name IN {names:Array(String)}",
        parameters={"names": names},
    )
    return {row[0] for row in result.result_rows}


# ── Creation, backfill and the marker table ──────────────────────────────────

MARKER_TABLE = "nodeglow_rollup_state"
# Rows from before the cutoff reach the raw table late (probe batches); the
# backfill waits this long after the cutoff so they are in it.
BACKFILL_DELAY = timedelta(minutes=10)
# Backfills read a whole raw table: far longer than the default 30 s.
BACKFILL_TIMEOUT_SECONDS = 3600
READY_CACHE_SECONDS = 60

_MARKER_DDL = f"""
    CREATE TABLE IF NOT EXISTS {MARKER_TABLE}
    (
        source     String,
        cutoff     DateTime64(3, 'UTC'),
        backfilled UInt8,
        updated_at DateTime64(3, 'UTC')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (source)
"""


@dataclass(frozen=True)
class RollupState:
    source: str
    cutoff: datetime | None  # None: not created
    backfilled: bool


_ready_cache: dict[str, tuple[float, bool]] = {}
_last_updated: datetime | None = None


def _next_updated_at() -> datetime:
    """Wall-clock version for the marker, strictly increasing within a process
    (ReplacingMergeTree keeps the row with the highest version)."""
    global _last_updated
    now = datetime.now(timezone.utc)
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)
    if _last_updated is not None and now <= _last_updated:
        now = _last_updated + timedelta(milliseconds=1)
    _last_updated = now
    return now


def _objects(source: str) -> list[str]:
    tables = [rollup_table(source, lv.key) for lv in LEVELS]
    return tables + [f"{t}_mv" for t in tables]


async def state(c=None) -> dict[str, RollupState]:
    """The marker rows per source. A source without a row is absent."""
    from services import clickhouse_client as ch
    from services.tenancy import system_scope

    with system_scope("rollup state (installation)"):
        c = c or await ch.get_client()
        if MARKER_TABLE not in await _present(c, [MARKER_TABLE]):
            return {}
        result = await c.query(f"SELECT source, cutoff, backfilled FROM {MARKER_TABLE} FINAL")
        return {
            str(src): RollupState(str(src), _utc(cut), bool(done))
            for src, cut, done in result.result_rows
        }


async def ready(source: str, c=None) -> bool:
    """Created and backfilled: readers may use the rollup tables. Any error
    reads as "not ready" (raw fallback); the answer is cached for 60 s."""
    hit = _ready_cache.get(source)
    now = time.monotonic()
    if hit is not None and now - hit[0] < READY_CACHE_SECONDS:
        return hit[1]
    try:
        rs = (await state(c)).get(source)
    except Exception:
        log.warning("rollup state unreadable, reading raw tables", exc_info=True)
        return False
    ok = rs is not None and rs.cutoff is not None and rs.backfilled
    _ready_cache[source] = (now, ok)
    return ok


async def _write_marker(c, source: str, cutoff: datetime, backfilled: bool) -> None:
    await c.insert(
        MARKER_TABLE,
        [[source, _utc(cutoff), 1 if backfilled else 0, _next_updated_at()]],
        column_names=["source", "cutoff", "backfilled", "updated_at"],
    )
    _ready_cache.pop(source, None)


def _next_midnight(now: datetime) -> datetime:
    """The next UTC midnight after ``now``: a bucket boundary of every level."""
    day = _utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(days=1)


async def _backfill(c, source: str, cutoff: datetime) -> None:
    cols = ", ".join(column_names(source))
    for lv in LEVELS:
        table = rollup_table(source, lv.key)
        # Rows before the cutoff can only stem from an interrupted earlier
        # backfill (the view never writes them): remove them, or they would
        # be counted twice.
        await c.command(
            f"ALTER TABLE {table} DELETE WHERE bucket < toDateTime('{_cut(cutoff)[:19]}', 'UTC') "
            "SETTINGS mutations_sync = 2"
        )
        select = select_aggregates(source, lv, where=f"timestamp < {cutoff_expr(cutoff)}")
        await c.command(f"INSERT INTO {table} ({cols}) {select}")


async def maintain(c=None, now: datetime | None = None) -> dict[str, str]:
    """Create the rollup tables and views once the tenant rebuild is done, and
    backfill the history once. Raises on any DDL or backfill failure.

    Per source: ``deferred`` (rebuild not done), ``created`` (tables and views
    created or repaired this run), ``waiting`` (cutoff + 10 min not reached),
    ``backfilled`` (this run), ``ready`` (an earlier run).
    """
    from services import clickhouse_client as ch
    from services import clickhouse_rebuild
    from services.tenancy import system_scope

    now = _utc(now) if now is not None else datetime.now(timezone.utc)
    own = c is None
    with system_scope("rollup maintenance (installation)"):
        if own:
            await ch.get_client()  # schemas first
            c = await ch.connect(ch.CLICKHOUSE_URL, send_receive_timeout=BACKFILL_TIMEOUT_SECONDS)
        try:
            return await _maintain(c, now, ch, clickhouse_rebuild)
        finally:
            if own:
                closed = c.close()
                if inspect.isawaitable(closed):
                    await closed


async def _maintain(c, now: datetime, ch, clickhouse_rebuild) -> dict[str, str]:
    rebuilt = await clickhouse_rebuild.status(c)
    await c.command(_MARKER_DDL)
    marks = await state(c)
    out: dict[str, str] = {}
    pending: dict[str, datetime] = {}  # created, awaiting the backfill
    touched = False

    for source in SOURCES:
        if rebuilt.get(source) not in ("rebuilt", "grace"):
            out[source] = "deferred"
            continue
        mark = marks.get(source)
        if mark is not None and mark.backfilled:
            out[source] = "ready"
            continue
        names = _objects(source)
        present = await _present(c, names)
        missing = [n for n in names if n not in present]
        if mark is None:
            cutoff = _next_midnight(now)
            await _write_marker(c, source, cutoff, False)
        else:
            cutoff = mark.cutoff
            if any(n.endswith("_mv") for n in missing) and now >= cutoff:
                # Views missing after the cutoff: rows since then would be in
                # neither the views nor the backfill. Start over from the next
                # midnight; the backfill clears what is below it first.
                cutoff = _next_midnight(now)
                await _write_marker(c, source, cutoff, False)
        for lv in LEVELS:
            await c.command(table_ddl(source, lv))
        for lv in LEVELS:
            await c.command(view_ddl(source, lv, cutoff))
        touched = True
        if mark is None or missing:
            out[source] = "created"
        else:
            pending[source] = cutoff

    if touched and ch.CLICKHOUSE_TENANT_URL:
        # Grants and row policies for the new tables; without them the
        # restricted tenant user fails with ACCESS_DENIED. Repeated every run
        # until the backfill is done, so a failure here is retried.
        await ch.provision_tenant_user(c, ch.CLICKHOUSE_TENANT_URL)

    for source, cutoff in pending.items():
        if now < cutoff + BACKFILL_DELAY:
            out[source] = "waiting"
            continue
        await _backfill(c, source, cutoff)
        await _write_marker(c, source, cutoff, True)
        out[source] = "backfilled"
    return out

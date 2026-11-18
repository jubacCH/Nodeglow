"""Rollup schema as data: 5m / 1h / 1d aggregate tables and their views.

Every raw table (ping_checks, agent_metrics, bandwidth_metrics) is rolled up
into three AggregatingMergeTree tables. The DDL and the SELECT used by both
the materialized views and the backfill are generated from the specs below.

Known gap (one-time, at rollout): rows with ``timestamp`` before a source's
cutoff that reach the raw table later than cutoff + 10 minutes (the backfill
delay) are in neither the views (they only see rows at or after the cutoff)
nor the backfill (it has already run), so they are missing from the rollups.

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
BACKFILL_SETTINGS = "SETTINGS optimize_aggregation_in_order = 1"
READY_CACHE_SECONDS = 60
READY_FAILURE_CACHE_SECONDS = 30

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


_ready_cache: dict[str, tuple[float, bool]] = {}  # source -> (expires at, answer)
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
    reads as "not ready" (raw fallback). Answers are cached for 60 s, errors
    for 30 s (a down server must not be retried on every reader call)."""
    hit = _ready_cache.get(source)
    now = time.monotonic()
    if hit is not None and now < hit[0]:
        return hit[1]
    try:
        rs = (await state(c)).get(source)
    except Exception:
        log.warning("rollup state unreadable, reading raw tables", exc_info=True)
        _ready_cache[source] = (now + READY_FAILURE_CACHE_SECONDS, False)
        return False
    ok = rs is not None and rs.cutoff is not None and rs.backfilled
    _ready_cache[source] = (now + READY_CACHE_SECONDS, ok)
    return ok


async def _write_marker(c, source: str, cutoff: datetime, backfilled: bool) -> None:
    await c.insert(
        MARKER_TABLE,
        [[source, _utc(cutoff), 1 if backfilled else 0, _next_updated_at()]],
        column_names=["source", "cutoff", "backfilled", "updated_at"],
    )
    _ready_cache.pop(source, None)


_deferred: dict[str, str] = {}  # source -> reason, for the system status
_deferred_logged: set[tuple[str, str]] = set()


def _note_deferred(source: str, rebuild_state: str) -> None:
    _deferred[source] = f"deferred: rebuild {rebuild_state}"
    if (source, rebuild_state) not in _deferred_logged:
        _deferred_logged.add((source, rebuild_state))
        log.info("Rollups of %s deferred: tenant rebuild is %s", source, rebuild_state)


def deferred_reasons() -> dict[str, str]:
    """Sources the last maintenance run deferred, with the reason."""
    return dict(_deferred)


def _next_midnight(now: datetime) -> datetime:
    """The next UTC midnight after ``now``: a bucket boundary of every level."""
    day = _utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(days=1)


async def _drop_views(c, source: str) -> None:
    for lv in LEVELS:
        await c.command(f"DROP VIEW IF EXISTS {rollup_table(source, lv.key)}_mv")


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
        # The raw sort keys start with the GROUP BY columns (tenant, entity
        # keys, timestamp), so aggregating in sort order keeps memory flat:
        # without it 30 days of 500 hosts exceeded the memory limit.
        await c.command(f"INSERT INTO {table} ({cols}) {select} {BACKFILL_SETTINGS}")


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


async def _setup_source(c, source: str, cutoff: datetime, now: datetime, started: float,
                        views_missing: bool) -> tuple[datetime, bool]:
    """Tables and views of one source, all three views on one cutoff.

    Returns ``(cutoff, recreated)``. Views are only ever created together: when
    any is missing the rest is dropped and all three are created with the
    marker's cutoff (``IF NOT EXISTS`` would keep an old cutoff on a survivor
    and the levels would disagree). After creating them the clock is checked:
    if the cutoff has already passed, rows between it and the creation are in
    neither view nor backfill, so the cutoff moves to the next midnight and the
    views are created again. ``now`` plus the time elapsed since the run began
    is the clock, so a view is provably created before its cutoff.
    """
    recreated = False
    for _ in range(3):
        for lv in LEVELS:
            await c.command(table_ddl(source, lv))
        if views_missing:
            await _drop_views(c, source)
            for lv in LEVELS:
                await c.command(view_ddl(source, lv, cutoff))
            recreated = True
            current = now + timedelta(seconds=time.monotonic() - started)
            if current >= cutoff:
                cutoff = _next_midnight(current)
                # Views first, marker second: a crash in between must not leave
                # views of the old cutoff next to a marker of the new one.
                await _drop_views(c, source)
                await _write_marker(c, source, cutoff, False)
                continue  # views exist now, but with the stale cutoff
        return cutoff, recreated
    raise RuntimeError(f"rollup views of {source} could not be created before their cutoff")


async def _maintain(c, now: datetime, ch, clickhouse_rebuild) -> dict[str, str]:
    started = time.monotonic()
    rebuilt = await clickhouse_rebuild.status(c)
    await c.command(_MARKER_DDL)
    marks = await state(c)
    out: dict[str, str] = {}
    pending: dict[str, datetime] = {}  # created, awaiting the backfill
    touched = False
    broken: list[str] = []

    for source in SOURCES:
        state_now = rebuilt.get(source)
        if state_now not in ("rebuilt", "grace"):
            out[source] = "deferred"
            _note_deferred(source, str(state_now))
            continue
        _deferred.pop(source, None)
        mark = marks.get(source)
        names = _objects(source)
        present = await _present(c, names)
        if mark is not None and mark.backfilled:
            gone = [n for n in names if n not in present]
            if gone:
                # Readers keep using the rollups of a ready source: without
                # its views they silently stop filling.
                broken.append(f"{source}: {', '.join(gone)}")
                out[source] = "broken"
            else:
                out[source] = "ready"
            continue
        missing = [n for n in names if n not in present]
        views_missing = any(n.endswith("_mv") for n in missing)
        if mark is None:
            cutoff = _next_midnight(now)
            await _write_marker(c, source, cutoff, False)
            views_missing = True
        else:
            cutoff = mark.cutoff
            if views_missing and now >= cutoff:
                # Views missing after the cutoff: rows since then would be in
                # neither the views nor the backfill. Start over from the next
                # midnight; the backfill clears what is below it first.
                cutoff = _next_midnight(now)
                # Drop the old-cutoff views before the marker moves, so a
                # crash in between cannot leave them double counting.
                await _drop_views(c, source)
                await _write_marker(c, source, cutoff, False)
        cutoff, recreated = await _setup_source(c, source, cutoff, now, started, views_missing)
        touched = True
        if mark is None or missing or recreated:
            out[source] = "created"
        else:
            pending[source] = cutoff

    if touched and ch.CLICKHOUSE_TENANT_URL and ch.PROVISION_TENANT_USER:
        # Grants and row policies for the new tables; without them the
        # restricted tenant user fails with ACCESS_DENIED. Repeated every run
        # until the backfill is done, so a failure here is retried. Skipped
        # for operators who manage ClickHouse access themselves.
        await ch.provision_tenant_user(c, ch.CLICKHOUSE_TENANT_URL)

    for source, cutoff in pending.items():
        if now < cutoff + BACKFILL_DELAY:
            out[source] = "waiting"
            continue
        await _backfill(c, source, cutoff)
        await _write_marker(c, source, cutoff, True)
        out[source] = "backfilled"
    if broken:
        raise RuntimeError(
            "rollup tables or views of a backfilled source are missing, its rollups no "
            "longer fill: " + "; ".join(broken))
    if any(v == "created" for v in out.values()) and ch.CLICKHOUSE_TENANT_URL \
            and not ch.PROVISION_TENANT_USER:
        log.warning(
            "Rollup tables were created but tenant user provisioning is off "
            "(NODEGLOW_CLICKHOUSE_PROVISION_TENANT_USER=0): grant SELECT on these tables to the "
            "tenant user and add the same row policies as on the raw tables, or tenant reads "
            "fail with ACCESS_DENIED: %s", ", ".join(ROLLUP_TABLES))
    return out


# ── Readers: raw or rollup resolution by range ───────────────────────────────

RAW = "raw"
# Raw retention per source in hours: beyond it only a rollup has data.
RAW_TTL_HOURS = {"ping_checks": 720, "agent_metrics": 168, "bandwidth_metrics": 168}


def pick_level(hours: int) -> str:
    """The resolution a range of ``hours`` is read at."""
    if hours <= 48:
        return RAW
    if hours <= 720:
        return "5m"
    if hours <= 9600:
        return "1h"
    return "1d"


async def effective_level(source: str, hours: int) -> str:
    """``pick_level``, but raw while the source's rollups are not ready.

    A longer range than raw holds then shows data only as far back as raw
    reaches (``RAW_TTL_HOURS``); that is the price of not being backfilled yet.
    """
    picked = pick_level(hours)
    if picked == RAW or not await ready(source):
        return RAW
    return picked


@dataclass
class SeriesResult:
    level: str
    bucket_seconds: int
    points: list[dict]


def _clean(value):
    """NULL and nan (an empty t-digest) read as None."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    return value


def _since() -> str:
    """Lower bound on ``bucket`` that keeps the partial first bucket."""
    return (
        "bucket >= toStartOfInterval(now() - toIntervalHour({h:UInt32}), "
        "toIntervalSecond({b:UInt32}), 'UTC')"
    )


async def _series(source: str, hours: int, key_cond: str, key_params: dict,
                  raw_select, roll_select) -> tuple[str, int, list[dict]]:
    """Run the raw or rollup query of one source; the selects are callables
    taking the table name and returning the SELECT list."""
    from services import clickhouse_client as ch

    hours = int(hours)
    lvl = await effective_level(source, hours)
    params: dict = {"h": hours, **key_params}
    if lvl == RAW:
        secs = ch.bandwidth_bucket_seconds(hours)
        params["b"] = secs
        where = " AND ".join([
            ch.tenant_clause(params), key_cond, "timestamp >= now() - toIntervalHour({h:UInt32})"])
        sql = (
            f"SELECT {raw_select()} FROM {source} WHERE {where} "
            "GROUP BY bucket ORDER BY bucket ASC"
        )
    else:
        secs = level(lvl).seconds
        params["b"] = secs
        where = " AND ".join([ch.tenant_clause(params), key_cond, _since()])
        sql = (
            f"SELECT {roll_select()} FROM {rollup_table(source, lvl)} WHERE {where} "
            "GROUP BY bucket ORDER BY bucket ASC"
        )
    rows = await ch.query(sql, params)
    if lvl != RAW:
        # Rollup aliases carry an r_ prefix: an alias equal to a column name
        # turns ``sum(lat_n) AS lat_n`` into an aggregate inside an aggregate
        # (ClickHouse ILLEGAL_AGGREGATION).
        rows = [{(k[2:] if k.startswith("r_") else k): v for k, v in r.items()} for r in rows]
    return lvl, secs, rows


_RAW_BUCKET = "toStartOfInterval(timestamp, toIntervalSecond({b:UInt32}), 'UTC') AS bucket"


async def ping_series(host_id: int, hours: int) -> SeriesResult:
    lat_ok = _LAT_OK
    lvl, secs, rows = await _series(
        "ping_checks", hours, "host_id = {k:UInt32}", {"k": int(host_id)},
        lambda: (
            f"{_RAW_BUCKET}, count() AS checks, countIf(success = 1) AS ok, "
            f"countIf({lat_ok}) AS lat_n, "
            f"sumIf(latency_ms, {lat_ok}) / nullIf(countIf({lat_ok}), 0) AS latency_avg, "
            "minIf(latency_ms, success = 1) AS latency_min, "
            "maxIf(latency_ms, success = 1) AS latency_max, "
            "quantileTDigestIf(0.95)(latency_ms, success = 1) AS latency_p95"
        ),
        lambda: (
            "bucket, sum(checks) AS r_checks, sum(ok) AS r_ok, "
            "sum(lat_n) AS r_lat_n, sum(lat_sum) / nullIf(sum(lat_n), 0) AS r_latency_avg, "
            "min(lat_min) AS r_latency_min, max(lat_max) AS r_latency_max, "
            "quantileTDigestMerge(0.95)(lat_p95) AS r_latency_p95"
        ),
    )
    points = []
    for r in rows:
        n = int(r.get("lat_n") or 0)
        p = {"timestamp": r["bucket"], "checks": int(r["checks"]), "ok": int(r["ok"])}
        for k in ("latency_avg", "latency_min", "latency_max", "latency_p95"):
            p[k] = _clean(r.get(k)) if n else None
        points.append(p)
    return SeriesResult(lvl, secs, points)


async def agent_series(agent_id: int, hours: int) -> SeriesResult:
    def raw_sel():
        cols = [_RAW_BUCKET]
        for m in _AGENT_METRICS:
            cols += [
                f"countIf({m} IS NOT NULL) AS {m}_n",
                f"sum({m}) / nullIf(countIf({m} IS NOT NULL), 0) AS {m}_avg",
                f"min({m}) AS {m}_min", f"max({m}) AS {m}_max",
                f"quantileTDigestIf(0.95)(assumeNotNull({m}), isNotNull({m})) AS {m}_p95",
            ]
        return ", ".join(cols)

    def roll_sel():
        cols = ["bucket"]
        for m in _AGENT_METRICS:
            cols += [
                f"sum({m}_n) AS r_{m}_n",
                f"sum({m}_sum) / nullIf(sum({m}_n), 0) AS r_{m}_avg",
                f"min({m}_min) AS r_{m}_min", f"max({m}_max) AS r_{m}_max",
                f"quantileTDigestMerge(0.95)({m}_p95) AS r_{m}_p95",
            ]
        return ", ".join(cols)

    lvl, secs, rows = await _series(
        "agent_metrics", hours, "agent_id = {k:UInt32}", {"k": int(agent_id)}, raw_sel, roll_sel)
    points = []
    for r in rows:
        p = {"timestamp": r["bucket"]}
        for m in _AGENT_METRICS:
            n = int(r.get(f"{m}_n") or 0)
            for suffix in ("avg", "min", "max", "p95"):
                p[f"{m}_{suffix}"] = _clean(r.get(f"{m}_{suffix}")) if n else None
        points.append(p)
    return SeriesResult(lvl, secs, points)


async def bandwidth_series(source_type: str, source_id: str, interface_name: str,
                           hours: int) -> SeriesResult:
    cond = ("source_type = {st:String} AND source_id = {sid:String} "
            "AND interface_name = {iface:String}")
    lvl, secs, rows = await _series(
        "bandwidth_metrics", hours, cond,
        {"st": source_type, "sid": source_id, "iface": interface_name},
        lambda: (
            f"{_RAW_BUCKET}, avg(rx_rate_bps) AS rx_rate_avg, "
            "avg(tx_rate_bps) AS tx_rate_avg, max(rx_rate_bps) AS rx_rate_max, "
            "max(tx_rate_bps) AS tx_rate_max"
        ),
        lambda: (
            "bucket, "
            "sum(rx_rate_sum) / nullIf(sum(rate_n), 0) AS r_rx_rate_avg, "
            "sum(tx_rate_sum) / nullIf(sum(rate_n), 0) AS r_tx_rate_avg, "
            "max(rx_rate_max) AS r_rx_rate_max, max(tx_rate_max) AS r_tx_rate_max"
        ),
    )
    keys = ("rx_rate_avg", "tx_rate_avg", "rx_rate_max", "tx_rate_max")
    points = [{"timestamp": r["bucket"], **{k: _clean(r.get(k)) for k in keys}} for r in rows]
    return SeriesResult(lvl, secs, points)

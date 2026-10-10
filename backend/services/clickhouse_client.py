"""Async ClickHouse client — syslog and time-series storage backend."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("nodeglow.clickhouse")


# ── Schema migrations ────────────────────────────────────────────────────────
# Run on first connect. Existing deployments did NOT re-run clickhouse/init.sql,
# so the Phase 2 tables (ping_checks, agent_metrics, bandwidth_metrics) need to
# be created from the application side. CREATE TABLE IF NOT EXISTS is idempotent.
# ALTER TABLE ... ADD COLUMN IF NOT EXISTS handles the Phase 3 denormalized
# columns for installs that already created the original Phase 2 tables.
_PHASE2_SCHEMAS = [
    """
    CREATE TABLE IF NOT EXISTS ping_checks
    (
        timestamp   DateTime64(3, 'UTC') NOT NULL,
        host_id     UInt32 NOT NULL,
        host_name   LowCardinality(String) DEFAULT '',
        success     UInt8  NOT NULL,
        latency_ms  Nullable(Float32),
        probe_id    UInt32 DEFAULT 0
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMMDD(timestamp)
    ORDER BY (host_id, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 30 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_metrics
    (
        timestamp     DateTime64(3, 'UTC') NOT NULL,
        agent_id      UInt32 NOT NULL,
        agent_name    LowCardinality(String) DEFAULT '',
        cpu_pct       Nullable(Float32),
        mem_pct       Nullable(Float32),
        mem_used_mb   Nullable(Float32),
        mem_total_mb  Nullable(Float32),
        disk_pct      Nullable(Float32),
        load_1        Nullable(Float32),
        load_5        Nullable(Float32),
        load_15       Nullable(Float32),
        uptime_s      Nullable(UInt64),
        rx_bytes      Nullable(Float64),
        tx_bytes      Nullable(Float64),
        data_json     String DEFAULT ''
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMMDD(timestamp)
    ORDER BY (agent_id, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 7 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS bandwidth_metrics
    (
        timestamp       DateTime64(3, 'UTC') NOT NULL,
        source_type     LowCardinality(String) NOT NULL,
        source_id       String NOT NULL,
        source_name     LowCardinality(String) DEFAULT '',
        interface_name  LowCardinality(String) NOT NULL,
        rx_bytes        UInt64 DEFAULT 0,
        tx_bytes        UInt64 DEFAULT 0,
        rx_rate_bps     UInt64 DEFAULT 0,
        tx_rate_bps     UInt64 DEFAULT 0
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMMDD(timestamp)
    ORDER BY (source_type, source_id, interface_name, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 7 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
]

# Idempotent column additions for installs that created the original Phase 2
# tables before host_name/agent_name/source_name were introduced.
_PHASE3_ALTERS = [
    "ALTER TABLE ping_checks ADD COLUMN IF NOT EXISTS host_name LowCardinality(String) DEFAULT ''",
    "ALTER TABLE agent_metrics ADD COLUMN IF NOT EXISTS agent_name LowCardinality(String) DEFAULT ''",
    "ALTER TABLE bandwidth_metrics ADD COLUMN IF NOT EXISTS source_name LowCardinality(String) DEFAULT ''",
    # Which probe produced a result; 0 is the core. Added rather than made part
    # of ORDER BY, because prepending a sort key needs a full table rewrite and
    # every existing row is legitimately probe 0.
    "ALTER TABLE ping_checks ADD COLUMN IF NOT EXISTS probe_id UInt32 DEFAULT 0",
    # syslog_aggregated and its materialized view were never read by anything,
    # but the view ran a GROUP BY over every syslog insert block and the target
    # table kept 90 days of rows. The view goes first, so no insert can hit a
    # dropped target. Both are metadata operations, not mutations.
    "DROP VIEW IF EXISTS syslog_aggregated_mv",
    "DROP TABLE IF EXISTS syslog_aggregated",
    # syslog_messages has row-level TTL rules (TTL ... WHERE severity = ...) on
    # monthly partitions. With ttl_only_drop_parts = 1 a part is only removed
    # once EVERY row in it has expired, i.e. effectively when the 90-day
    # error rows of the whole month expire — debug/info rows configured for
    # 1-3 days lived for months. With 0, TTL merges delete the expired rows.
    # Cost: background TTL merges rewrite parts (at most every
    # merge_with_ttl_timeout, default 4 h per partition); the first ones after
    # this change rewrite most of the current and previous month once. The
    # ALTER itself is a metadata change and returns immediately.
    # The time-series tables keep ttl_only_drop_parts = 1: their TTL is a
    # single whole-row expression on daily partitions, where dropping whole
    # parts is exact to the day and free.
    "ALTER TABLE syslog_messages MODIFY SETTING ttl_only_drop_parts = 0",
]

_schemas_applied = False

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://nodeglow:nodeglow@clickhouse:8123/nodeglow"
)

# HTTP compression for inserts and results: "lz4" (default), "zstd", or
# "none" to switch it off.
_compression_env = os.environ.get("NODEGLOW_CLICKHOUSE_COMPRESSION", "lz4").strip().lower()
CLICKHOUSE_COMPRESSION: str | bool = False if _compression_env in ("", "none", "0", "false", "off") else _compression_env

_client = None
_client_lock = asyncio.Lock()


async def get_client():
    """Return (or lazily create) the async ClickHouse client."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        import clickhouse_connect
        for attempt in range(1, 6):
            try:
                _client = await clickhouse_connect.get_async_client(
                    dsn=CLICKHOUSE_URL,
                    # lz4 ships with clickhouse-connect; cheap on CPU, and it
                    # shrinks both syslog insert batches and large results.
                    compress=CLICKHOUSE_COMPRESSION,
                    query_limit=0,
                    connect_timeout=10,
                    send_receive_timeout=30,
                )
                log.info("ClickHouse connected: %s", CLICKHOUSE_URL)
                await _ensure_schemas(_client)
                return _client
            except Exception as e:
                log.warning("ClickHouse connect attempt %d failed: %s", attempt, e)
                try:
                    from services.metrics import CLICKHOUSE_CONNECTION_ERRORS
                    CLICKHOUSE_CONNECTION_ERRORS.inc()
                except Exception:
                    pass
                if attempt < 5:
                    await asyncio.sleep(attempt * 2)
        raise RuntimeError("Could not connect to ClickHouse after 5 attempts")


async def _ensure_schemas(client) -> None:
    """Apply Phase 2 CREATE TABLE + Phase 3 ALTERs once per process."""
    global _schemas_applied
    if _schemas_applied:
        return
    for ddl in _PHASE2_SCHEMAS:
        try:
            await client.command(ddl)
        except Exception as exc:
            log.warning("ClickHouse schema migration failed: %s", exc)
    for ddl in _PHASE3_ALTERS:
        try:
            await client.command(ddl)
        except Exception as exc:
            log.warning("ClickHouse schema alter failed: %s", exc)
    _schemas_applied = True


async def insert_batch(rows: list[dict]) -> None:
    """Bulk-insert syslog message dicts into ClickHouse."""
    if not rows:
        return
    client = await get_client()
    columns = [
        "timestamp", "received_at", "source_ip", "hostname", "host_id",
        "facility", "severity", "app_name", "message",
        "template_hash", "tags", "noise_score",
        "extracted_fields", "geo_country", "geo_city",
    ]
    data = []
    for row in rows:
        # Convert extracted_fields dict to list of tuples for Map column
        fields = row.get("extracted_fields") or {}
        if isinstance(fields, dict):
            fields_map = fields
        else:
            fields_map = {}
        data.append([
            row.get("timestamp") or datetime.utcnow(),
            row.get("received_at") or datetime.utcnow(),
            row.get("source_ip") or "",
            row.get("hostname") or "",
            row.get("host_id"),
            row.get("facility"),
            row.get("severity") if row.get("severity") is not None else 6,
            row.get("app_name") or "",
            row.get("message") or "",
            row.get("template_hash") or "",
            row.get("tags") or "",
            row.get("noise_score") if row.get("noise_score") is not None else 50,
            fields_map,
            row.get("geo_country") or "",
            row.get("geo_city") or "",
        ])
    await _do_insert(client, "syslog_messages", data, columns)


def _record_insert_metric(table: str, status: str, row_count: int) -> None:
    """Best-effort: increment ClickHouse insert metrics. Never raises."""
    try:
        from services.metrics import CLICKHOUSE_INSERTS, CLICKHOUSE_INSERT_ROWS
        CLICKHOUSE_INSERTS.labels(table=table, status=status).inc()
        if status == "success":
            CLICKHOUSE_INSERT_ROWS.labels(table=table).inc(row_count)
    except Exception:
        pass


async def _do_insert(client, table: str, data: list, columns: list[str]) -> None:
    """Common insert path: wrapped in an OTel span and metric counters."""
    from services.tracing import tracer
    with tracer.start_as_current_span("clickhouse.insert") as span:
        span.set_attribute("clickhouse.table", table)
        span.set_attribute("clickhouse.row_count", len(data))
        try:
            await client.insert(table, data, column_names=columns)
        except Exception:
            _record_insert_metric(table, "failure", len(data))
            span.set_attribute("error", True)
            raise
        _record_insert_metric(table, "success", len(data))


# ── Time-series writes ────────────────────────────────────────────────────────
# Post-cutover: ClickHouse is the authoritative store for ping_checks,
# agent_metrics, and bandwidth_metrics. Failures here are real failures and
# must propagate so callers can decide whether to retry.

async def insert_ping_checks(rows: list[dict]) -> None:
    """Bulk-insert ping check results.

    Each row: {timestamp, host_id, host_name, success (bool), latency_ms (float|None),
    probe_id (int, 0 = checked by the core)}
    """
    if not rows:
        return
    columns = ["timestamp", "host_id", "host_name", "success", "latency_ms", "probe_id"]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            int(r["host_id"]),
            str(r.get("host_name") or ""),
            1 if r.get("success") else 0,
            float(r["latency_ms"]) if r.get("latency_ms") is not None else None,
            int(r.get("probe_id") or 0),
        ]
        for r in rows
    ]
    client = await get_client()
    await _do_insert(client, "ping_checks", data, columns)


async def insert_agent_metrics(rows: list[dict]) -> None:
    """Bulk-insert agent metric snapshots.

    Each row: {timestamp, agent_id, agent_name, cpu_pct, mem_pct, mem_used_mb,
              mem_total_mb, disk_pct, load_1, load_5, load_15, uptime_s,
              rx_bytes, tx_bytes, data_json}
    Missing keys become NULL.
    """
    if not rows:
        return
    columns = [
        "timestamp", "agent_id", "agent_name",
        "cpu_pct", "mem_pct", "mem_used_mb", "mem_total_mb", "disk_pct",
        "load_1", "load_5", "load_15",
        "uptime_s", "rx_bytes", "tx_bytes", "data_json",
    ]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            int(r["agent_id"]),
            str(r.get("agent_name") or ""),
            r.get("cpu_pct"),
            r.get("mem_pct"),
            r.get("mem_used_mb"),
            r.get("mem_total_mb"),
            r.get("disk_pct"),
            r.get("load_1"),
            r.get("load_5"),
            r.get("load_15"),
            int(r["uptime_s"]) if r.get("uptime_s") is not None else None,
            r.get("rx_bytes"),
            r.get("tx_bytes"),
            r.get("data_json") or "",
        ]
        for r in rows
    ]
    client = await get_client()
    await _do_insert(client, "agent_metrics", data, columns)


async def insert_bandwidth_metrics(rows: list[dict]) -> None:
    """Bulk-insert bandwidth samples.

    Each row: {timestamp, source_type, source_id, source_name, interface_name,
              rx_bytes, tx_bytes, rx_rate_bps, tx_rate_bps}
    """
    if not rows:
        return
    columns = [
        "timestamp", "source_type", "source_id", "source_name",
        "interface_name",
        "rx_bytes", "tx_bytes", "rx_rate_bps", "tx_rate_bps",
    ]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            str(r.get("source_type") or ""),
            str(r.get("source_id") or ""),
            str(r.get("source_name") or ""),
            str(r.get("interface_name") or ""),
            int(r.get("rx_bytes") or 0),
            int(r.get("tx_bytes") or 0),
            int(r.get("rx_rate_bps") or 0),
            int(r.get("tx_rate_bps") or 0),
        ]
        for r in rows
    ]
    client = await get_client()
    await _do_insert(client, "bandwidth_metrics", data, columns)


# ── Time-series read API ─────────────────────────────────────────────────────
# All callers should use these helpers instead of writing raw SQL — keeps the
# query patterns consistent and the typing stable.

# How far back the "latest state" lookups read ping_checks. The table keeps 30
# days; reading all of it to find the newest row per host made the per-minute
# ping job and every correlation cycle scan ~30 daily partitions. A day covers
# any sane check interval many times over (even hourly checks give 24 rows).
PING_LOOKBACK_HOURS = int(os.environ.get("NODEGLOW_PING_LOOKBACK_HOURS", "24"))


async def _latest_ping_rows(
    host_ids: list[int] | None, lookback_hours: int | None,
) -> list[dict]:
    clauses: list[str] = []
    params: dict = {}
    if lookback_hours:
        clauses.append("timestamp >= now() - toIntervalHour({lb:UInt32})")
        params["lb"] = int(lookback_hours)
    if host_ids:
        clauses.append("host_id IN ({hids:Array(UInt32)})")
        params["hids"] = list(host_ids)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT
            host_id,
            max(timestamp)                AS _ts,
            argMax(success,    timestamp) AS success,
            argMax(latency_ms, timestamp) AS latency_ms,
            argMax(host_name,  timestamp) AS host_name
        FROM ping_checks
        {where}
        GROUP BY host_id
    """
    return await query(sql, params)


async def get_latest_ping_per_host(
    host_ids: list[int] | None = None,
    lookback_hours: int | None = PING_LOOKBACK_HOURS,
) -> dict[int, dict]:
    """Return {host_id: {timestamp, success, latency_ms, host_name}} for the
    most recent ping per host. If host_ids is None, returns all hosts that
    were checked within the lookback window.

    The main query only reads the last ``lookback_hours``. Hosts that were
    asked for by id but have no row in that window (maintenance, disabled, a
    probe that went quiet) are looked up once more without the bound; that
    second query is restricted to those few ids, so the (host_id, timestamp)
    sort key keeps it to their granules instead of the whole table.
    ``lookback_hours=None`` disables the bound entirely.

    Note: the SQL aliases use `_ts` instead of `timestamp` to avoid a
    ClickHouse 24.8 parser quirk where aliasing a column to its own name
    while other aggregates reference the column triggers ILLEGAL_AGGREGATION
    ("nested aggregate function"). We rename in Python after the query.
    """
    rows = await _latest_ping_rows(host_ids, lookback_hours)
    out: dict[int, dict] = {}
    for r in rows:
        r["timestamp"] = r.pop("_ts")
        out[int(r["host_id"])] = r
    if host_ids and lookback_hours:
        missing = [int(h) for h in host_ids if int(h) not in out]
        if missing:
            for r in await _latest_ping_rows(missing, None):
                r["timestamp"] = r.pop("_ts")
                out[int(r["host_id"])] = r
    return out


async def get_ping_uptime(
    host_ids: list[int] | None = None,
    hours: int = 24,
) -> dict[int, dict]:
    """Return {host_id: {total, ok, uptime_pct, avg_latency}} aggregated over
    the last N hours."""
    where_clauses = ["timestamp >= now() - toIntervalHour({h:UInt32})"]
    params: dict = {"h": int(hours)}
    if host_ids:
        where_clauses.append("host_id IN ({hids:Array(UInt32)})")
        params["hids"] = list(host_ids)
    where = " AND ".join(where_clauses)
    sql = f"""
        SELECT
            host_id,
            count() AS total,
            sumIf(1, success = 1) AS ok,
            avgIf(latency_ms, success = 1) AS avg_latency
        FROM ping_checks
        WHERE {where}
        GROUP BY host_id
    """
    rows = await query(sql, params)
    out: dict[int, dict] = {}
    for r in rows:
        total = int(r["total"]) or 0
        ok = int(r["ok"] or 0)
        out[int(r["host_id"])] = {
            "total": total,
            "ok": ok,
            "uptime_pct": round(ok / total * 100, 2) if total else None,
            "avg_latency": float(r["avg_latency"]) if r["avg_latency"] is not None else None,
        }
    return out


async def get_ping_history(host_id: int, hours: int = 24) -> list[dict]:
    """Return raw ping records for one host over the last N hours, oldest first."""
    sql = """
        SELECT timestamp, success, latency_ms
        FROM ping_checks
        WHERE host_id = {hid:UInt32}
          AND timestamp >= now() - toIntervalHour({h:UInt32})
        ORDER BY timestamp ASC
    """
    return await query(sql, {"hid": int(host_id), "h": int(hours)})


async def get_ping_status_transitions(
    host_id: int,
    hours: int = 24,
) -> list[dict]:
    """Return list of ping status transition events for one host.

    Uses a window function to compare each check against its predecessor and
    emits one row per flip (success changes from 0↔1). First row is always
    emitted as the initial state. Latest rows first.

    Row shape: {timestamp, success, latency_ms, prev_success}

    Time-series-cheap: one CH query, no per-row round trips.
    """
    sql = """
        SELECT timestamp, success, latency_ms, prev_success
        FROM (
            SELECT
                timestamp,
                success,
                latency_ms,
                lagInFrame(success) OVER (ORDER BY timestamp ASC) AS prev_success,
                row_number() OVER (ORDER BY timestamp ASC) AS rn
            FROM ping_checks
            WHERE host_id = {hid:UInt32}
              AND timestamp >= now() - toIntervalHour({h:UInt32})
        )
        WHERE rn = 1 OR prev_success != success
        ORDER BY timestamp DESC
    """
    return await query(sql, {"hid": int(host_id), "h": int(hours)})


# ── Syslog time windows ──────────────────────────────────────────────────────
# syslog_messages has two clocks:
#   timestamp   — what the device wrote into the message. RFC 3164 carries no
#                 year and no zone, so this is the device's local wall time
#                 read as UTC (hours off for any device not on UTC), and a
#                 device without NTP can be anywhere.
#   received_at — when Nodeglow ingested the message; monotonic and trustworthy.
# Windows like "the last hour" must therefore filter on received_at. But the
# table is PARTITION BY toYYYYMM(timestamp) ORDER BY (severity, source_ip,
# timestamp): received_at prunes nothing, so every such query read every
# partition. Each received_at window now also carries a lower bound on
# timestamp, loosened by a skew margin so a device whose clock runs behind is
# still found. Rows with a device clock further behind than the margin fall
# out of these windows — they are also the rows the timestamp-based TTL
# deletes first, so they were never reliably queryable anyway.
SYSLOG_CLOCK_SKEW_HOURS = int(os.environ.get("NODEGLOW_SYSLOG_CLOCK_SKEW_HOURS", "24"))


def received_since_clause(
    since: datetime, param: str = "since", skew_hours: int | None = None,
) -> tuple[str, dict]:
    """``received_at >= since`` plus a partition/primary-key pruning bound.

    Returns (sql_fragment, params). The fragment keeps received_at semantics
    exactly; the timestamp predicate only lets ClickHouse skip partitions and
    granules that cannot contain a match within the skew margin.
    """
    skew = SYSLOG_CLOCK_SKEW_HOURS if skew_hours is None else skew_hours
    prune = f"{param}_prune"
    sql = (
        f"received_at >= {{{param}:DateTime64(3)}} "
        f"AND timestamp >= {{{prune}:DateTime64(3)}}"
    )
    return sql, {param: since, prune: since - timedelta(hours=skew)}


async def count_syslog_by_host(
    host_ids: list[int],
    since: datetime,
    max_severity: int | None = None,
) -> dict[int, int]:
    """{host_id: message count} since ``since`` (device clock), one query.

    Replaces one count() round trip per host in the correlation engine.
    """
    if not host_ids:
        return {}
    clauses = [
        "host_id IN ({hids:Array(Int32)})",
        "timestamp >= {t:DateTime64(3)}",
    ]
    params: dict = {"hids": [int(h) for h in host_ids], "t": since}
    if max_severity is not None:
        clauses.append("severity <= {max_sev:Int8}")
        params["max_sev"] = int(max_severity)
    sql = f"""
        SELECT host_id, count() AS cnt
        FROM syslog_messages
        WHERE {' AND '.join(clauses)}
        GROUP BY host_id
    """
    rows = await query(sql, params)
    return {int(r["host_id"]): int(r["cnt"]) for r in rows if r.get("host_id") is not None}


async def count_syslog_received_by_source(
    since: datetime, source_ips: list[str] | None = None,
) -> dict[str, int]:
    """{source_ip: messages ingested since ``since``} in one query.

    Uses received_at — these counts are compared against the hourly volume
    baselines, which are learned on ingest time as well. ``source_ips=None``
    counts every source.
    """
    where, params = received_since_clause(since)
    if source_ips is not None:
        if not source_ips:
            return {}
        where += " AND source_ip IN ({ips:Array(String)})"
        params["ips"] = list(source_ips)
    rows = await query(
        f"SELECT source_ip, count() AS cnt FROM syslog_messages "
        f"WHERE {where} GROUP BY source_ip",
        params,
    )
    return {r["source_ip"]: int(r["cnt"]) for r in rows}


async def count_syslog_received_by_host(
    since: datetime, host_ids: list[int],
) -> dict[int, int]:
    """{host_id: messages ingested since ``since``} in one query."""
    if not host_ids:
        return {}
    where, params = received_since_clause(since)
    params["hids"] = [int(h) for h in host_ids]
    rows = await query(
        f"SELECT host_id, count() AS cnt FROM syslog_messages "
        f"WHERE {where} AND host_id IN ({{hids:Array(Int32)}}) "
        f"GROUP BY host_id",
        params,
    )
    return {int(r["host_id"]): int(r["cnt"]) for r in rows if r.get("host_id") is not None}


async def get_syslog_events_for_host(
    host_id: int | None,
    host_name: str,
    host_source_ip: str,
    since: datetime,
    max_severity: int = 4,
    limit: int = 200,
) -> list[dict]:
    """Return syslog rows for one host over a time window, filtered by
    severity (≤ max_severity; 4=warning, 3=error, etc.). Newest first.

    At least one of host_id / host_name / host_source_ip should be provided
    — without any matcher the query returns an empty list instead of the
    entire log firehose.
    """
    if not host_id and not host_name and not host_source_ip:
        return []

    matchers: list[str] = []
    params: dict = {"since": since, "max_sev": int(max_severity), "lim": int(limit)}
    if host_id is not None:
        matchers.append("host_id = {hid:Int32}")
        params["hid"] = int(host_id)
    if host_name:
        matchers.append("positionCaseInsensitive(hostname, {hname:String}) > 0")
        params["hname"] = host_name
    if host_source_ip:
        matchers.append("source_ip = {hsip:String}")
        params["hsip"] = host_source_ip

    since_sql, since_params = received_since_clause(since)
    params.update(since_params)
    where = (
        f"{since_sql} "
        f"AND ({' OR '.join(matchers)}) "
        "AND severity <= {max_sev:Int8}"
    )
    sql = f"""
        SELECT received_at, severity, facility, hostname, app_name, message
        FROM syslog_messages
        WHERE {where}
        ORDER BY received_at DESC
        LIMIT {{lim:UInt32}}
    """
    return await query(sql, params)


async def get_offline_hosts_since(
    host_ids: list[int],
    min_failures: int,
    lookback_hours: int = PING_LOOKBACK_HOURS,
) -> list[int]:
    """Return host_ids whose last `min_failures` checks all failed.

    Used by the correlation engine to detect host-down events without N+1
    queries against Postgres.

    Only the newest ``min_failures`` checks matter, so only the last
    ``lookback_hours`` are read — however long an outage has lasted, its most
    recent failed checks are always inside that window as long as the host is
    still being checked. A host with fewer than ``min_failures`` checks in the
    window is not being checked at all; whether that is a problem is the
    self-check's call (probe staleness), not an outage verdict based on
    day-old rows.

    The newest rows are picked by sorting (timestamp, success) tuples rather
    than relying on an ORDER BY subquery feeding groupArray: aggregation is
    free to read its input in any order, so the old form could compare the
    wrong checks under parallel execution.
    """
    if not host_ids or min_failures <= 0:
        return []
    sql = """
        SELECT host_id
        FROM (
            SELECT
                host_id,
                arraySlice(
                    arrayReverseSort(groupArray((timestamp, success))),
                    1, {n:UInt32}
                ) AS recent
            FROM ping_checks
            WHERE host_id IN ({hids:Array(UInt32)})
              AND timestamp >= now() - toIntervalHour({lb:UInt32})
            GROUP BY host_id
        )
        WHERE length(recent) >= {n:UInt32}
          AND arraySum(x -> x.2, recent) = 0
    """
    rows = await query(sql, {
        "hids": list(host_ids), "n": int(min_failures), "lb": int(lookback_hours),
    })
    return [int(r["host_id"]) for r in rows]


async def get_latest_agent_metrics(agent_ids: list[int] | None = None) -> dict[int, dict]:
    """Return {agent_id: latest_snapshot_dict}.

    The newest timestamp per agent is resolved first, in a subquery that only
    reads (agent_id, timestamp). The outer query then reads the remaining
    columns, data_json above all, for just those rows: the MinMax and primary
    key indexes cut it to a handful of granules. A plain argMax over the table
    read every data_json ever stored; on production that was ~60 ms against
    ~15 ms, and the dashboard asks twice per request.

    The outer argMax/GROUP BY stays so that two rows sharing an agent's newest
    timestamp still collapse into one.
    """
    agent_filter = ""
    params: dict = {}
    if agent_ids:
        agent_filter = "agent_id IN ({aids:Array(UInt32)})"
        params["aids"] = list(agent_ids)
    inner_where = f"WHERE {agent_filter}" if agent_filter else ""
    outer_where = (
        "WHERE (agent_id, timestamp) IN ("
        f"SELECT agent_id, max(timestamp) FROM agent_metrics {inner_where} GROUP BY agent_id"
        ")"
    )
    if agent_filter:
        outer_where += f" AND {agent_filter}"
    # _ts alias avoids the same ClickHouse parser quirk documented in
    # get_latest_ping_per_host above.
    sql = f"""
        SELECT
            agent_id,
            max(timestamp)                  AS _ts,
            argMax(agent_name,   timestamp) AS agent_name,
            argMax(cpu_pct,      timestamp) AS cpu_pct,
            argMax(mem_pct,      timestamp) AS mem_pct,
            argMax(mem_used_mb,  timestamp) AS mem_used_mb,
            argMax(mem_total_mb, timestamp) AS mem_total_mb,
            argMax(disk_pct,     timestamp) AS disk_pct,
            argMax(load_1,       timestamp) AS load_1,
            argMax(load_5,       timestamp) AS load_5,
            argMax(load_15,      timestamp) AS load_15,
            argMax(uptime_s,     timestamp) AS uptime_s,
            argMax(rx_bytes,     timestamp) AS rx_bytes,
            argMax(tx_bytes,     timestamp) AS tx_bytes,
            argMax(data_json,    timestamp) AS data_json
        FROM agent_metrics
        {outer_where}
        GROUP BY agent_id
    """
    rows = await query(sql, params)
    out: dict[int, dict] = {}
    for r in rows:
        r["timestamp"] = r.pop("_ts")
        out[int(r["agent_id"])] = r
    return out


async def get_agent_history(
    agent_id: int,
    limit: int = 60,
    hours: int | None = None,
) -> list[dict]:
    """Return recent agent_metrics rows for one agent, newest first."""
    where = "agent_id = {aid:UInt32}"
    params: dict = {"aid": int(agent_id), "lim": int(limit)}
    if hours:
        where += " AND timestamp >= now() - toIntervalHour({h:UInt32})"
        params["h"] = int(hours)
    sql = f"""
        SELECT
            timestamp, cpu_pct, mem_pct, mem_used_mb, mem_total_mb,
            disk_pct, load_1, load_5, load_15, uptime_s,
            rx_bytes, tx_bytes, data_json
        FROM agent_metrics
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {{lim:UInt32}}
    """
    return await query(sql, params)


async def get_latest_bandwidth_per_iface(
    source_type: str | None = None,
    source_id: str | None = None,
    since_hours: int = 1,
) -> list[dict]:
    """Return latest sample per (source_type, source_id, interface_name).

    Used by the bandwidth dashboard for "top talkers" and per-interface gauges.
    """
    where_clauses = ["timestamp >= now() - toIntervalHour({h:UInt32})"]
    params: dict = {"h": int(since_hours)}
    if source_type:
        where_clauses.append("source_type = {st:String}")
        params["st"] = source_type
    if source_id:
        where_clauses.append("source_id = {sid:String}")
        params["sid"] = source_id
    where = " AND ".join(where_clauses)
    # _ts alias avoids the ClickHouse parser quirk — see get_latest_ping_per_host.
    sql = f"""
        SELECT
            source_type,
            source_id,
            interface_name,
            max(timestamp)                 AS _ts,
            argMax(source_name, timestamp) AS source_name,
            argMax(rx_bytes,    timestamp) AS rx_bytes,
            argMax(tx_bytes,    timestamp) AS tx_bytes,
            argMax(rx_rate_bps, timestamp) AS rx_rate_bps,
            argMax(tx_rate_bps, timestamp) AS tx_rate_bps
        FROM bandwidth_metrics
        WHERE {where}
        GROUP BY source_type, source_id, interface_name
    """
    rows = await query(sql, params)
    for r in rows:
        r["timestamp"] = r.pop("_ts")
    return rows


async def get_bandwidth_history_ch(
    source_type: str | None = None,
    source_id: str | None = None,
    interface_name: str | None = None,
    hours: int = 24,
    limit: int = 2000,
) -> list[dict]:
    """Time-series bandwidth data for charting."""
    where_clauses = ["timestamp >= now() - toIntervalHour({h:UInt32})"]
    params: dict = {"h": int(hours), "lim": int(limit)}
    if source_type:
        where_clauses.append("source_type = {st:String}")
        params["st"] = source_type
    if source_id:
        where_clauses.append("source_id = {sid:String}")
        params["sid"] = source_id
    if interface_name:
        where_clauses.append("interface_name = {iface:String}")
        params["iface"] = interface_name
    where = " AND ".join(where_clauses)
    sql = f"""
        SELECT timestamp, source_type, source_id, source_name, interface_name,
               rx_bytes, tx_bytes, rx_rate_bps, tx_rate_bps
        FROM bandwidth_metrics
        WHERE {where}
        ORDER BY timestamp ASC
        LIMIT {{lim:UInt32}}
    """
    return await query(sql, params)


async def get_previous_bandwidth_sample(
    source_type: str,
    source_id: str,
    interface_name: str,
) -> dict | None:
    """Return the most recent prior sample for rate calculation, or None."""
    sql = """
        SELECT timestamp, rx_bytes, tx_bytes
        FROM bandwidth_metrics
        WHERE source_type = {st:String}
          AND source_id   = {sid:String}
          AND interface_name = {iface:String}
        ORDER BY timestamp DESC
        LIMIT 1
    """
    rows = await query(sql, {
        "st": source_type, "sid": source_id, "iface": interface_name,
    })
    return rows[0] if rows else None


async def query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a SELECT and return list of row dicts."""
    from services.tracing import tracer
    with tracer.start_as_current_span("clickhouse.query") as span:
        client = await get_client()
        try:
            result = await client.query(sql, parameters=params or {})
        except Exception:
            try:
                from services.metrics import CLICKHOUSE_QUERY_ERRORS
                CLICKHOUSE_QUERY_ERRORS.inc()
            except Exception:
                pass
            span.set_attribute("error", True)
            raise
        rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
        span.set_attribute("clickhouse.row_count", len(rows))
        return rows


async def query_scalar(sql: str, params: dict | None = None) -> Any:
    """Execute a query returning a single scalar value."""
    rows = await query(sql, params)
    if rows:
        return next(iter(rows[0].values()))
    return None


async def query_chunked(
    sql: str,
    list_param: str,
    values: list,
    other_params: dict | None = None,
    chunk_size: int = 1000,
) -> list[dict]:
    # ClickHouse rejects HTTP form fields > http_max_field_value_size (128 KiB default).
    # An IN-list with thousands of items easily exceeds that, so split into chunks.
    if not values:
        return []
    base = dict(other_params or {})
    out: list[dict] = []
    for i in range(0, len(values), chunk_size):
        chunk_params = {**base, list_param: values[i : i + chunk_size]}
        out.extend(await query(sql, chunk_params))
    return out


def _where_clauses(
    since: datetime,
    sev: int | None = None,
    fac: int | None = None,
    host: str = "",
    app: str = "",
    q: str = "",
    host_id: int | None = None,
    host_source_ip: str = "",
    host_name: str = "",
    sev_list: list[int] | None = None,
    country: str = "",
) -> tuple[str, dict]:
    """Build WHERE clause string + params dict for syslog queries.

    Supports special search syntax in `q`:
    - field:key=value  — search extracted_fields Map column
    - country:XX       — filter by geo_country
    """
    since_sql, params = received_since_clause(since)
    clauses = [since_sql]

    if sev is not None:
        clauses.append("severity = {sev:Int8}")
        params["sev"] = sev
    if fac is not None:
        clauses.append("facility = {fac:Int8}")
        params["fac"] = fac
    if host:
        clauses.append(
            "(positionCaseInsensitive(hostname, {host:String}) > 0 "
            "OR positionCaseInsensitive(source_ip, {host:String}) > 0)"
        )
        params["host"] = host
    if app:
        clauses.append("positionCaseInsensitive(app_name, {app:String}) > 0")
        params["app"] = app
    if country:
        clauses.append("geo_country = {country:String}")
        params["country"] = country
    if q:
        tokens = q.split()
        fi = 0
        for i, token in enumerate(tokens[:5]):
            # field:key=value syntax for searching extracted_fields
            if token.startswith("field:") and "=" in token:
                parts = token[6:].split("=", 1)
                fk = f"fk{fi}"
                fv = f"fv{fi}"
                clauses.append(f"extracted_fields[{{{fk}:String}}] = {{{fv}:String}}")
                params[fk] = parts[0]
                params[fv] = parts[1]
                fi += 1
            elif token.startswith("country:"):
                clauses.append("geo_country = {geo_c:String}")
                params["geo_c"] = token[8:]
            else:
                # Substring, case-insensitive — deliberately. The tokenbf_v1
                # index on `message` cannot serve this: it holds whole,
                # case-sensitive tokens of the raw text, so it only helps
                # hasToken()/LIKE on complete tokens. Adding such a predicate
                # would silently change what a search for "fail" finds
                # ("failed" would no longer match). An index the substring
                # search can use would be an ngrambf_v1 on lower(message),
                # which needs a MATERIALIZE INDEX mutation over the whole
                # table — out of scope here. The timestamp bound above is
                # what keeps these scans small.
                key = f"q{i}"
                clauses.append(f"positionCaseInsensitive(message, {{{key}:String}}) > 0")
                params[key] = token
    if host_id is not None:
        sub = ["host_id = {hid:Int32}"]
        params["hid"] = host_id
        if host_source_ip:
            sub.append("source_ip = {hsip:String}")
            params["hsip"] = host_source_ip
        if host_name:
            sub.append("positionCaseInsensitive(hostname, {hname:String}) > 0")
            params["hname"] = host_name
        clauses.append(f"({' OR '.join(sub)})")
    if sev_list:
        safe_sevs = [int(s) for s in sev_list]
        in_vals = ",".join(str(s) for s in safe_sevs)
        clauses.append(f"severity IN ({in_vals})")

    return " AND ".join(clauses), params



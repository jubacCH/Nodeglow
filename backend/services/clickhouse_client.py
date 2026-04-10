"""Async ClickHouse client — syslog and time-series storage backend."""
import asyncio
import logging
import os
from datetime import datetime
from typing import Any

log = logging.getLogger("nodeglow.clickhouse")


# ── Schema migrations ────────────────────────────────────────────────────────
# Run on first connect. Existing deployments did NOT re-run clickhouse/init.sql,
# so the Phase 2 tables (ping_checks, agent_metrics, bandwidth_metrics) need to
# be created from the application side. CREATE TABLE IF NOT EXISTS is idempotent.
_PHASE2_SCHEMAS = [
    """
    CREATE TABLE IF NOT EXISTS ping_checks
    (
        timestamp   DateTime64(3, 'UTC') NOT NULL,
        host_id     UInt32 NOT NULL,
        success     UInt8  NOT NULL,
        latency_ms  Nullable(Float32)
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

_schemas_applied = False

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://nodeglow:nodeglow@clickhouse:8123/nodeglow"
)

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
                    compress=False,
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
    """Apply Phase 2 CREATE TABLE IF NOT EXISTS once per process."""
    global _schemas_applied
    if _schemas_applied:
        return
    for ddl in _PHASE2_SCHEMAS:
        try:
            await client.command(ddl)
        except Exception as exc:
            log.warning("ClickHouse schema migration failed: %s", exc)
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
    try:
        await client.insert("syslog_messages", data, column_names=columns)
        _record_insert_metric("syslog_messages", "success", len(data))
    except Exception:
        _record_insert_metric("syslog_messages", "failure", len(data))
        raise


def _record_insert_metric(table: str, status: str, row_count: int) -> None:
    """Best-effort: increment ClickHouse insert metrics. Never raises."""
    try:
        from services.metrics import CLICKHOUSE_INSERTS, CLICKHOUSE_INSERT_ROWS
        CLICKHOUSE_INSERTS.labels(table=table, status=status).inc()
        if status == "success":
            CLICKHOUSE_INSERT_ROWS.labels(table=table).inc(row_count)
    except Exception:
        pass


# ── Phase 2 typed inserts ────────────────────────────────────────────────────
# Each helper is fire-and-forget safe: callers should NOT let a ClickHouse
# failure break their primary Postgres write. Wrap calls in try/except, the
# helpers themselves only suppress connection-level errors so unexpected bugs
# still surface during development.

async def insert_ping_checks(rows: list[dict]) -> None:
    """Bulk-insert ping check results.

    Each row: {timestamp, host_id, success (bool), latency_ms (float|None)}
    """
    if not rows:
        return
    columns = ["timestamp", "host_id", "success", "latency_ms"]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            int(r["host_id"]),
            1 if r.get("success") else 0,
            float(r["latency_ms"]) if r.get("latency_ms") is not None else None,
        ]
        for r in rows
    ]
    client = await get_client()
    try:
        await client.insert("ping_checks", data, column_names=columns)
        _record_insert_metric("ping_checks", "success", len(data))
    except Exception:
        _record_insert_metric("ping_checks", "failure", len(data))
        raise


async def insert_agent_metrics(rows: list[dict]) -> None:
    """Bulk-insert agent metric snapshots.

    Each row: {timestamp, agent_id, cpu_pct, mem_pct, mem_used_mb, mem_total_mb,
              disk_pct, load_1, load_5, load_15, uptime_s, rx_bytes, tx_bytes,
              data_json}
    Missing keys become NULL.
    """
    if not rows:
        return
    columns = [
        "timestamp", "agent_id",
        "cpu_pct", "mem_pct", "mem_used_mb", "mem_total_mb", "disk_pct",
        "load_1", "load_5", "load_15",
        "uptime_s", "rx_bytes", "tx_bytes", "data_json",
    ]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            int(r["agent_id"]),
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
    try:
        await client.insert("agent_metrics", data, column_names=columns)
        _record_insert_metric("agent_metrics", "success", len(data))
    except Exception:
        _record_insert_metric("agent_metrics", "failure", len(data))
        raise


async def insert_bandwidth_metrics(rows: list[dict]) -> None:
    """Bulk-insert bandwidth samples.

    Each row: {timestamp, source_type, source_id, interface_name,
              rx_bytes, tx_bytes, rx_rate_bps, tx_rate_bps}
    """
    if not rows:
        return
    columns = [
        "timestamp", "source_type", "source_id", "interface_name",
        "rx_bytes", "tx_bytes", "rx_rate_bps", "tx_rate_bps",
    ]
    data = [
        [
            r.get("timestamp") or datetime.utcnow(),
            str(r.get("source_type") or ""),
            str(r.get("source_id") or ""),
            str(r.get("interface_name") or ""),
            int(r.get("rx_bytes") or 0),
            int(r.get("tx_bytes") or 0),
            int(r.get("rx_rate_bps") or 0),
            int(r.get("tx_rate_bps") or 0),
        ]
        for r in rows
    ]
    client = await get_client()
    try:
        await client.insert("bandwidth_metrics", data, column_names=columns)
        _record_insert_metric("bandwidth_metrics", "success", len(data))
    except Exception:
        _record_insert_metric("bandwidth_metrics", "failure", len(data))
        raise


async def query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a SELECT and return list of row dicts."""
    client = await get_client()
    result = await client.query(sql, parameters=params or {})
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


async def query_scalar(sql: str, params: dict | None = None) -> Any:
    """Execute a query returning a single scalar value."""
    rows = await query(sql, params)
    if rows:
        return next(iter(rows[0].values()))
    return None


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
    clauses = ["received_at >= {since:DateTime64(3)}"]
    params: dict = {"since": since}

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


async def query_aggregated(
    since: datetime,
    source_ip: str = "",
    template_hash: str = "",
    severity: int | None = None,
) -> list[dict]:
    """Query the aggregated syslog table for trend/dashboard data."""
    clauses = ["bucket >= {since:DateTime}"]
    params: dict = {"since": since}
    if source_ip:
        clauses.append("source_ip = {sip:String}")
        params["sip"] = source_ip
    if template_hash:
        clauses.append("template_hash = {th:String}")
        params["th"] = template_hash
    if severity is not None:
        clauses.append("severity = {sev:Int8}")
        params["sev"] = severity
    where = " AND ".join(clauses)
    return await query(
        f"SELECT * FROM syslog_aggregated WHERE {where} ORDER BY bucket DESC LIMIT 1000",
        params,
    )

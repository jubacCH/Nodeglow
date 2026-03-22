"""Async ClickHouse client — syslog storage backend."""
import asyncio
import logging
import os
from datetime import datetime
from typing import Any

log = logging.getLogger("nodeglow.clickhouse")

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
                return _client
            except Exception as e:
                log.warning("ClickHouse connect attempt %d failed: %s", attempt, e)
                if attempt < 5:
                    await asyncio.sleep(attempt * 2)
        raise RuntimeError("Could not connect to ClickHouse after 5 attempts")


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
    await client.insert("syslog_messages", data, column_names=columns)


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
    clauses = ["timestamp >= {since:DateTime64(3)}"]
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

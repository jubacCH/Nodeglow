"""Async ClickHouse client — syslog and time-series storage backend.

Tenancy (multi-tenancy phase 6, see docs/specs/2026-10-10-multi-tenancy-design.md
"Data isolation — ClickHouse"). Every table carries ``tenant_id``; there is no
ORM to hang a filter on, so the isolation lives here:

* **Reads fail closed.** :func:`query` (and with it ``query_scalar`` /
  ``query_chunked`` and every helper) refuses to run without a tenant context.
  In a tenant scope it

  1. refuses SQL that reads a tenant table without the tenant predicate
     (:data:`TENANT_FILTER`, ``tenant_id = {nodeglow_tenant:UInt32}``) — one
     per ``FROM``/``JOIN`` of a tenant table — and binds the parameter itself;
  2. sends ``additional_table_filters`` with ``tenant_id = <tenant>`` for every
     tenant table, so even a predicate in the wrong place cannot widen the read
     (``NODEGLOW_CLICKHOUSE_TABLE_FILTERS=0`` switches this net off);
  3. when ``CLICKHOUSE_TENANT_URL`` is configured, runs the query as that
     restricted user with ``SQL_nodeglow_tenant = <tenant>``: ClickHouse row
     policies (:func:`provision_tenant_user`) then filter on the server, and a
     query without the setting errors.

  In ``system_scope()`` (installation jobs: retention sweeps, the step-B
  rebuild, health) reads are installation-wide, on the main connection.
* **Inserts** take ``tenant_id`` from each row — never from the ambient
  context; ingestion batches mix tenants. A row without one is refused.
"""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

from services import tenancy as _tenancy

log = logging.getLogger("nodeglow.clickhouse")


# ── Tenancy ──────────────────────────────────────────────────────────────────

# Every table that holds tenant data. A new table must be added here (and get a
# tenant_id column), or the read guard below does not know about it.
TENANT_TABLES = ("syslog_messages", "ping_checks", "agent_metrics", "bandwidth_metrics")

TENANT_PARAM = "nodeglow_tenant"
# The predicate every read of a tenant table carries, in raw SQL as well as in
# the helpers below. query() binds the parameter from the context.
TENANT_FILTER = "tenant_id = {nodeglow_tenant:UInt32}"
_TENANT_PLACEHOLDER = "{" + TENANT_PARAM + ":"

# Custom setting read by the row policies (needs the SQL_ prefix in the
# server's custom_settings_prefixes — clickhouse/config.xml sets it).
ROW_POLICY_SETTING = "SQL_nodeglow_tenant"
ROW_POLICY_NAME = "nodeglow_tenant_isolation"
UNRESTRICTED_POLICY_NAME = "nodeglow_unrestricted"

_TENANT_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:`?\w+`?\.)?`?(" + "|".join(TENANT_TABLES) + r")\b`?",
    re.IGNORECASE,
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no")


TABLE_FILTERS_ENABLED = _env_flag("NODEGLOW_CLICKHOUSE_TABLE_FILTERS", True)


def _require_context() -> "_tenancy.TenantContext":
    ctx = _tenancy.current()
    if ctx is None or (not ctx.system and ctx.tenant_id is None):
        raise _tenancy.TenantContextError(
            "ClickHouse access without a tenant context "
            "(use tenant_scope() or, for installation jobs, system_scope())"
        )
    return ctx


def tenant_clause(params: dict) -> str:
    """The tenant predicate for a helper's WHERE clause, its parameter added to
    ``params``.

    Tenant scope: :data:`TENANT_FILTER`. System scope: ``1`` — the read is
    installation-wide, which a system scope chose explicitly. No context:
    raises :class:`~services.tenancy.TenantContextError`.
    """
    ctx = _require_context()
    if ctx.system:
        return "1"
    params[TENANT_PARAM] = int(ctx.tenant_id)
    return TENANT_FILTER


def tenant_tables_read(sql: str) -> list[str]:
    """Tenant tables named after FROM/JOIN in ``sql`` (one entry per read)."""
    return [m.lower() for m in _TENANT_TABLE_REF.findall(sql)]


def _check_tenant_predicates(sql: str) -> None:
    reads = tenant_tables_read(sql)
    found = sql.count(_TENANT_PLACEHOLDER)
    if reads and found < len(reads):
        raise _tenancy.TenantContextError(
            f"ClickHouse read of {', '.join(sorted(set(reads)))} without the tenant "
            f"predicate ({len(reads)} table read(s), {found} predicate(s)); "
            f"add {TENANT_FILTER}"
        )


def _table_filters(tenant_id: int) -> str:
    """``additional_table_filters`` as a ClickHouse map literal."""
    tid = int(tenant_id)
    pairs = ", ".join(f"'{t}': 'tenant_id = {tid}'" for t in TENANT_TABLES)
    return "{" + pairs + "}"


def tenant_query_settings(tenant_id: int, *, row_policy: bool) -> dict:
    """Per-query settings that confine a read to ``tenant_id`` on the server."""
    settings: dict = {}
    if TABLE_FILTERS_ENABLED:
        settings["additional_table_filters"] = _table_filters(tenant_id)
    if row_policy:
        settings[ROW_POLICY_SETTING] = str(int(tenant_id))
    return settings


def _row_tenant(row: dict) -> int:
    tid = row.get("tenant_id")
    if tid is None:
        raise _tenancy.TenantContextError(
            "ClickHouse insert of a row without tenant_id (stamp it from the "
            "producing host / agent / source)"
        )
    tid = int(tid)
    if tid <= 0:
        raise _tenancy.TenantContextError(f"ClickHouse insert with invalid tenant_id {tid}")
    return tid


# ── Schema migrations ────────────────────────────────────────────────────────
# Run on first connect. Existing deployments did NOT re-run clickhouse/init.sql,
# so the Phase 2 tables (ping_checks, agent_metrics, bandwidth_metrics) need to
# be created from the application side. CREATE TABLE IF NOT EXISTS is idempotent.
# ALTER TABLE ... ADD COLUMN IF NOT EXISTS handles the Phase 3 denormalized
# columns for installs that already created the original Phase 2 tables.
#
# Fresh tables lead their sort key with tenant_id (the spec's "step B" shape).
# tenant_id has no DEFAULT there: every insert path stamps it, and a writer that
# forgets would land in tenant 0, which no tenant can read.
_PHASE2_SCHEMAS = [
    # syslog_messages is also in init.sql, which only runs when the server's
    # data directory is first created and, depending on the image's
    # entrypoint, in its default database. The app creates it as well so an
    # install never lacks it in the database it actually uses.
    """
    CREATE TABLE IF NOT EXISTS syslog_messages
    (
        tenant_id       UInt32,
        site_id         UInt32 DEFAULT 0,
        timestamp       DateTime64(3, 'UTC') NOT NULL,
        received_at     DateTime64(3, 'UTC') NOT NULL DEFAULT now64(),
        source_ip       LowCardinality(String) NOT NULL,
        hostname        LowCardinality(String) DEFAULT '',
        host_id         Nullable(Int32),
        facility        Nullable(Int8),
        severity        Int8 DEFAULT 6,
        app_name        LowCardinality(String) DEFAULT '',
        message         String NOT NULL,
        template_hash   LowCardinality(String) DEFAULT '',
        tags            String DEFAULT '',
        noise_score     Int8 DEFAULT 50,
        extracted_fields Map(String, String) DEFAULT map(),
        geo_country     LowCardinality(String) DEFAULT '',
        geo_city        LowCardinality(String) DEFAULT '',
        INDEX idx_message_bloom message TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4,
        INDEX idx_hostname_bloom hostname TYPE bloom_filter(0.01) GRANULARITY 4
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (tenant_id, severity, source_ip, timestamp)
    TTL
        toDateTime(timestamp) + INTERVAL 1  DAY  WHERE severity = 7  AND noise_score >= 80,
        toDateTime(timestamp) + INTERVAL 2  DAY  WHERE severity = 7,
        toDateTime(timestamp) + INTERVAL 2  DAY  WHERE severity = 6  AND noise_score >= 80,
        toDateTime(timestamp) + INTERVAL 3  DAY  WHERE severity = 6,
        toDateTime(timestamp) + INTERVAL 7  DAY  WHERE severity = 5,
        toDateTime(timestamp) + INTERVAL 30 DAY  WHERE severity = 4,
        toDateTime(timestamp) + INTERVAL 90 DAY  WHERE severity IN (0, 1, 2, 3),
        toDateTime(timestamp) + INTERVAL 180 DAY WHERE noise_score <= 10
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 0
    """,
    """
    CREATE TABLE IF NOT EXISTS ping_checks
    (
        tenant_id   UInt32,
        timestamp   DateTime64(3, 'UTC') NOT NULL,
        host_id     UInt32 NOT NULL,
        host_name   LowCardinality(String) DEFAULT '',
        success     UInt8  NOT NULL,
        latency_ms  Nullable(Float32),
        probe_id    UInt32 DEFAULT 0
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMMDD(timestamp)
    ORDER BY (tenant_id, host_id, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 30 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_metrics
    (
        tenant_id     UInt32,
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
    ORDER BY (tenant_id, agent_id, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 7 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS bandwidth_metrics
    (
        tenant_id       UInt32,
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
    ORDER BY (tenant_id, source_type, source_id, interface_name, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 7 DAY
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
    """,
]


def tenant_column_alters(table: str, *, site: bool | None = None) -> list[str]:
    """Step A for one table: add ``tenant_id`` (and ``site_id`` on syslog).

    A metadata-only change: parts written before it do not contain the column,
    so ClickHouse computes it from the DEFAULT on read — every existing row
    reads as tenant 1, which is the backfill. That is also why the DEFAULT must
    stay 1 on a table that still has such parts (changing a column's default
    changes what old parts read as); the step-B rebuild writes the value
    physically and drops the default. On a table that already has the column
    (fresh install, rebuilt) the statements are no-ops.
    """
    stmts = [f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UInt32 DEFAULT 1"]
    if site if site is not None else table == "syslog_messages":
        # source_ip alone is ambiguous inside a tenant with several sites; 0 =
        # not attributed to a site (the direct listener; phase 10 fills it).
        stmts.append(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS site_id UInt32 DEFAULT 0")
    return stmts


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
    # Multi-tenancy step A (phase 6), see _STEP_A_ALTERS.
]

# Multi-tenancy step A (phase 6): tenant_id on every table, existing rows read
# as tenant 1. The sort-key prefix is the step-B rebuild
# (services/clickhouse_rebuild.py), which runs in the background. Unlike the
# optional columns above these are required: every insert names tenant_id.
_STEP_A_ALTERS = [stmt for table in TENANT_TABLES for stmt in tenant_column_alters(table)]
_PHASE3_ALTERS += _STEP_A_ALTERS

_schemas_applied = False

CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://nodeglow:nodeglow@clickhouse:8123/nodeglow"
)
# Optional DSN of a restricted user for tenant-scoped reads. When set, the app
# creates the user and the row policies through CLICKHOUSE_URL (unless
# NODEGLOW_CLICKHOUSE_PROVISION_TENANT_USER=0, for operators who manage
# ClickHouse access themselves) and runs every tenant read as that user.
# Unset (the default, and every install before phase 6): one connection and
# the application-level filter only.
CLICKHOUSE_TENANT_URL = os.environ.get("CLICKHOUSE_TENANT_URL", "").strip()
PROVISION_TENANT_USER = _env_flag("NODEGLOW_CLICKHOUSE_PROVISION_TENANT_USER", True)

# HTTP compression for inserts and results: "lz4" (default), "zstd", or
# "none" to switch it off.
_compression_env = os.environ.get("NODEGLOW_CLICKHOUSE_COMPRESSION", "lz4").strip().lower()
CLICKHOUSE_COMPRESSION: str | bool = False if _compression_env in ("", "none", "0", "false", "off") else _compression_env

_client = None
_client_lock = asyncio.Lock()
_tenant_client = None
_tenant_client_lock = asyncio.Lock()
# When the restricted connection failed its check: (monotonic time, reason).
# Tenant reads fail closed until a retry (after _TENANT_RETRY_SECONDS) passes.
_tenant_client_error: tuple[float, str] | None = None
_TENANT_RETRY_SECONDS = 60.0


def _redact_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    host = parts.hostname or ""
    netloc = f"{parts.username}:***@{host}" + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=netloc).geturl()


async def connect(dsn: str, *, send_receive_timeout: int = 30, database: str | None = None):
    """A new async client for ``dsn`` with the application's defaults.

    ``database`` applies when the DSN names none (its path wins otherwise).
    """
    import clickhouse_connect
    kwargs = {"database": database} if database and not dsn_credentials(dsn)[2] else {}
    return await clickhouse_connect.get_async_client(
        dsn=dsn,
        # lz4 ships with clickhouse-connect; cheap on CPU, and it
        # shrinks both syslog insert batches and large results.
        compress=CLICKHOUSE_COMPRESSION,
        query_limit=0,
        connect_timeout=10,
        send_receive_timeout=send_receive_timeout,
        **kwargs,
    )


async def get_client():
    """Return (or lazily create) the main async ClickHouse client.

    This connection owns the schema, performs every insert and serves
    ``system_scope()`` reads. Tenant reads go through :func:`get_tenant_client`.
    """
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        for attempt in range(1, 6):
            try:
                client = await connect(CLICKHOUSE_URL)
                log.info("ClickHouse connected: %s", _redact_dsn(CLICKHOUSE_URL))
                # Published only once the schema is in place: a failed step A
                # fails this attempt, and the next attempt (or the next call)
                # applies it again instead of every insert failing for the
                # rest of the process.
                await _ensure_schemas(client)
                _client = client
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


def allow_custom_setting(client, name: str) -> None:
    """Let clickhouse-connect 0.8 send a custom (``SQL_…``) setting.

    0.8.x checks every setting against ``system.settings`` and raises for
    unknown ones; custom settings are not listed there. Registering it as a
    writable setting makes the client forward it, and the server decides
    (with row policies: it is required). Later versions forward unknown
    settings anyway; for them this only adds a harmless entry.
    """
    inner = getattr(client, "client", client)
    known = getattr(inner, "server_settings", None)
    if not isinstance(known, dict) or name in known:
        return
    try:
        from clickhouse_connect.driver.models import SettingDef
        known[name] = SettingDef(name=name, value="", readonly=0)
    except Exception:  # pragma: no cover - layout of a future client version
        log.debug("Could not register custom ClickHouse setting %s", name, exc_info=True)


class TenantIsolationError(RuntimeError):
    """The restricted tenant user is configured but its row policies are not
    in force; tenant reads are refused rather than run without them."""


def tenant_database() -> str | None:
    """The database of the restricted user: its DSN's, else CLICKHOUSE_URL's."""
    return dsn_credentials(CLICKHOUSE_TENANT_URL)[2] or dsn_credentials(CLICKHOUSE_URL)[2]


async def verify_restricted(client) -> None:
    """The restricted connection must be confined by the row policy on every
    tenant table: a read without ``SQL_nodeglow_tenant`` fails, a read with
    it succeeds. Raises :class:`TenantIsolationError` otherwise."""
    for table in TENANT_TABLES:
        sql = f"SELECT tenant_id FROM {table} LIMIT 0"
        try:
            await client.query(sql)
        except Exception:
            pass
        else:
            raise TenantIsolationError(
                f"{table}: the restricted user reads without {ROW_POLICY_SETTING} "
                "(row policy missing)"
            )
        try:
            await client.query(sql, settings={ROW_POLICY_SETTING: "0"})
        except Exception as exc:
            raise TenantIsolationError(f"{table}: the restricted user cannot read ({exc})") from exc


async def get_tenant_client():
    """The client for tenant-scoped reads: the restricted user of
    ``CLICKHOUSE_TENANT_URL`` (row policies), else the main client.

    The restricted connection is checked once (:func:`verify_restricted`)
    before it is used. If the check fails — provisioning failed or was partial,
    the DSN names the main user, a policy was dropped — tenant reads fail
    closed with :class:`TenantIsolationError` and an error in the log; the
    check is retried every minute.
    """
    global _tenant_client, _tenant_client_error
    if not CLICKHOUSE_TENANT_URL:
        return await get_client()
    if _tenant_client is not None:
        return _tenant_client
    await get_client()  # schemas and the user's provisioning come first
    async with _tenant_client_lock:
        if _tenant_client is not None:
            return _tenant_client
        now = asyncio.get_running_loop().time()
        if _tenant_client_error and now - _tenant_client_error[0] < _TENANT_RETRY_SECONDS:
            raise TenantIsolationError(_tenant_client_error[1])
        client = None
        try:
            if dsn_credentials(CLICKHOUSE_TENANT_URL)[0] == dsn_credentials(CLICKHOUSE_URL)[0]:
                raise TenantIsolationError(
                    "CLICKHOUSE_TENANT_URL names the same user as CLICKHOUSE_URL"
                )
            client = await connect(CLICKHOUSE_TENANT_URL, database=tenant_database())
            allow_custom_setting(client, ROW_POLICY_SETTING)
            await verify_restricted(client)
        except Exception as exc:
            reason = f"ClickHouse tenant isolation not in place, tenant reads refused: {exc}"
            _tenant_client_error = (now, reason)
            log.error(reason)
            if client is not None:
                try:
                    closed = client.close()
                    if asyncio.iscoroutine(closed):
                        await closed
                except Exception:
                    pass
            raise TenantIsolationError(reason) from exc
        _tenant_client, _tenant_client_error = client, None
        log.info("ClickHouse tenant connection: %s", _redact_dsn(CLICKHOUSE_TENANT_URL))
    return _tenant_client


def _quote_ident(name: str) -> str:
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _quote_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def row_policy_statements(user: str, password: str | None, database: str,
                          tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    """DDL that creates the restricted tenant user and the row policies.

    * The user may read exactly the tenant tables (and ``system.settings``,
      which the client library reads on connect) — not the whole database, so
      the step-B work tables are out of its reach.
    * ``nodeglow_tenant_isolation`` (``TO <user>``): rows of the tenant named
      by the ``SQL_nodeglow_tenant`` setting. Without the setting ``getSetting``
      raises, so a query that forgot it errors instead of returning rows.
    * ``nodeglow_unrestricted`` (``TO ALL EXCEPT <user>``): every other user —
      the main application user above all — keeps reading every row, whatever
      the server's ``users_without_row_policies_can_read_rows`` says.

    Policies and grants are bound to table *names*, so they follow the step-B
    ``EXCHANGE TABLES`` onto the rebuilt table.
    """
    u = _quote_ident(user)
    db = _quote_ident(database)
    stmts: list[str] = []
    # DEFAULT DATABASE: the helpers name tables unqualified.
    if password is not None:
        pw = _quote_str(password)
        stmts.append(f"CREATE USER IF NOT EXISTS {u} IDENTIFIED WITH sha256_password BY {pw} "
                     f"DEFAULT DATABASE {db}")
        stmts.append(f"ALTER USER {u} IDENTIFIED WITH sha256_password BY {pw} DEFAULT DATABASE {db}")
    else:
        stmts.append(f"CREATE USER IF NOT EXISTS {u} DEFAULT DATABASE {db}")
        stmts.append(f"ALTER USER {u} DEFAULT DATABASE {db}")
    stmts.append(f"GRANT SELECT ON system.settings TO {u}")
    for table in tables:
        t = _quote_ident(table)
        stmts.append(f"GRANT SELECT ON {db}.{t} TO {u}")
        stmts.append(
            f"CREATE ROW POLICY OR REPLACE {ROW_POLICY_NAME} ON {db}.{t} FOR SELECT "
            f"USING tenant_id = toUInt32(getSetting({_quote_str(ROW_POLICY_SETTING)})) "
            f"AS permissive TO {u}"
        )
        stmts.append(
            f"CREATE ROW POLICY OR REPLACE {UNRESTRICTED_POLICY_NAME} ON {db}.{t} FOR SELECT "
            f"USING 1 AS permissive TO ALL EXCEPT {u}"
        )
    return stmts


def dsn_credentials(dsn: str) -> tuple[str, str | None, str | None]:
    """(user, password, database) from a ClickHouse HTTP DSN."""
    parts = urlsplit(dsn)
    user = unquote(parts.username or "default")
    password = unquote(parts.password) if parts.password is not None else None
    database = parts.path.strip("/") or None
    return user, password, database


def revoke_statements(user: str, database: str,
                      tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    """Take a previously provisioned tenant user's grants away."""
    u, db = _quote_ident(user), _quote_ident(database)
    return [f"REVOKE SELECT ON {db}.{_quote_ident(t)} FROM {u}" for t in tables] + [
        f"REVOKE SELECT ON system.settings FROM {u}"]


async def provision_tenant_user(client, tenant_dsn: str, database: str | None = None) -> None:
    """Create/refresh the restricted user of ``tenant_dsn`` and its policies.

    A user the policies were provisioned for before (the DSN's user was
    renamed) loses its grants first: re-targeting the policies would otherwise
    put it under ``nodeglow_unrestricted``, reading every tenant's rows.
    """
    user, password, dsn_db = dsn_credentials(tenant_dsn)
    database = dsn_db or database or str(await client.command("SELECT currentDatabase()"))
    result = await client.query(
        "SELECT DISTINCT arrayJoin(apply_to_list) AS u FROM system.row_policies "
        "WHERE short_name = {p:String} AND database = {db:String}",
        parameters={"p": ROW_POLICY_NAME, "db": database},
    )
    for (previous,) in result.result_rows:
        if previous != user:
            for stmt in revoke_statements(previous, database):
                try:
                    await client.command(stmt)
                except Exception:
                    log.debug("%s failed", stmt, exc_info=True)
            log.warning("ClickHouse: revoked the tenant-table grants of %s, the previous "
                        "restricted user (now %s)", previous, user)
    for stmt in row_policy_statements(user, password, database):
        await client.command(stmt)
    log.info("ClickHouse row policies provisioned for %s on %s", user, database)


async def _ensure_schemas(client) -> None:
    """Apply Phase 2 CREATE TABLE + Phase 3 ALTERs once per process.

    Raises when a CREATE TABLE or a step-A ALTER failed (every insert names
    tenant_id, so without it every insert would fail): the connect attempt
    fails and is retried, and the schema is applied again. The other ALTERs
    are best effort, as before.
    """
    global _schemas_applied
    if _schemas_applied:
        return
    required_failed: list[str] = []
    for ddl in _PHASE2_SCHEMAS:
        try:
            await client.command(ddl)
        except Exception as exc:
            log.warning("ClickHouse schema migration failed: %s", exc)
            required_failed.append(" ".join(ddl.split())[:60])
    for ddl in _PHASE3_ALTERS:
        try:
            await client.command(ddl)
        except Exception as exc:
            log.warning("ClickHouse schema alter failed: %s", exc)
            if ddl in _STEP_A_ALTERS:
                required_failed.append(ddl)
    if required_failed:
        raise RuntimeError(f"ClickHouse schema incomplete: {'; '.join(required_failed)}")
    # The step-B rebuild stops (TTL) merges on a table for its cutover. Had the
    # process died in between, nothing else would start them again (the
    # blocker lives until the ClickHouse server restarts): the table would
    # eventually refuse inserts with "too many parts", or stop expiring rows.
    for table in TENANT_TABLES:
        for action in ("MERGES", "TTL MERGES"):
            try:
                await client.command(f"SYSTEM START {action} {table}")
            except Exception:
                log.debug("SYSTEM START %s %s failed", action, table, exc_info=True)
    if CLICKHOUSE_TENANT_URL and PROVISION_TENANT_USER:
        try:
            await provision_tenant_user(client, CLICKHOUSE_TENANT_URL, tenant_database())
        except Exception as exc:
            # Not fatal for the main connection; get_tenant_client() verifies
            # the policies and refuses tenant reads while they are missing.
            log.error("ClickHouse row-policy provisioning failed: %s", exc)
    _schemas_applied = True

async def insert_batch(rows: list[dict]) -> None:
    """Bulk-insert syslog message dicts into ClickHouse.

    Every row names its ``tenant_id`` (and optionally ``site_id``); a batch
    with a row that does not is refused as a whole, before anything is sent.
    """
    if not rows:
        return
    columns = [
        "tenant_id", "site_id",
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
            _row_tenant(row),
            int(row.get("site_id") or 0),
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
    client = await get_client()
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

    Each row: {tenant_id (the host's), timestamp, host_id, host_name,
    success (bool), latency_ms (float|None), probe_id (int, 0 = checked by
    the core)}
    """
    if not rows:
        return
    columns = ["tenant_id", "timestamp", "host_id", "host_name", "success", "latency_ms", "probe_id"]
    data = [
        [
            _row_tenant(r),
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

    Each row: {tenant_id (the agent's), timestamp, agent_id, agent_name,
              cpu_pct, mem_pct, mem_used_mb, mem_total_mb, disk_pct, load_1,
              load_5, load_15, uptime_s, rx_bytes, tx_bytes, data_json}
    Missing keys become NULL; a missing tenant_id is refused.
    """
    if not rows:
        return
    columns = [
        "tenant_id", "timestamp", "agent_id", "agent_name",
        "cpu_pct", "mem_pct", "mem_used_mb", "mem_total_mb", "disk_pct",
        "load_1", "load_5", "load_15",
        "uptime_s", "rx_bytes", "tx_bytes", "data_json",
    ]
    data = [
        [
            _row_tenant(r),
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

    Each row: {tenant_id (the source's), timestamp, source_type, source_id,
              source_name, interface_name, rx_bytes, tx_bytes, rx_rate_bps,
              tx_rate_bps}
    """
    if not rows:
        return
    columns = [
        "tenant_id", "timestamp", "source_type", "source_id", "source_name",
        "interface_name",
        "rx_bytes", "tx_bytes", "rx_rate_bps", "tx_rate_bps",
    ]
    data = [
        [
            _row_tenant(r),
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
# query patterns consistent and the typing stable. Every helper starts its
# WHERE clause with the tenant predicate (tenant_clause), so it raises without
# a context and, once the sort key leads with tenant_id, prunes on it.

# How far back the "latest state" lookups read ping_checks. The table keeps 30
# days; reading all of it to find the newest row per host made the per-minute
# ping job and every correlation cycle scan ~30 daily partitions. A day covers
# any sane check interval many times over (even hourly checks give 24 rows).
PING_LOOKBACK_HOURS = int(os.environ.get("NODEGLOW_PING_LOOKBACK_HOURS", "24"))


async def _latest_ping_rows(
    host_ids: list[int] | None, lookback_hours: int | None,
) -> list[dict]:
    params: dict = {}
    clauses: list[str] = [tenant_clause(params)]
    if lookback_hours:
        clauses.append("timestamp >= now() - toIntervalHour({lb:UInt32})")
        params["lb"] = int(lookback_hours)
    if host_ids:
        clauses.append("host_id IN ({hids:Array(UInt32)})")
        params["hids"] = list(host_ids)
    where = f"WHERE {' AND '.join(clauses)}"
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
    params: dict = {"h": int(hours)}
    where_clauses = [tenant_clause(params), "timestamp >= now() - toIntervalHour({h:UInt32})"]
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
    params: dict = {"hid": int(host_id), "h": int(hours)}
    sql = f"""
        SELECT timestamp, success, latency_ms
        FROM ping_checks
        WHERE {tenant_clause(params)}
          AND host_id = {{hid:UInt32}}
          AND timestamp >= now() - toIntervalHour({{h:UInt32}})
        ORDER BY timestamp ASC
    """
    return await query(sql, params)


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
    params: dict = {"hid": int(host_id), "h": int(hours)}
    sql = f"""
        SELECT timestamp, success, latency_ms, prev_success
        FROM (
            SELECT
                timestamp,
                success,
                latency_ms,
                lagInFrame(success) OVER (ORDER BY timestamp ASC) AS prev_success,
                row_number() OVER (ORDER BY timestamp ASC) AS rn
            FROM ping_checks
            WHERE {tenant_clause(params)}
              AND host_id = {{hid:UInt32}}
              AND timestamp >= now() - toIntervalHour({{h:UInt32}})
        )
        WHERE rn = 1 OR prev_success != success
        ORDER BY timestamp DESC
    """
    return await query(sql, params)


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
    granules that cannot contain a match within the skew margin. It carries no
    tenant predicate — the caller's WHERE does (TENANT_FILTER).
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
    params: dict = {"hids": [int(h) for h in host_ids], "t": since}
    clauses = [
        tenant_clause(params),
        "host_id IN ({hids:Array(Int32)})",
        "timestamp >= {t:DateTime64(3)}",
    ]
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
    if source_ips is not None and not source_ips:
        return {}
    since_sql, params = received_since_clause(since)
    where = f"{tenant_clause(params)} AND {since_sql}"
    if source_ips is not None:
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
    since_sql, params = received_since_clause(since)
    where = f"{tenant_clause(params)} AND {since_sql}"
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
        f"{tenant_clause(params)} AND {since_sql} "
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
    params: dict = {
        "hids": list(host_ids), "n": int(min_failures), "lb": int(lookback_hours),
    }
    sql = f"""
        SELECT host_id
        FROM (
            SELECT
                host_id,
                arraySlice(
                    arrayReverseSort(groupArray((timestamp, success))),
                    1, {{n:UInt32}}
                ) AS recent
            FROM ping_checks
            WHERE {tenant_clause(params)}
              AND host_id IN ({{hids:Array(UInt32)}})
              AND timestamp >= now() - toIntervalHour({{lb:UInt32}})
            GROUP BY host_id
        )
        WHERE length(recent) >= {{n:UInt32}}
          AND arraySum(x -> x.2, recent) = 0
    """
    rows = await query(sql, params)
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
    timestamp still collapse into one. Both reads carry the tenant predicate.
    """
    params: dict = {}
    tenant = tenant_clause(params)
    agent_filter = tenant
    if agent_ids:
        agent_filter += " AND agent_id IN ({aids:Array(UInt32)})"
        params["aids"] = list(agent_ids)
    outer_where = (
        f"WHERE {agent_filter} AND (agent_id, timestamp) IN ("
        f"SELECT agent_id, max(timestamp) FROM agent_metrics WHERE {agent_filter} GROUP BY agent_id"
        ")"
    )
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
    params: dict = {"aid": int(agent_id), "lim": int(limit)}
    where = f"{tenant_clause(params)} AND agent_id = {{aid:UInt32}}"
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
    params: dict = {"h": int(since_hours)}
    where_clauses = [tenant_clause(params), "timestamp >= now() - toIntervalHour({h:UInt32})"]
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
    params: dict = {"h": int(hours), "lim": int(limit)}
    where_clauses = [tenant_clause(params), "timestamp >= now() - toIntervalHour({h:UInt32})"]
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
    """Return the most recent prior sample for rate calculation, or None.

    ``source_id`` is not unique across tenants (a UniFi device MAC), so the
    tenant predicate is what keeps one customer's counter from becoming the
    other's baseline.
    """
    params: dict = {"st": source_type, "sid": source_id, "iface": interface_name}
    sql = f"""
        SELECT timestamp, rx_bytes, tx_bytes
        FROM bandwidth_metrics
        WHERE {tenant_clause(params)}
          AND source_type = {{st:String}}
          AND source_id   = {{sid:String}}
          AND interface_name = {{iface:String}}
        ORDER BY timestamp DESC
        LIMIT 1
    """
    rows = await query(sql, params)
    return rows[0] if rows else None


# ── Query primitives ─────────────────────────────────────────────────────────


async def _run_query(client, sql: str, params: dict, settings: dict | None) -> list[dict]:
    from services.tracing import tracer
    with tracer.start_as_current_span("clickhouse.query") as span:
        try:
            if settings:
                result = await client.query(sql, parameters=params, settings=settings)
            else:
                result = await client.query(sql, parameters=params)
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


async def query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a SELECT and return list of row dicts.

    Needs a tenant context. In a tenant scope every read of a tenant table
    must carry :data:`TENANT_FILTER` (the parameter is bound here) and the
    server-side filters of :func:`tenant_query_settings` apply; in
    ``system_scope()`` the read is installation-wide.
    """
    ctx = _require_context()
    params = dict(params or {})
    if ctx.system:
        client = await get_client()
        return await _run_query(client, sql, params, None)
    tenant_id = int(ctx.tenant_id)
    _check_tenant_predicates(sql)
    if _TENANT_PLACEHOLDER in sql:
        given = params.get(TENANT_PARAM)
        if given is not None and int(given) != tenant_id:
            raise _tenancy.TenantContextError(
                f"ClickHouse query for tenant {given} inside tenant {tenant_id}'s scope"
            )
        params[TENANT_PARAM] = tenant_id
    restricted = bool(CLICKHOUSE_TENANT_URL)
    client = await get_tenant_client()
    return await _run_query(
        client, sql, params, tenant_query_settings(tenant_id, row_policy=restricted),
    )


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
    _require_context()
    if not values:
        return []
    base = dict(other_params or {})
    out: list[dict] = []
    for i in range(0, len(values), chunk_size):
        chunk_params = {**base, list_param: values[i : i + chunk_size]}
        out.extend(await query(sql, chunk_params))
    return out


# ── Retention per tenant ─────────────────────────────────────────────────────
# Table TTLs are the installation's ceiling (they cannot differ per row
# without a retention column, see the spec). A tenant that keeps less gets its
# older rows removed by the nightly per-tenant cleanup, as lightweight DELETEs
# confined to its tenant_id. Only issued when there is something to delete:
# every DELETE is a mutation, an empty one is still work for the server.
#
# table -> (settings key, the table's TTL in days = the ceiling)
_retention_capped_logged: set[tuple[str, int]] = set()

RETENTION_RULES: dict[str, tuple[str, int]] = {
    "ping_checks": ("ping_retention_days", 30),
}


async def enforce_retention(table: str, days: int) -> int:
    """Delete the current tenant's rows of ``table`` older than ``days``.

    Returns the number of rows deleted (0 when nothing was older, or ``days``
    is at or above the table's TTL ceiling, which expires them anyway).
    Requires a tenant scope: an installation-wide sweep is what the TTL does.
    """
    if table not in RETENTION_RULES:
        raise ValueError(f"no per-tenant retention for {table}")
    ctx = _require_context()
    if ctx.system:
        raise _tenancy.TenantContextError("enforce_retention() runs per tenant, not in system scope")
    key, ceiling = RETENTION_RULES[table]
    days = int(days)
    if days > ceiling and (table, ctx.tenant_id) not in _retention_capped_logged:
        _retention_capped_logged.add((table, ctx.tenant_id))
        log.info("Retention: %s=%d for tenant %s exceeds the %d-day TTL of %s; "
                 "rows expire after %d days", key, days, ctx.tenant_id, ceiling, table, ceiling)
    if days <= 0 or days >= ceiling:
        return 0
    params: dict = {"d": days}
    where = f"{tenant_clause(params)} AND timestamp < now() - toIntervalDay({{d:UInt32}})"
    expired = int(await query_scalar(f"SELECT count() FROM {table} WHERE {where}", params) or 0)
    if not expired:
        return 0
    client = await get_client()
    await client.command(f"DELETE FROM {table} WHERE {where}", parameters=params)
    log.info("Retention: deleted %d rows of %s older than %d days (tenant %s)",
             expired, table, days, ctx.tenant_id)
    return expired


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

    The first clause is the tenant predicate (raises without a context).

    Supports special search syntax in `q`:
    - field:key=value  — search extracted_fields Map column
    - country:XX       — filter by geo_country
    """
    since_sql, params = received_since_clause(since)
    clauses = [tenant_clause(params), since_sql]

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

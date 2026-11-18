"""Smoke and tenancy tests against a real ClickHouse server.

The rest of the suite mocks ClickHouse, so a client library change that breaks
the actual connection (clickhouse-connect 1.x needing the aiohttp extra for its
async client, 2026-10-10) passed CI and failed in production. These tests run
only when NODEGLOW_TEST_CLICKHOUSE_URL points at a server — CI starts one in the
"ClickHouse (live)" job, configured like production (clickhouse/config.xml,
init.sql, a user with access management):

    NODEGLOW_TEST_CLICKHOUSE_URL=http://nodeglow:nodeglow@localhost:8123/nodeglow \
        pytest tests/test_live_clickhouse.py

Multi-tenancy phase 6: step A on an old-schema table, inserts carry tenant_id,
reads in tenant A never return tenant B's rows (helpers, raw SQL, the
additional_table_filters net), the row policy of the restricted user, and the
step-B rebuild preserving row counts. Tables the tests create get unique names;
rows in the shared tables use unique ids.
"""
import importlib
import inspect
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from services.tenancy import TenantContextError, system_scope, tenant_scope

LIVE_URL = os.environ.get("NODEGLOW_TEST_CLICKHOUSE_URL", "")

pytestmark = pytest.mark.skipif(not LIVE_URL, reason="NODEGLOW_TEST_CLICKHOUSE_URL not set")

A, B = 101, 102

# The tables as they were before phase 6 (no tenant_id, old sort keys).
OLD_PING_DDL = """
    CREATE TABLE {name}
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
"""

OLD_SYSLOG_DDL = """
    CREATE TABLE {name}
    (
        timestamp       DateTime64(3, 'UTC') NOT NULL,
        received_at     DateTime64(3, 'UTC') NOT NULL DEFAULT now64(),
        source_ip       LowCardinality(String) NOT NULL,
        hostname        LowCardinality(String) DEFAULT '',
        host_id         Nullable(Int32),
        severity        Int8 DEFAULT 6,
        message         String NOT NULL,
        noise_score     Int8 DEFAULT 50,
        INDEX idx_message_bloom message TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (severity, source_ip, timestamp)
    TTL
        toDateTime(timestamp) + INTERVAL 2 DAY WHERE severity = 7,
        toDateTime(timestamp) + INTERVAL 90 DAY WHERE severity IN (0, 1, 2, 3)
    SETTINGS index_granularity = 8192, ttl_only_drop_parts = 0
"""


@pytest.fixture
async def ch(monkeypatch):
    """The production client module, pointed at the live server."""
    import services.clickhouse_client as mod

    monkeypatch.setenv("CLICKHOUSE_URL", LIVE_URL)
    monkeypatch.delenv("CLICKHOUSE_TENANT_URL", raising=False)
    mod = importlib.reload(mod)
    yield mod
    for client in (mod._client, mod._tenant_client):
        if client is not None:
            closed = client.close()  # sync in 0.8, a coroutine in later versions
            if inspect.isawaitable(closed):
                await closed
    importlib.reload(mod)


async def _close(client):
    closed = client.close()
    if inspect.isawaitable(closed):
        await closed


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _nosleep(_seconds):
    return None


async def _rows(client, sql, params=None):
    result = await client.query(sql, parameters=params or {})
    return [dict(zip(result.column_names, r)) for r in result.result_rows]


async def _scalar(client, sql, params=None):
    rows = await _rows(client, sql, params)
    return next(iter(rows[0].values())) if rows else None


async def _sorting_key(client, table):
    return await _scalar(
        client,
        "SELECT sorting_key FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        {"t": table},
    )


# ── Connection ────────────────────────────────────────────────────────────────


async def test_connects_and_queries(ch):
    with system_scope("live test"):
        rows = await ch.query("SELECT 1 AS one, version() AS v")
    assert rows[0]["one"] == 1
    assert rows[0]["v"]


async def test_insert_and_read_back(ch):
    client = await ch.get_client()
    table = f"nodeglow_live_{_uid()}"
    await client.command(f"CREATE TABLE {table} (host_id UInt32, latency Float64) ENGINE = Memory")
    try:
        await client.insert(table, [[1, 1.5], [2, 2.5]], column_names=["host_id", "latency"])
        with system_scope("live test"):
            rows = await ch.query(f"SELECT host_id, latency FROM {table} ORDER BY host_id")
        assert [(r["host_id"], r["latency"]) for r in rows] == [(1, 1.5), (2, 2.5)]
    finally:
        await client.command(f"DROP TABLE IF EXISTS {table}")


async def test_query_without_context_never_reaches_the_server(ch):
    with pytest.raises(TenantContextError):
        await ch.query("SELECT 1")


# ── Schema ────────────────────────────────────────────────────────────────────


async def test_fresh_tables_lead_with_tenant(ch):
    client = await ch.get_client()  # applies the schema
    for table in ("ping_checks", "agent_metrics", "bandwidth_metrics", "syslog_messages"):
        key = await _sorting_key(client, table)
        # The app creates every table itself, syslog_messages included.
        assert key is not None, table
        assert key.startswith("tenant_id, "), (table, key)


async def test_step_a_on_an_old_schema_table(ch):
    client = await ch.get_client()
    ping, syslog = f"ping_old_{_uid()}", f"syslog_old_{_uid()}"
    await client.command(OLD_PING_DDL.format(name=ping))
    await client.command(OLD_SYSLOG_DDL.format(name=syslog))
    try:
        now = datetime.utcnow()
        await client.insert(ping, [[now, 1, "h", 1, 1.0, 0], [now, 2, "h", 0, None, 0]],
                            column_names=["timestamp", "host_id", "host_name", "success",
                                          "latency_ms", "probe_id"])
        await client.insert(syslog, [[now, now, "10.0.0.1", "h", 1, 3, "m", 50]],
                            column_names=["timestamp", "received_at", "source_ip", "hostname",
                                          "host_id", "severity", "message", "noise_score"])
        for _ in range(2):  # idempotent
            for stmt in ch.tenant_column_alters(ping) + ch.tenant_column_alters(syslog, site=True):
                await client.command(stmt)

        # Existing rows read as tenant 1 — the backfill — without a mutation.
        assert await _rows(client, f"SELECT tenant_id, count() AS n FROM {ping} GROUP BY tenant_id") == [
            {"tenant_id": 1, "n": 2}]
        assert await _rows(client, f"SELECT tenant_id, site_id FROM {syslog}") == [
            {"tenant_id": 1, "site_id": 0}]
        assert await _scalar(client, (
            "SELECT count() FROM system.mutations WHERE database = currentDatabase() "
            "AND table = {t:String}"), {"t": ping}) == 0

        await client.insert(ping, [[now, 3, "h", 1, 1.0, 0, A]],
                            column_names=["timestamp", "host_id", "host_name", "success",
                                          "latency_ms", "probe_id", "tenant_id"])
        assert await _scalar(client, f"SELECT count() FROM {ping} WHERE tenant_id = {A}") == 1
        assert await _scalar(client, f"SELECT count() FROM {ping} WHERE tenant_id = 1") == 2
    finally:
        await client.command(f"DROP TABLE IF EXISTS {ping}")
        await client.command(f"DROP TABLE IF EXISTS {syslog}")


# ── Inserts and isolated reads ────────────────────────────────────────────────


async def _seed_two_tenants(ch):
    host_a, host_b = random.randint(10**8, 2 * 10**8), random.randint(2 * 10**8, 3 * 10**8)
    # Aware UTC: clickhouse-connect sends a naive datetime as local time, which
    # on a non-UTC machine moves the rows an hour or more back, out of the
    # one-hour window of get_latest_bandwidth_per_iface (flaky outside CI).
    now = datetime.now(timezone.utc)
    await ch.insert_ping_checks([
        {"tenant_id": A, "timestamp": now - timedelta(minutes=2), "host_id": host_a, "success": True, "latency_ms": 1.0},
        {"tenant_id": A, "timestamp": now - timedelta(minutes=1), "host_id": host_a, "success": False},
        {"tenant_id": B, "timestamp": now - timedelta(minutes=1), "host_id": host_b, "success": True, "latency_ms": 2.0},
        # Same host id in the other tenant: the tenant decides, not the id.
        {"tenant_id": B, "timestamp": now, "host_id": host_a, "success": True, "latency_ms": 9.0},
    ])
    await ch.insert_agent_metrics([
        {"tenant_id": A, "timestamp": now, "agent_id": host_a, "agent_name": "a", "cpu_pct": 1.0},
        {"tenant_id": B, "timestamp": now, "agent_id": host_a, "agent_name": "CANARY-B", "cpu_pct": 99.0},
    ])
    await ch.insert_bandwidth_metrics([
        {"tenant_id": A, "timestamp": now, "source_type": "unifi", "source_id": f"mac-{host_a}",
         "interface_name": "p1", "rx_rate_bps": 1},
        {"tenant_id": B, "timestamp": now, "source_type": "unifi", "source_id": f"mac-{host_a}",
         "interface_name": "p1", "rx_rate_bps": 999},
    ])
    return host_a, host_b, now


async def test_reads_in_one_tenant_never_return_the_others_rows(ch):
    host_a, host_b, now = await _seed_two_tenants(ch)

    with tenant_scope(A):
        latest = await ch.get_latest_ping_per_host([host_a, host_b])
        uptime = await ch.get_ping_uptime([host_a, host_b])
        history = await ch.get_ping_history(host_a)
        transitions = await ch.get_ping_status_transitions(host_a)
        offline = await ch.get_offline_hosts_since([host_a, host_b], 1)
        agents = await ch.get_latest_agent_metrics([host_a])
        agent_hist = await ch.get_agent_history(host_a)
        bw = await ch.get_latest_bandwidth_per_iface(source_type="unifi", source_id=f"mac-{host_a}")
        bw_hist = await ch.get_bandwidth_history_ch(source_id=f"mac-{host_a}")
        prev = await ch.get_previous_bandwidth_sample("unifi", f"mac-{host_a}", "p1")
        raw = await ch.query(
            f"SELECT host_id, count() AS n FROM ping_checks WHERE {ch.TENANT_FILTER} "
            "AND host_id IN ({hids:Array(UInt32)}) GROUP BY host_id",
            {"hids": [host_a, host_b]},
        )

    assert set(latest) == {host_a} and latest[host_a]["success"] == 0
    assert set(uptime) == {host_a} and uptime[host_a]["total"] == 2
    assert len(history) == 2 and len(transitions) == 2
    assert offline == [host_a]
    assert agents[host_a]["agent_name"] == "a" and len(agent_hist) == 1
    assert [r["rx_rate_bps"] for r in bw] == [1] and [r["rx_rate_bps"] for r in bw_hist] == [1]
    assert prev["rx_bytes"] == 0
    assert raw == [{"host_id": host_a, "n": 2}]

    with tenant_scope(B):
        latest_b = await ch.get_latest_ping_per_host([host_a, host_b])
        agents_b = await ch.get_latest_agent_metrics([host_a])
    assert set(latest_b) == {host_a, host_b} and latest_b[host_a]["latency_ms"] == pytest.approx(9.0)
    assert agents_b[host_a]["agent_name"] == "CANARY-B"


async def test_table_filters_confine_a_read_on_the_server(ch):
    """The additional_table_filters net, bypassing the client-side guard: an
    unfiltered raw read still returns only the tenant's rows on 24.8 —
    subqueries and joins included."""
    host_a, host_b, _ = await _seed_two_tenants(ch)
    client = await ch.get_client()
    settings = ch.tenant_query_settings(A, row_policy=False)
    params = {"hids": [host_a, host_b]}
    plain = await ch._run_query(
        client, "SELECT count() AS n FROM ping_checks WHERE host_id IN ({hids:Array(UInt32)})",
        params, settings)
    sub = await ch._run_query(client, (
        "SELECT count() AS n FROM (SELECT host_id FROM ping_checks "
        "WHERE host_id IN ({hids:Array(UInt32)})) AS t "
        "JOIN (SELECT DISTINCT agent_id FROM agent_metrics) AS a ON a.agent_id = t.host_id"
    ), params, settings)
    # Only tenant B has an agent named CANARY-B: in A the IN-subquery is empty.
    canary_sql = (
        "SELECT count() AS n FROM ping_checks WHERE host_id IN "
        "(SELECT agent_id FROM agent_metrics WHERE agent_name = 'CANARY-B')"
    )
    canary = await ch._run_query(client, canary_sql, {}, settings)
    unfiltered = await ch._run_query(
        client, "SELECT count() AS n FROM ping_checks WHERE host_id IN ({hids:Array(UInt32)})",
        params, None)
    assert plain[0]["n"] == 2
    assert sub[0]["n"] == 2  # host_a's two rows in A, joined with A's agent row
    assert canary[0]["n"] == 0
    assert unfiltered[0]["n"] == 4
    assert (await ch._run_query(client, canary_sql, {}, None))[0]["n"] >= 3


async def test_syslog_insert_carries_tenant_and_site(ch):
    client = await ch.get_client()
    assert await _sorting_key(client, "syslog_messages") is not None
    marker = f"live-{_uid()}"
    now = datetime.utcnow()
    await ch.insert_batch([
        {"tenant_id": A, "site_id": 7, "timestamp": now, "received_at": now, "source_ip": "10.0.0.1",
         "message": marker, "severity": 3},
        {"tenant_id": B, "timestamp": now, "received_at": now, "source_ip": "10.0.0.1",
         "message": marker, "severity": 3},
    ])
    with tenant_scope(A):
        where, params = ch._where_clauses(now - timedelta(hours=1), q=marker)
        rows = await ch.query(f"SELECT tenant_id, site_id FROM syslog_messages WHERE {where}", params)
    assert rows == [{"tenant_id": A, "site_id": 7}]


# ── Purge of a deleted tenant (ee, phase 9) ───────────────────────────────────


async def test_purge_deletes_every_row_of_one_tenant_and_nothing_else(ch):
    """The ee purge: a lightweight DELETE per tenant table, confined to the
    tenant; another tenant's rows (same host ids) stay. Tenant ids are unique
    to this run — the tables are shared with the other live tests."""
    purge = pytest.importorskip("nodeglow_ee.tenancy.purge")
    gone, kept = random.randint(10**6, 2 * 10**6), random.randint(2 * 10**6, 3 * 10**6)
    host = random.randint(10**8, 2 * 10**8)
    now = datetime.utcnow()
    for tid in (gone, kept):
        await ch.insert_ping_checks([{"tenant_id": tid, "timestamp": now - timedelta(minutes=i),
                                      "host_id": host, "success": True} for i in range(3)])
        await ch.insert_agent_metrics([{"tenant_id": tid, "timestamp": now, "agent_id": host,
                                        "agent_name": f"t{tid}", "cpu_pct": 1.0}])
        await ch.insert_bandwidth_metrics([{"tenant_id": tid, "timestamp": now, "source_type": "unifi",
                                            "source_id": f"mac-{host}", "interface_name": "p1",
                                            "rx_rate_bps": 1}])
        await ch.insert_batch([{"tenant_id": tid, "timestamp": now, "received_at": now,
                                "source_ip": "10.0.0.1", "message": f"purge-{tid}", "severity": 3}])

    async def counts(tid):
        out = {}
        with tenant_scope(tid):
            for table in ch.TENANT_TABLES:
                out[table] = await ch.query_scalar(
                    f"SELECT count() FROM {table} WHERE {ch.TENANT_FILTER}")
        return out

    assert await counts(gone) == {"ping_checks": 3, "agent_metrics": 1, "bandwidth_metrics": 1,
                                  "syslog_messages": 1}
    before_kept = await counts(kept)
    report = await purge.purge_clickhouse(gone)
    assert {t: report[t] for t in ch.TENANT_TABLES} == {
        "ping_checks": 3, "agent_metrics": 1, "bandwidth_metrics": 1, "syslog_messages": 1}
    assert set((await counts(gone)).values()) == {0}
    assert await counts(kept) == before_kept
    # Idempotent: a resumed purge finds nothing and deletes nothing.
    assert {t: v for t, v in (await purge.purge_clickhouse(gone)).items()
            if t in ch.TENANT_TABLES} == {t: 0 for t in ch.TENANT_TABLES}


# ── Row policy (restricted user) ──────────────────────────────────────────────


async def test_row_policy_rejects_queries_without_the_setting(ch, monkeypatch):
    admin = await ch.get_client()
    ch.allow_custom_setting(admin, ch.ROW_POLICY_SETTING)
    try:
        await admin.query(f"SELECT getSetting('{ch.ROW_POLICY_SETTING}')",
                          settings={ch.ROW_POLICY_SETTING: "1"})
    except Exception as exc:
        pytest.skip(f"server does not accept the custom setting (custom_settings_prefixes): {exc}")
    user, password = f"nodeglow_app_{_uid()}", f"pw-{_uid()}"
    database = await _scalar(admin, "SELECT currentDatabase()")
    try:
        await admin.command(f"CREATE USER IF NOT EXISTS {user}_probe")
        await admin.command(f"DROP USER IF EXISTS {user}_probe")
    except Exception as exc:
        pytest.skip(f"test user cannot manage access (CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT): {exc}")

    parts = LIVE_URL.split("://", 1)
    host = parts[1].split("@", 1)[-1].split("/", 1)[0]
    dsn = f"{parts[0]}://{user}:{password}@{host}/{database}"
    host_a, host_b, _ = await _seed_two_tenants(ch)
    restricted = None
    try:
        await ch.provision_tenant_user(admin, dsn)
        restricted = await ch.connect(dsn)
        ch.allow_custom_setting(restricted, ch.ROW_POLICY_SETTING)
        sql = "SELECT count() AS n FROM ping_checks WHERE host_id IN ({hids:Array(UInt32)})"
        params = {"hids": [host_a, host_b]}

        in_a = await _rows_with(restricted, sql, params, {ch.ROW_POLICY_SETTING: str(A)})
        in_b = await _rows_with(restricted, sql, params, {ch.ROW_POLICY_SETTING: str(B)})
        assert in_a[0]["n"] == 2 and in_b[0]["n"] == 2
        with pytest.raises(Exception):  # noqa: B017 - the server's error class varies by version
            await _rows_with(restricted, sql, params, None)
        # Only the tenant tables are granted (not the database: the step-B
        # work tables hold every tenant's rows without a policy).
        other = f"nodeglow_live_{_uid()}"
        await admin.command(f"CREATE TABLE {other} (x UInt8) ENGINE = Memory")
        try:
            with pytest.raises(Exception):  # noqa: B017
                await _rows_with(restricted, f"SELECT count() FROM {other}", {}, None)
        finally:
            await admin.command(f"DROP TABLE IF EXISTS {other}")
        # Everyone else (the application's main user) still reads every row.
        assert (await _rows_with(admin, sql, params, None))[0]["n"] == 4

        # Through the module: tenant reads run as the restricted user.
        monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", dsn)
        monkeypatch.setattr(ch, "_tenant_client", None)
        with tenant_scope(A):
            assert len(await ch.get_ping_history(host_a)) == 2
        with tenant_scope(B):
            assert len(await ch.get_ping_history(host_a)) == 1
    finally:
        if restricted is not None:
            await _close(restricted)
        for table in ch.TENANT_TABLES:
            for policy in (ch.ROW_POLICY_NAME, ch.UNRESTRICTED_POLICY_NAME):
                await admin.command(f"DROP ROW POLICY IF EXISTS {policy} ON {database}.{table}")
        await admin.command(f"DROP USER IF EXISTS {user}")


async def _rows_with(client, sql, params, settings):
    if settings:
        result = await client.query(sql, parameters=params, settings=settings)
    else:
        result = await client.query(sql, parameters=params)
    return [dict(zip(result.column_names, r)) for r in result.result_rows]


# ── Step B: the rebuild ───────────────────────────────────────────────────────


async def _counts(client, table):
    return {
        (r["partition_id"], r["tenant_id"]): r["n"]
        for r in await _rows(client, f"SELECT _partition_id AS partition_id, tenant_id, count() AS n "
                                     f"FROM {table} GROUP BY partition_id, tenant_id")
    }


async def test_step_b_rebuild_preserves_counts_and_resumes(ch, monkeypatch):
    from services import clickhouse_rebuild as rb

    await ch.get_client()
    admin = await ch.connect(LIVE_URL, send_receive_timeout=600)
    table = f"ping_rb_{_uid()}"
    old, work = table + rb.OLD_SUFFIX, table + rb.WORK_SUFFIX
    cols = ["timestamp", "host_id", "host_name", "success", "latency_ms", "probe_id"]
    try:
        await rb._ensure_progress_table(admin)
        await admin.command(OLD_PING_DDL.format(name=table))
        now = datetime.utcnow().replace(microsecond=0)
        # Three daily partitions, several parts each, before step A (tenant 1)…
        for day in range(3):
            for batch in range(3):
                await admin.insert(table, [
                    [now - timedelta(days=day, minutes=i), batch * 100 + i, "h", i % 2, float(i), 0]
                    for i in range(50)
                ], column_names=cols)
        for stmt in ch.tenant_column_alters(table):
            await admin.command(stmt)
        # …and after it, for another tenant.
        await admin.insert(table, [
            [now - timedelta(days=d, minutes=1), 7, "h", 1, 1.0, 0, A] for d in range(3)
        ], column_names=cols + ["tenant_id"])
        before = await _counts(admin, table)
        assert sum(before.values()) == 3 * 3 * 50 + 3

        # A run that runs out of budget at once leaves a resumable work table.
        assert await rb.rebuild_table(admin, table, budget_seconds=0, waits=(0,), sleep=_nosleep) == "copying"
        assert (await _sorting_key(admin, work)).startswith("tenant_id, ")
        # Rows keep arriving in between.
        await admin.insert(table, [[now, 9, "h", 1, 1.0, 0, A]], column_names=cols + ["tenant_id"])
        before[(now.strftime("%Y%m%d"), A)] = before.get((now.strftime("%Y%m%d"), A), 0) + 1

        assert await rb.rebuild_table(admin, table, waits=(0,), sleep=_nosleep) == "rebuilt"
        assert (await _sorting_key(admin, table)).startswith("tenant_id, host_id")
        assert await _counts(admin, table) == before
        # The old data is kept for the grace period, with the same counts.
        assert await _counts(admin, old) == before
        assert await _scalar(admin, (
            "SELECT default_kind FROM system.columns WHERE database = currentDatabase() "
            "AND table = {t:String} AND name = 'tenant_id'"), {"t": table}) == ""

        # A row that reached the old table late (an insert in flight during
        # the exchange) is carried over by the delta pass.
        await admin.insert(old, [[now, 11, "h", 1, 1.0, 0, B]], column_names=cols + ["tenant_id"])
        assert await rb.copy_delta(admin, old, table, table) == 1
        assert await _scalar(admin, f"SELECT count() FROM {table} WHERE tenant_id = {B}") == 1

        # A ClickHouse restart lifts the merge blockers on the old table;
        # merges then rewrite its parts. That is not a reason for "attention".
        await admin.command(f"SYSTEM START MERGES {old}")
        await admin.command(f"OPTIMIZE TABLE {old} FINAL")
        assert await rb.rebuild_table(admin, table, waits=(0,), sleep=_nosleep) == "grace"
        monkeypatch.setattr(rb, "GRACE_HOURS", 0)
        assert await rb.rebuild_table(admin, table, waits=(0,), sleep=_nosleep) == "done"
        assert await rb.table_info(admin, old) is None
        assert await rb.rebuild_table(admin, table, waits=(0,), sleep=_nosleep) == "done"
    finally:
        for name in (table, old, work, table + rb.DELTA_SUFFIX):
            await admin.command(f"DROP TABLE IF EXISTS {name}")
        await _close(admin)


async def test_step_b_rebuild_keeps_syslog_ttl_and_indexes(ch):
    from services import clickhouse_rebuild as rb

    await ch.get_client()
    admin = await ch.connect(LIVE_URL, send_receive_timeout=600)
    table = f"syslog_rb_{_uid()}"
    old, work = table + rb.OLD_SUFFIX, table + rb.WORK_SUFFIX
    try:
        await rb._ensure_progress_table(admin)
        await admin.command(OLD_SYSLOG_DDL.format(name=table))
        for stmt in ch.tenant_column_alters(table, site=True):
            await admin.command(stmt)
        now = datetime.utcnow().replace(microsecond=0)
        await admin.insert(table, [
            [now - timedelta(hours=i), now, f"10.0.0.{i % 5}", "h", i, 3, f"msg {i}", 50, (i % 2) + 1, 0]
            for i in range(200)
        ], column_names=["timestamp", "received_at", "source_ip", "hostname", "host_id", "severity",
                         "message", "noise_score", "tenant_id", "site_id"])
        before = await _counts(admin, table)
        engine_before = await _scalar(admin, (
            "SELECT engine_full FROM system.tables WHERE database = currentDatabase() AND name = {t:String}"
        ), {"t": table})

        assert await rb.rebuild_table(admin, table, waits=(0,), sleep=_nosleep) == "rebuilt"
        assert await _counts(admin, table) == before
        engine_after = await _scalar(admin, (
            "SELECT engine_full FROM system.tables WHERE database = currentDatabase() AND name = {t:String}"
        ), {"t": table})
        assert "ORDER BY (tenant_id, severity, source_ip, timestamp)" in engine_after
        # TTL rules and settings are the installation's own, unchanged.
        assert engine_before.split(" TTL ", 1)[1] == engine_after.split(" TTL ", 1)[1]
        assert await _rows(admin, (
            "SELECT name FROM system.data_skipping_indices WHERE database = currentDatabase() "
            "AND table = {t:String}"), {"t": table}) == [{"name": "idx_message_bloom"}]
    finally:
        for name in (table, old, work, table + rb.DELTA_SUFFIX):
            await admin.command(f"DROP TABLE IF EXISTS {name}")
        await _close(admin)


async def test_step_b_refuses_when_disk_is_short(ch, monkeypatch):
    from services import clickhouse_rebuild as rb

    await ch.get_client()
    admin = await ch.connect(LIVE_URL)
    table = f"ping_disk_{_uid()}"
    try:
        await admin.command(OLD_PING_DDL.format(name=table))
        for stmt in ch.tenant_column_alters(table):
            await admin.command(stmt)
        await admin.insert(table, [[datetime.utcnow(), 1, "h", 1, 1.0, 0]],
                           column_names=["timestamp", "host_id", "host_name", "success", "latency_ms", "probe_id"])
        size = await rb.table_bytes(admin, table)
        free = await rb.free_bytes(admin, table)
        assert size > 0 and free > 0

        async def tiny_disk(c, t):
            return 2 * size - 1
        monkeypatch.setattr(rb, "free_bytes", tiny_disk)
        with pytest.raises(rb.RebuildRefused):
            await rb.rebuild_table(admin, table)
        assert await rb.table_info(admin, table + rb.WORK_SUFFIX) is None
    finally:
        await admin.command(f"DROP TABLE IF EXISTS {table}")
        await _close(admin)


# ── Metric rollups ────────────────────────────────────────────────────────────


@pytest.fixture
async def rollups_clean(ch):
    """Rollup views, tables and the marker table gone before and after a test:
    the raw tables are shared, and maintain() is meant to run once per install."""
    from services import rollups

    client = await ch.get_client()

    async def wipe():
        for table in rollups.ROLLUP_TABLES:
            await client.command(f"DROP VIEW IF EXISTS {table}_mv")
        for table in rollups.ROLLUP_TABLES:
            await client.command(f"DROP TABLE IF EXISTS {table}")
        await client.command(f"DROP TABLE IF EXISTS {rollups.MARKER_TABLE}")

    await wipe()
    rollups._ready_cache.clear()
    yield client
    await wipe()
    rollups._ready_cache.clear()


def _tid() -> int:
    return random.randint(3 * 10**6, 4 * 10**6)


async def test_raw_inserts_work_with_rollup_views(ch, rollups_clean):
    from services import rollups

    client = rollups_clean
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = await rollups.maintain(client, now=now)
    assert out == {s: "created" for s in rollups.SOURCES}, out
    tid, host = _tid(), random.randint(10**8, 2 * 10**8)
    cut = rollups._next_midnight(now)
    # Past the cutoff the views are live: this is the path that must not break inserts.
    ts = cut + timedelta(minutes=1)
    await ch.insert_ping_checks([{"tenant_id": tid, "timestamp": ts, "host_id": host, "success": True,
                                  "latency_ms": 3.0}])
    # All metrics None in one row: the p95 state and the sums must accept it.
    await ch.insert_agent_metrics([{"tenant_id": tid, "timestamp": ts, "agent_id": host,
                                    "agent_name": "a"}])
    await ch.insert_bandwidth_metrics([{"tenant_id": tid, "timestamp": ts, "source_type": "unifi",
                                        "source_id": f"mac-{host}", "interface_name": "p1",
                                        "rx_rate_bps": 5, "tx_rate_bps": 7}])
    for table in ("ping_checks", "agent_metrics", "bandwidth_metrics"):
        assert await _scalar(client, f"SELECT count() FROM {table} WHERE tenant_id = {tid}") == 1
    # ...and the live view really wrote them to the rollups.
    assert await _scalar(client, f"SELECT sum(checks) FROM ping_checks_5m WHERE tenant_id = {tid}") == 1
    assert await _scalar(client, f"SELECT sum(cpu_pct_n) FROM agent_metrics_1h WHERE tenant_id = {tid}") == 0
    assert await _scalar(client, f"SELECT sum(rate_n) FROM bandwidth_metrics_1d WHERE tenant_id = {tid}") == 1


async def test_backfill_and_view_do_not_double_count(ch, rollups_clean):
    from services import rollups

    client = rollups_clean
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cut = rollups._next_midnight(now)
    tid, host = _tid(), random.randint(10**8, 2 * 10**8)

    async def ping(ts):
        await ch.insert_ping_checks([{"tenant_id": tid, "timestamp": ts, "host_id": host,
                                      "success": True, "latency_ms": 1.0}])

    await ping(cut - timedelta(hours=2))
    await ping(cut - timedelta(minutes=1))                    # before the views exist
    assert (await rollups.maintain(client, now=now))["ping_checks"] == "created"
    assert not (await rollups.state(client))["ping_checks"].backfilled
    await ping(cut - timedelta(seconds=30))                   # after creation, before the cutoff
    await ping(cut + timedelta(minutes=1))                    # after the cutoff: view only

    async def assert_counts():
        raw = await _scalar(client, f"SELECT count() FROM ping_checks WHERE tenant_id = {tid}")
        assert raw == 4
        for lv in rollups.LEVELS:
            got = await _scalar(
                client, f"SELECT sum(checks) FROM ping_checks_{lv.key} "
                        f"WHERE tenant_id = {tid} AND host_id = {host}")
            assert got == raw, (lv.key, got, raw)

    after = cut + timedelta(minutes=11)
    assert (await rollups.maintain(client, now=cut + timedelta(minutes=5)))["ping_checks"] == "waiting"
    assert (await rollups.maintain(client, now=after))["ping_checks"] == "backfilled"
    assert (await rollups.state(client))["ping_checks"].backfilled
    await assert_counts()
    assert (await rollups.maintain(client, now=after))["ping_checks"] == "ready"
    await assert_counts()

    # Interrupted-backfill retry: marker back to "not backfilled", rows already there.
    await rollups._write_marker(client, "ping_checks", cut, False)
    assert (await rollups.maintain(client, now=after + timedelta(minutes=1)))["ping_checks"] == "backfilled"
    await assert_counts()


async def test_rollup_matches_raw_aggregates(ch, rollups_clean):
    from services import rollups

    client = rollups_clean
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cut = rollups._next_midnight(now)
    tid, agent = _tid(), random.randint(10**8, 2 * 10**8)
    base = cut - timedelta(hours=4)  # on an hour boundary (cut is midnight)
    rng = random.Random(7)
    rows = [
        {"tenant_id": tid, "agent_id": agent, "agent_name": "a",
         "timestamp": base + timedelta(seconds=rng.randrange(3 * 3600)),
         "cpu_pct": None if rng.random() < 0.1 else round(rng.uniform(0, 100), 2)}
        for _ in range(200)
    ]
    await ch.insert_agent_metrics(rows)
    assert (await rollups.maintain(client, now=now))["agent_metrics"] == "created"
    assert (await rollups.maintain(client, now=cut + timedelta(minutes=11)))["agent_metrics"] == "backfilled"

    roll = await _rows(client, f"""
        SELECT bucket, sum(cpu_pct_sum) AS s, sum(cpu_pct_n) AS n, min(cpu_pct_min) AS mn,
               max(cpu_pct_max) AS mx, quantileTDigestMerge(0.95)(cpu_pct_p95) AS p95
        FROM agent_metrics_1h WHERE tenant_id = {tid} AND agent_id = {agent}
        GROUP BY bucket ORDER BY bucket""")
    raw = await _rows(client, f"""
        SELECT toStartOfHour(timestamp, 'UTC') AS bucket, sum(cpu_pct) AS s, count(cpu_pct) AS n,
               min(cpu_pct) AS mn, max(cpu_pct) AS mx, quantile(0.95)(cpu_pct) AS p95
        FROM agent_metrics WHERE tenant_id = {tid} AND agent_id = {agent}
        GROUP BY bucket ORDER BY bucket""")
    assert len(raw) >= 3 and len(roll) == len(raw)
    for r, w in zip(roll, raw):
        assert r["bucket"] == w["bucket"]
        assert r["n"] == w["n"]
        assert r["s"] == pytest.approx(w["s"], rel=1e-6)
        assert r["mn"] == w["mn"] and r["mx"] == w["mx"]
        # A t-digest on ~60 samples is no exact quantile: compare by rank, the
        # digest's p95 must sit between the raw p90 and p99 (and, loosely, near
        # the raw value).
        vals = sorted(x["cpu_pct"] for x in rows if x["cpu_pct"] is not None
                      and x["timestamp"].replace(minute=0, second=0, microsecond=0) == r["bucket"].replace(tzinfo=timezone.utc))
        rank = sum(v <= r["p95"] + 1e-4 for v in vals) / len(vals)
        assert 0.90 <= rank <= 0.99, (r["bucket"], r["p95"], rank)
        assert r["p95"] == pytest.approx(w["p95"], abs=6.0)


async def test_series_readers_over_rolled_up_ranges(ch, rollups_clean):
    from services import rollups

    client = rollups_clean
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cut = rollups._next_midnight(now)
    tid, host = _tid(), random.randint(10**8, 2 * 10**8)
    # Old enough for the 5m range (> 48 h), before the cutoff: backfilled rows.
    base = (now - timedelta(hours=60)).replace(minute=0, second=0)
    rows = [{"tenant_id": tid, "timestamp": base + timedelta(minutes=i), "host_id": host,
             "success": i % 10 != 0, "latency_ms": None if i % 10 == 0 else float(i % 7 + 1)}
            for i in range(120)]
    # One bucket of failed pings only: no latency, must read as None.
    rows += [{"tenant_id": tid, "timestamp": base + timedelta(hours=3, minutes=i), "host_id": host,
              "success": False, "latency_ms": None} for i in range(3)]
    await ch.insert_ping_checks(rows)
    await ch.insert_agent_metrics([{"tenant_id": tid, "timestamp": base + timedelta(minutes=i),
                                    "agent_id": host, "agent_name": "a",
                                    "cpu_pct": float(i % 50)} for i in range(60)])
    await ch.insert_bandwidth_metrics([{"tenant_id": tid, "timestamp": base + timedelta(minutes=i),
                                        "source_type": "agent", "source_id": str(host),
                                        "interface_name": "total", "rx_rate_bps": 100 + i,
                                        "tx_rate_bps": 10} for i in range(60)])
    assert set((await rollups.maintain(client, now=now)).values()) == {"created"}
    assert set((await rollups.maintain(client, now=cut + timedelta(minutes=11))).values()) == {"backfilled"}
    rollups._ready_cache.clear()

    with tenant_scope(tid):
        res = await rollups.ping_series(host, hours=24 * 7)
        assert res.level == "5m" and res.bucket_seconds == 300
        assert sum(p["checks"] for p in res.points) == 123
        assert sum(p["ok"] for p in res.points) == 108
        full = [p for p in res.points if p["latency_avg"] is not None]
        assert full and all(1 <= p["latency_min"] <= p["latency_p95"] <= p["latency_max"] <= 7
                            for p in full)
        empty = [p for p in res.points if p["ok"] == 0]
        assert empty and all(p["latency_avg"] is None and p["latency_p95"] is None for p in empty)
        res_1h = await rollups.ping_series(host, hours=24 * 60)
        assert res_1h.level == "1h" and sum(p["checks"] for p in res_1h.points) == 123
        ag = await rollups.agent_series(host, hours=24 * 7)
        assert ag.level == "5m" and ag.points
        assert all(p["mem_pct_avg"] is None and p["mem_pct_p95"] is None for p in ag.points)
        assert max(p["cpu_pct_max"] for p in ag.points) == 49.0
        bw = await rollups.bandwidth_series("agent", str(host), "total", hours=24 * 7)
        assert bw.level == "5m" and max(p["rx_rate_max"] for p in bw.points) == 159
        assert all(p["tx_rate_avg"] == 10 for p in bw.points)
        total = await ch.get_bandwidth_total_history_ch(hours=24 * 7)
        assert any(r["agents_n"] == 1 for r in total)

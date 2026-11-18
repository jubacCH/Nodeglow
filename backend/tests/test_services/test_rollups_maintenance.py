"""Rollup maintenance against a recording fake client (no server)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import clickhouse_rebuild, rollups
from services import clickhouse_client as ch

ALL_REBUILT = {t: "rebuilt" for t in ch.TENANT_TABLES}
MARKER = "nodeglow_rollup_state"
T_CREATE = datetime(2026, 11, 18, 10, 17)
CUTOFF = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)


class FakeCH:
    """Records every statement; keeps the marker rows and existing objects."""

    def __init__(self, fail_on: str | None = None):
        self.log: list[str] = []
        self.marker: dict[str, tuple] = {}
        self.existing: set[str] = set()
        self.fail_on = fail_on

    async def command(self, sql, parameters=None, settings=None):
        sql = " ".join(sql.split())
        self.log.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        if sql.startswith("DROP VIEW IF EXISTS "):
            self.existing.discard(sql.split()[-1])
        for kw in ("CREATE TABLE IF NOT EXISTS ", "CREATE MATERIALIZED VIEW IF NOT EXISTS "):
            if sql.startswith(kw):
                self.existing.add(sql[len(kw):].split()[0])
        return ""

    async def insert(self, table, data, column_names=None):
        assert table == MARKER
        for row in data:
            rec = dict(zip(column_names, row))
            self.marker[rec["source"]] = (rec["cutoff"], bool(rec["backfilled"]))
            self.log.append(f"INSERT INTO {MARKER} {rec['source']} backfilled={rec['backfilled']}")

    async def query(self, sql, parameters=None):
        if "FROM nodeglow_rollup_state" in sql:
            rows = [(s, c, int(b)) for s, (c, b) in self.marker.items()]
            return SimpleNamespace(column_names=["source", "cutoff", "backfilled"], result_rows=rows)
        assert "system.tables" in sql
        names = [n for n in parameters["names"] if n in self.existing]
        return SimpleNamespace(column_names=["name"], result_rows=[(n,) for n in names])

    def matching(self, *needles):
        return [s for s in self.log if all(n in s for n in needles)]

    def index_of(self, needle):
        return next(i for i, s in enumerate(self.log) if needle in s)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "")
    monkeypatch.setattr(ch, "PROVISION_TENANT_USER", True)
    rollups._ready_cache.clear()
    return None


def rebuild(monkeypatch, status):
    async def fake_status(c=None):
        return dict(status)

    monkeypatch.setattr(clickhouse_rebuild, "status", fake_status)


async def test_deferred_while_rebuild_runs(monkeypatch):
    rebuild(monkeypatch, {**ALL_REBUILT, "ping_checks": "copying"})
    fake = FakeCH()
    out = await rollups.maintain(fake, now=T_CREATE)
    assert out["ping_checks"] == "deferred"
    assert out["agent_metrics"] == "created"
    assert not [s for s in fake.matching("ping_checks")]  # nothing touches ping_checks


async def test_create_writes_marker_before_views(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    out = await rollups.maintain(fake, now=T_CREATE)
    assert out == {s: "created" for s in rollups.SOURCES}
    for source in rollups.SOURCES:
        marker_write = fake.index_of(f"INSERT INTO {MARKER} {source}")
        assert marker_write < fake.index_of(f"CREATE TABLE IF NOT EXISTS {source}_5m")
        assert marker_write < fake.index_of(f"CREATE MATERIALIZED VIEW IF NOT EXISTS {source}_5m_mv")
    assert "2026-11-19 00:00:00.000" in fake.matching("CREATE MATERIALIZED VIEW")[0]
    assert len(fake.matching("CREATE MATERIALIZED VIEW")) == 9
    assert len(fake.matching("CREATE TABLE IF NOT EXISTS", "AggregatingMergeTree")) == 9
    assert fake.marker["ping_checks"] == (CUTOFF, False)
    assert not fake.matching("INSERT INTO ping_checks_5m")  # no backfill yet


async def test_grace_counts_as_rebuilt(monkeypatch):
    rebuild(monkeypatch, {**ALL_REBUILT, "bandwidth_metrics": "grace"})
    out = await rollups.maintain(FakeCH(), now=T_CREATE)
    assert out["bandwidth_metrics"] == "created"


async def test_attention_and_pending_are_deferred(monkeypatch):
    rebuild(monkeypatch, {**ALL_REBUILT, "ping_checks": "attention", "agent_metrics": "pending",
                          "bandwidth_metrics": "missing"})
    out = await rollups.maintain(FakeCH(), now=T_CREATE)
    assert set(out.values()) == {"deferred"}


async def test_backfill_waits_ten_minutes_after_cutoff(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    await rollups.maintain(fake, now=T_CREATE)
    out = await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 5))
    assert set(out.values()) == {"waiting"}
    assert not fake.matching("ALTER TABLE") and not fake.matching("INSERT INTO ping_checks_5m")

    out = await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 11))
    assert set(out.values()) == {"backfilled"}
    for source in rollups.SOURCES:
        for lv in rollups.LEVELS:
            table = rollups.rollup_table(source, lv.key)
            delete = fake.index_of(f"ALTER TABLE {table} DELETE WHERE bucket < toDateTime('2026-11-19 00:00:00'")
            insert = fake.index_of(f"INSERT INTO {table} (")
            assert delete < insert
            sql = fake.matching(f"INSERT INTO {table} (")[0]
            assert f"FROM {source} WHERE timestamp < toDateTime64('2026-11-19 00:00:00.000', 3, 'UTC') GROUP BY" in sql
        assert fake.marker[source] == (CUTOFF, True)
    assert fake.matching("mutations_sync = 2")
    # the marker is flipped only after all three levels of that source
    last_insert = max(i for i, s in enumerate(fake.log) if s.startswith("INSERT INTO agent_metrics_"))
    assert fake.index_of(f"INSERT INTO {MARKER} agent_metrics backfilled=1") > last_insert

    # an earlier run's backfill is "ready" and does no further work
    before = len(fake.log)
    out = await rollups.maintain(fake, now=datetime(2026, 11, 20, 3, 0))
    assert set(out.values()) == {"ready"}
    assert not [s for s in fake.log[before:] if "INSERT" in s or "ALTER" in s]


async def test_retry_after_interrupted_backfill_deletes_before_insert(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    await rollups.maintain(fake, now=T_CREATE)
    fake.fail_on = "INSERT INTO agent_metrics_1h ("
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 11))
    assert fake.marker["agent_metrics"] == (CUTOFF, False)  # not marked done
    fake.fail_on = None
    fake.log.clear()
    out = await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 21))
    assert out["agent_metrics"] == "backfilled"
    # every level is cleaned again before it is filled again
    for lv in rollups.LEVELS:
        table = rollups.rollup_table("agent_metrics", lv.key)
        assert fake.index_of(f"ALTER TABLE {table} DELETE") < fake.index_of(f"INSERT INTO {table} (")
    assert fake.marker["agent_metrics"] == (CUTOFF, True)


async def test_ddl_failure_raises(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH(fail_on="CREATE MATERIALIZED VIEW")
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=T_CREATE)


async def test_views_are_recreated_when_marker_exists_but_ddl_failed(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH(fail_on="CREATE MATERIALIZED VIEW")
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=T_CREATE)
    fake.fail_on = None
    out = await rollups.maintain(fake, now=datetime(2026, 11, 18, 10, 27))
    assert set(out.values()) == {"created"}  # repaired; views exist now
    assert len(fake.existing & {f"{t}_mv" for t in rollups.ROLLUP_TABLES}) == 9
    # the cutoff of the first run is kept
    assert fake.marker["ping_checks"][0] == CUTOFF


async def test_backfill_does_not_run_without_views(monkeypatch):
    """A marker without views must not backfill: rows after the cutoff would be lost."""
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH(fail_on="CREATE MATERIALIZED VIEW")
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=T_CREATE)
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 30))
    assert not fake.matching("INSERT INTO ping_checks_5m (")
    assert fake.marker["ping_checks"][1] is False


async def test_tenant_user_provisioned_once_after_create(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "http://t:t@h/db")
    calls = []
    fake = FakeCH()

    async def fake_provision(client, dsn, database=None, tables=None):
        calls.append((dsn, tables))
        fake.log.append("PROVISION")

    monkeypatch.setattr(ch, "provision_tenant_user", fake_provision)
    await rollups.maintain(fake, now=T_CREATE)
    assert len(calls) == 1 and calls[0][0] == "http://t:t@h/db"
    last_ddl = max(i for i, s in enumerate(fake.log) if s.startswith("CREATE"))
    assert fake.index_of("PROVISION") > last_ddl


async def test_provisioning_skipped_when_operator_manages_access(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "http://t:t@h/db")
    monkeypatch.setattr(ch, "PROVISION_TENANT_USER", False)
    calls = []

    async def fake_provision(*a, **k):
        calls.append(1)

    monkeypatch.setattr(ch, "provision_tenant_user", fake_provision)
    out = await rollups.maintain(FakeCH(), now=T_CREATE)
    assert set(out.values()) == {"created"} and not calls


async def test_provisioning_failure_raises_and_is_retried(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    monkeypatch.setattr(ch, "CLICKHOUSE_TENANT_URL", "http://t:t@h/db")
    calls = []

    async def flaky(client, dsn, database=None, tables=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("no grant")

    monkeypatch.setattr(ch, "provision_tenant_user", flaky)
    fake = FakeCH()
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=T_CREATE)
    await rollups.maintain(fake, now=datetime(2026, 11, 18, 10, 27))
    assert len(calls) == 2


async def test_state_and_ready(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    assert await rollups.state(fake) == {}  # marker table not there: nothing created
    await rollups.maintain(fake, now=T_CREATE)
    st = await rollups.state(fake)
    assert st["ping_checks"] == rollups.RollupState("ping_checks", CUTOFF, False)
    assert not await rollups.ready("ping_checks", fake)
    await rollups.maintain(fake, now=datetime(2026, 11, 19, 0, 11))
    rollups._ready_cache.clear()
    assert await rollups.ready("ping_checks", fake)


async def test_ready_is_cached_for_a_minute(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    await rollups.maintain(fake, now=T_CREATE)
    assert not await rollups.ready("ping_checks", fake)
    fake.marker["ping_checks"] = (CUTOFF, True)
    assert not await rollups.ready("ping_checks", fake)  # cached
    monkeypatch.setattr(rollups.time, "monotonic", lambda: 10**9)
    assert await rollups.ready("ping_checks", fake)


async def test_stale_cutoff_moves_when_views_were_missing_past_it(monkeypatch):
    """Views created only after the cutoff would leave a hole: restart from the next midnight."""
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH(fail_on="CREATE MATERIALIZED VIEW")
    with pytest.raises(RuntimeError):
        await rollups.maintain(fake, now=T_CREATE)
    fake.fail_on = None
    out = await rollups.maintain(fake, now=datetime(2026, 11, 19, 3, 0))
    assert set(out.values()) == {"created"}
    new_cut = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert fake.marker["ping_checks"] == (new_cut, False)
    assert "2026-11-20 00:00:00.000" in fake.matching("CREATE MATERIALIZED VIEW", "ping_checks_5m_mv")[-1]
    assert not fake.matching("INSERT INTO ping_checks_5m (")


async def test_job_propagates_failures_to_the_job_instrumentation(monkeypatch):
    """A failing run must raise (not log and return) so the job check sees it."""
    import scheduler

    async def boom(c=None, now=None):
        raise RuntimeError("ddl failed")

    async def connected():
        return object()

    monkeypatch.setattr(ch, "get_client", connected)
    monkeypatch.setattr(rollups, "maintain", boom)
    with pytest.raises(RuntimeError):
        await scheduler.run_rollup_maintenance()


async def test_job_skips_quietly_when_clickhouse_is_unreachable(monkeypatch):
    import scheduler

    async def down():
        raise ConnectionError("down")

    async def never(c=None, now=None):
        raise AssertionError("maintain must not run")

    monkeypatch.setattr(ch, "get_client", down)
    monkeypatch.setattr(rollups, "maintain", never)
    await scheduler.run_rollup_maintenance()


async def test_job_raises_on_incomplete_schema(monkeypatch):
    """Only a missing connection is skipped; a broken schema is a failure."""
    import scheduler

    async def broken():
        raise ch.SchemaIncomplete("ClickHouse schema incomplete: x")

    monkeypatch.setattr(ch, "get_client", broken)
    with pytest.raises(ch.SchemaIncomplete):
        await scheduler.run_rollup_maintenance()


async def test_get_client_distinguishes_schema_failure_from_no_connection(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(ch.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ch, "_client", None)

    async def refuse(dsn, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(ch, "connect", refuse)
    with pytest.raises(ch.ClickHouseUnreachable):
        await ch.get_client()

    async def connected(dsn, **kw):
        return object()

    async def bad_schema(client):
        raise ch.SchemaIncomplete("ClickHouse schema incomplete: y")

    monkeypatch.setattr(ch, "connect", connected)
    monkeypatch.setattr(ch, "_ensure_schemas", bad_schema)
    with pytest.raises(RuntimeError) as exc:
        await ch.get_client()
    assert not isinstance(exc.value, ch.ClickHouseUnreachable)
    assert isinstance(exc.value.__cause__, ch.SchemaIncomplete)


def test_job_is_registered_every_ten_minutes():
    import inspect as _inspect

    import scheduler

    src = _inspect.getsource(scheduler)
    assert 'add_job(run_rollup_maintenance, "interval", minutes=10' in src


async def test_ready_failure_is_cached_briefly(monkeypatch):
    calls = []

    class Broken:
        async def query(self, *a, **k):
            calls.append(1)
            raise ConnectionError("down")

    clock = {"t": 1000.0}
    monkeypatch.setattr(rollups.time, "monotonic", lambda: clock["t"])
    assert not await rollups.ready("ping_checks", Broken())
    assert not await rollups.ready("ping_checks", Broken())
    assert len(calls) == 1  # second call served from the negative cache
    clock["t"] += 31
    assert not await rollups.ready("ping_checks", Broken())
    assert len(calls) == 2


async def test_midnight_race_moves_cutoff_when_creation_crosses_it(monkeypatch):
    """A run that starts before 00:00 UTC but creates the views after it must not
    leave a gap between the cutoff and the views."""
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    real = rollups.time.monotonic
    ticks = {"n": 0}

    def slow_clock():
        # first reading (run start) then every later reading is 90 s later
        ticks["n"] += 1
        return 0.0 if ticks["n"] == 1 else 90.0

    monkeypatch.setattr(rollups.time, "monotonic", slow_clock)
    out = await rollups.maintain(fake, now=datetime(2026, 11, 18, 23, 59, 0))
    monkeypatch.setattr(rollups.time, "monotonic", real)
    assert set(out.values()) == {"created"}
    new_cut = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert fake.marker["ping_checks"] == (new_cut, False)
    # final views of every level carry the moved cutoff
    for source in rollups.SOURCES:
        for lv in rollups.LEVELS:
            last = fake.matching("CREATE MATERIALIZED VIEW", f"{source}_{lv.key}_mv")[-1]
            assert "2026-11-20 00:00:00.000" in last


async def test_repair_recreates_all_three_views_on_one_cutoff(monkeypatch):
    rebuild(monkeypatch, ALL_REBUILT)
    fake = FakeCH()
    await rollups.maintain(fake, now=T_CREATE)
    fake.existing.discard("ping_checks_1h_mv")  # one view lost
    fake.log.clear()
    out = await rollups.maintain(fake, now=datetime(2026, 11, 19, 3, 0))  # past the cutoff
    assert out["ping_checks"] == "created"
    assert len(fake.matching("DROP VIEW IF EXISTS ping_checks_")) == 3
    cutoffs = {
        s.split("timestamp >= toDateTime64('")[1][:23]
        for s in fake.matching("CREATE MATERIALIZED VIEW", "ping_checks_")
    }
    assert cutoffs == {"2026-11-20 00:00:00.000"}
    assert fake.marker["ping_checks"][0] == datetime(2026, 11, 20, tzinfo=timezone.utc)

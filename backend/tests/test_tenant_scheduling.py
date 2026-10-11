"""Per-tenant scheduling (multi-tenancy phase 5): fair share, cadence,
budgets, tenant-local schedules, limits and per-tenant job metrics.

Timings are measured with a fake clock (``tenant_jobs.monotonic``) or in
"steps" of a driver that releases work deterministically, so the assertions
are about scheduling decisions, not about the speed of the test machine.
The single-tenant cases pin the promise of the phase: one tenant behaves
exactly as before (same call, same order, same number of steps).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base, PingHost, Provider, Setting, Site, Tenant
from models.tenant import TenantSetting
from services import tenant_jobs, tenancy
from services.metrics import instrument_job
from services.tenancy import per_tenant_job, system_scope, tenant_scope
from services.tenant_jobs import (
    CadenceSpec, FairShareLimiter, fleet_job, last_slot, slot_due, tenant_zone,
)

A, B = 1, 2


def _tenants(*ids):
    return patch("services.tenancy.SingleTenancy.active_tenant_ids",
                 new=AsyncMock(return_value=list(ids)))


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(tenant_jobs, "monotonic", c)
    return c


@pytest.fixture
async def sf():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        with system_scope("test: seed two tenants", audit=False):
            s.add(Provider(id=1, name="P", slug="p"))
            s.add_all([Tenant(id=A, provider_id=1, name="A", slug="a"),
                       Tenant(id=B, provider_id=1, name="B", slug="b")])
            s.add_all([Site(id=1, tenant_id=A, name="A"), Site(id=2, tenant_id=B, name="B")])
            await s.commit()
    with patch("models.base.AsyncSessionLocal", factory):
        yield factory
    await engine.dispose()


# ── Fair-share limiter ───────────────────────────────────────────────────────

async def _drive(limiter_factory, work: list[int]) -> dict[int, int]:
    """Submit one item per entry of ``work`` (its tenant), in that order.
    Each step, every item holding a slot finishes. Returns, per tenant, the
    step in which its last item finished — the "time" a tenant waited."""
    lim = limiter_factory()
    finished_at: dict[int, int] = {}
    holding: list[tuple[int, asyncio.Event]] = []

    async def item(tid: int):
        ev = asyncio.Event()
        async with lim(tid):
            holding.append((tid, ev))
            await ev.wait()

    tasks = [asyncio.create_task(item(t)) for t in work]
    step = 0
    while not all(t.done() for t in tasks):
        for _ in range(5):
            await asyncio.sleep(0)
        step += 1
        batch, holding[:] = list(holding), []
        for tid, ev in batch:
            finished_at[tid] = step
            ev.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert step < 1000
    return finished_at


def _semaphore(total):
    def make():
        sem = asyncio.Semaphore(total)
        return lambda _tid: sem
    return make


def _fair(total, cap=None):
    def make():
        lim = FairShareLimiter(total, (lambda _t: cap) if cap else None)
        return lim.slot
    return make


async def test_one_tenant_takes_exactly_as_long_as_with_a_plain_semaphore():
    work = [A] * 17
    assert await _drive(_fair(4), work) == await _drive(_semaphore(4), work) == {A: 5}


async def test_a_small_tenant_is_not_queued_behind_a_large_one():
    work = [A] * 40 + [B] * 4  # A submitted all of its work first
    plain = await _drive(_semaphore(4), work)
    fair = await _drive(_fair(4), work)
    assert plain == {A: 10, B: 11}  # FIFO: B waits for all of A
    # Round-robin: A already holds all 4 slots when B arrives (step 1); from
    # then on B gets every other free slot — two per step. A pays one step.
    assert fair == {A: 11, B: 3}
    assert max(fair.values()) == max(plain.values())  # same total throughput


async def test_per_tenant_cap_reserves_capacity():
    lim = FairShareLimiter(4, lambda _t: 2)
    held = []

    async def hold(tid):
        await lim.acquire(tid)
        held.append(tid)

    tasks = [asyncio.create_task(hold(A)) for _ in range(4)]
    for _ in range(5):
        await asyncio.sleep(0)
    assert held == [A, A] and lim.in_use == 2  # A is capped at 2 of 4
    tasks.append(asyncio.create_task(hold(B)))
    for _ in range(5):
        await asyncio.sleep(0)
    assert held == [A, A, B]  # B gets a free slot at once
    for t in tasks:
        t.cancel()


async def test_cancelled_waiter_gives_up_its_place():
    lim = FairShareLimiter(1)
    await lim.acquire(A)
    waiter = asyncio.create_task(lim.acquire(B))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    lim.release(A)
    assert lim.in_use == 0
    await asyncio.wait_for(lim.acquire(A), 1)  # not handed to the cancelled waiter


async def test_shared_limiter_is_per_event_loop_and_name():
    a = tenant_jobs.limiter("x", 3)
    assert tenant_jobs.limiter("x", 3) is a and a.total == 3
    assert tenant_jobs.limiter("y", 3) is not a


# ── Single tenant: the plain call ────────────────────────────────────────────

async def test_single_tenant_jobs_are_the_plain_call(monkeypatch):
    """No task, no cadence query, no budget: the function runs in the
    caller's task, in tenant 1's scope, and its duration is recorded."""
    outer = asyncio.current_task()
    seen = []

    async def boom(*_a, **_k):
        raise AssertionError("single-tenant runs must not read the cadence settings")

    monkeypatch.setattr(tenant_jobs, "tenant_intervals", boom)

    @instrument_job("pt")
    @per_tenant_job
    async def analysis():
        seen.append(("analysis", asyncio.current_task() is outer, tenancy.current_tenant_id()))

    @instrument_job("fj")
    @fleet_job(cadence_spec=tenant_jobs.PING_CADENCE)
    async def dataplane():
        seen.append(("dataplane", asyncio.current_task() is outer, tenancy.current_tenant_id()))

    await analysis()
    await dataplane()
    await dataplane()  # always due: the tick is this tenant's interval
    assert seen == [("analysis", True, 1), ("dataplane", True, 1), ("dataplane", True, 1)]
    assert tenant_jobs.stats.get("pt", 1).runs == 1
    assert tenant_jobs.stats.get("fj", 1).runs == 2


async def test_single_tenant_job_duration_is_unchanged(clock):
    """Fake clock: the recorded duration is the function's own."""

    @per_tenant_job
    async def job():
        clock.t += 7.5

    await job()
    assert tenant_jobs.stats.get("job", 1).last_duration == 7.5


# ── Several tenants: loops, rotation, isolation, budget ─────────────────────

async def test_analysis_loop_rotates_the_start_tenant():
    order = []

    @per_tenant_job
    async def job():
        order.append(tenancy.current_tenant_id())

    with _tenants(A, B, 3):
        await job()
        await job()
        await job()
    assert order == [A, B, 3, B, 3, A, 3, A, B]


async def test_fleet_job_runs_tenants_concurrently():
    both = asyncio.Barrier(2)

    @fleet_job
    async def job():
        await asyncio.wait_for(both.wait(), 1)  # deadlocks if run one by one

    with _tenants(A, B):
        await job()
    assert tenant_jobs.stats.get("job", A).runs == tenant_jobs.stats.get("job", B).runs == 1


async def test_fleet_job_isolates_a_failing_tenant():
    ran = []

    @fleet_job
    async def job():
        ran.append(tenancy.current_tenant_id())
        if tenancy.current_tenant_id() == A:
            raise RuntimeError("tenant A broke")

    with _tenants(A, B), pytest.raises(RuntimeError, match="tenant A broke"):
        await job()
    assert sorted(ran) == [A, B]
    assert tenant_jobs.stats.get("job", A).failures == 1
    assert tenant_jobs.stats.get("job", B).failures == 0


@pytest.mark.parametrize("decorator", [
    per_tenant_job(budget=0.05), fleet_job(budget=0.05),
], ids=["analysis", "dataplane"])
async def test_a_stuck_tenant_does_not_hold_the_others(decorator, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="nodeglow.tenant_jobs")
    release = asyncio.Event()
    ran = []

    @decorator
    async def job():
        tid = tenancy.current_tenant_id()
        ran.append(tid)
        if tid == A:
            await release.wait()

    with _tenants(A, B):
        await asyncio.wait_for(job(), 2)  # returns although A is still running
        assert ran == [A, B]
        await asyncio.wait_for(job(), 2)  # A is skipped while in flight
        assert ran == [A, B, B]
        st = tenant_jobs.stats.get("job", A)
        assert st.in_flight and st.over_budget == 1 and st.skipped_inflight == 1
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not tenant_jobs.stats.get("job", A).in_flight
        await asyncio.wait_for(job(), 2)
    assert sorted(ran[3:]) == [A, B]
    assert "still running after its" in caplog.text


# ── Per-tenant cadence ───────────────────────────────────────────────────────

async def test_each_tenant_runs_at_its_own_interval(clock, monkeypatch):
    """A every 10 s, B every 60 s; the job ticks at 10 s. Over two minutes A
    runs on every tick and B every sixth — not at A's pace."""
    spec = CadenceSpec("ping_interval", "ping_interval_min")
    monkeypatch.setattr(tenant_jobs, "tenant_intervals",
                        AsyncMock(return_value={A: 10, B: 60}))
    tenant_jobs.cadence.ticks["job"] = 10
    runs = {A: 0, B: 0}

    @fleet_job(cadence_spec=spec)
    async def job():
        runs[tenancy.current_tenant_id()] += 1

    with _tenants(A, B):
        for _ in range(12):
            await job()
            clock.t += 10.02  # scheduler jitter
    assert runs == {A: 12, B: 2}


async def test_cadence_tolerates_early_ticks(clock):
    c = tenant_jobs.cadence
    c.mark("j", A)
    clock.t += 59.0  # one second early
    assert c.due("j", A, 60, tick=60)
    clock.t -= 30.0
    assert not c.due("j", A, 60, tick=60)


async def test_cadence_never_runs_a_tenant_faster_than_its_interval(clock, monkeypatch):
    """90 s next to a 60 s tenant (tick 60): due at the 120 s tick, not every tick."""
    monkeypatch.setattr(tenant_jobs, "tenant_intervals", AsyncMock(return_value={A: 60, B: 90}))
    tenant_jobs.cadence.ticks["job"] = 60
    runs = {A: [], B: []}

    @fleet_job(cadence_spec=tenant_jobs.PING_CADENCE)
    async def job():
        runs[tenancy.current_tenant_id()].append(clock.t)

    with _tenants(A, B):
        for _ in range(7):
            await job()
            clock.t += 59.8  # ticks a little early
    gaps = [b - a for a, b in zip(runs[B], runs[B][1:])]
    assert len(runs[A]) == 7
    assert gaps and all(g >= 90 - tenant_jobs.CADENCE_JITTER for g in gaps), gaps


async def test_tick_is_the_fastest_tenant_within_the_floor(sf):
    async with sf() as s:
        with system_scope("test: settings", audit=False):
            s.add_all([TenantSetting(tenant_id=A, key="ping_interval", value="120"),
                       TenantSetting(tenant_id=B, key="ping_interval", value="3"),
                       Setting(key="ping_interval_min", value="15")])
            await s.commit()
    with _tenants(A, B):
        assert await tenant_jobs.tenant_intervals(tenant_jobs.PING_CADENCE, [A, B]) == {A: 120, B: 15}
        assert await tenant_jobs.job_tick(tenant_jobs.PING_CADENCE) == 15
    # One tenant without a row: the registry default.
    with _tenants(3):
        assert await tenant_jobs.job_tick(tenant_jobs.PING_CADENCE) == 60


async def test_reschedule_follows_the_fastest_tenant(sf, monkeypatch):
    import scheduler

    calls = []

    class FakeJob:
        def reschedule(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(scheduler.scheduler, "get_job", lambda job_id: FakeJob())
    async with sf() as s:
        with system_scope("test: settings", audit=False):
            s.add_all([TenantSetting(tenant_id=A, key="ping_interval", value="45"),
                       TenantSetting(tenant_id=B, key="proxmox_interval", value="300")])
            await s.commit()
    with _tenants(A, B):
        await scheduler.reschedule_cadence_jobs()
    assert calls == [{"trigger": "interval", "seconds": 45},
                     {"trigger": "interval", "seconds": 60}]
    assert tenant_jobs.cadence.ticks == {"ping_checks": 45, "integration_checks": 60}


# ── Limits: floors and retention caps ────────────────────────────────────────

def test_clamp_interval():
    spec = tenant_jobs.PING_CADENCE
    assert tenant_jobs.clamp_interval("5", 10, spec) == 10
    assert tenant_jobs.clamp_interval("7200", 10, spec) == 3600
    assert tenant_jobs.clamp_interval("abc", 10, spec) == 60
    assert tenant_jobs.clamp_interval("30", 10, spec) == 30


async def test_retention_is_capped_by_the_installation(db):
    from tests.conftest import put_settings

    await put_settings(db, integration_retention_days="60", incident_event_retention_days="0")
    assert await tenant_jobs.retention_days(db, "integration_retention_days", 7) == 60
    assert await tenant_jobs.retention_days(db, "incident_event_retention_days", 30) == 0
    await put_settings(db, integration_retention_days_max="14", incident_event_retention_days_max="90")
    assert await tenant_jobs.retention_days(db, "integration_retention_days", 7) == 14
    # "keep forever" becomes the cap
    assert await tenant_jobs.retention_days(db, "incident_event_retention_days", 30) == 90


def test_every_limit_key_is_declared():
    from services import settings_registry as reg

    for key in reg.RETENTION_KEYS:
        assert reg.lookup(key).is_tenant
        assert not reg.lookup(f"{key}_max").is_tenant
    for spec in (tenant_jobs.PING_CADENCE, tenant_jobs.INTEGRATION_CADENCE):
        assert reg.lookup(spec.key).is_tenant and not reg.lookup(spec.floor_key).is_tenant
    assert reg.lookup("ping_interval_min").default == "10"  # the old hard-coded minimum


async def test_settings_save_applies_the_floor_and_caps():
    from tests.conftest import setting_row
    from tests.test_routers.conftest import make_client

    async with make_client() as (client, sf):
        async with sf() as s:
            from tests.conftest import put_settings
            await put_settings(s, ping_interval_min="30", integration_retention_days_max="5")
        resp = await client.post("/settings/save", data={
            "site_name": "N", "ping_interval": "10", "proxmox_interval": "60",
            "ping_retention": "30", "proxmox_retention": "7", "anomaly_threshold": "2",
            "timezone": "UTC", "integration_retention": "30",
        })
        assert resp.status_code in (200, 303), resp.text
        async with sf() as s:
            assert (await setting_row(s, "ping_interval")).value == "30"
            assert (await setting_row(s, "integration_retention_days")).value == "5"


# ── Tenant-local schedules ───────────────────────────────────────────────────

def test_last_slot_weekly_in_a_zone():
    zrh = tenant_zone("Europe/Zurich")
    # Monday 2026-10-12, 07:30 UTC = 09:30 CEST: the 09:00 slot was at 07:00 UTC.
    assert last_slot(datetime(2026, 10, 12, 7, 30), zrh, 9, weekday=0) == datetime(2026, 10, 12, 7, 0)
    # Before the slot: last week's.
    assert last_slot(datetime(2026, 10, 12, 6, 30), zrh, 9, weekday=0) == datetime(2026, 10, 5, 7, 0)
    # Across the DST change (25 Oct 2026): winter time is UTC+1.
    assert last_slot(datetime(2026, 10, 26, 8, 30), zrh, 9, weekday=0) == datetime(2026, 10, 26, 8, 0)
    assert tenant_zone("Not/AZone").key == "UTC" and tenant_zone("").key == "UTC"


def test_slot_due_has_an_hour_of_grace_and_no_repeats():
    utc = tenant_zone("UTC")
    now = datetime(2026, 10, 12, 9, 20)  # Monday
    assert slot_due(now, utc, 9, weekday=0)
    assert not slot_due(datetime(2026, 10, 12, 10, 0, 1), utc, 9, weekday=0)  # grace over
    assert not slot_due(now, utc, 9, weekday=1)  # Tuesday's slot
    assert not slot_due(now, utc, 9, weekday=0, last_sent=datetime(2026, 10, 12, 9, 0, 5))
    assert slot_due(now, utc, 9, weekday=0, last_sent=datetime(2026, 10, 5, 9, 0, 5))


async def test_weekly_digest_fires_at_each_tenants_own_hour(sf, monkeypatch):
    """Monday 07:30 UTC: tenant B (07:00) is due, tenant A (09:00) is not —
    the old global cron used the first tenant's hour. Hours are UTC, also
    for a tenant whose time zone is Europe/Zurich (the UI says UTC)."""
    import scheduler

    class FakeDT(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 10, 12, 7, 30)

    monkeypatch.setattr(scheduler, "datetime", FakeDT)
    async with sf() as s:
        with system_scope("test: settings", audit=False):
            for tid, hour in ((A, "9"), (B, "7")):
                s.add_all([TenantSetting(tenant_id=tid, key="digest_enabled", value="1"),
                           TenantSetting(tenant_id=tid, key="digest_hour", value=hour),
                           TenantSetting(tenant_id=tid, key="timezone", value="Europe/Zurich")])
            await s.commit()
    due = {}
    for tid in (A, B):
        with tenant_scope(tid):
            async with sf() as s:
                due[tid] = await scheduler._digest_slot_due(s)
    assert due == {A: False, B: True}

    sent = []

    async def fake_build(db):
        sent.append(tenancy.current_tenant_id())
        return {}

    with _tenants(A, B), \
            patch("services.digest.build_weekly_digest", fake_build), \
            patch("services.digest.format_digest_html", lambda d: ""), \
            patch("services.digest.format_digest_text", lambda d: ""), \
            patch("notifications._send_email", new=AsyncMock()), \
            patch("notifications._log_notification", new=AsyncMock()), \
            patch("scheduler.AsyncSessionLocal", sf):
        async with sf() as s:
            with system_scope("test: smtp", audit=False):
                for tid in (A, B):
                    for k, v in (("smtp_host", "h"), ("smtp_user", "u"), ("smtp_password", "p"),
                                 ("smtp_to", "t")):
                        s.add(TenantSetting(tenant_id=tid, key=k, value=v))
                await s.commit()
        await scheduler.run_weekly_digest()
        await scheduler.run_weekly_digest()  # same slot: nothing new
    assert sent == [B]
    async with sf() as s:
        with system_scope("test: read", audit=False):
            rows = (await s.execute(select(TenantSetting.tenant_id).where(
                TenantSetting.key == "digest_last_sent"))).scalars().all()
    assert rows == [B]


@pytest.mark.parametrize("now, due", [
    (datetime(2026, 10, 12, 9, 0, 0, 300_000), True),   # the hourly tick at 09:00 UTC
    (datetime(2026, 10, 12, 9, 59, 0), True),           # late, within the old cron's grace
    (datetime(2026, 10, 12, 10, 0, 0, 200_000), False),  # next tick: no second digest
    (datetime(2026, 10, 12, 7, 0, 0, 200_000), False),   # 09:00 Zurich is not the slot
])
async def test_digest_hour_is_utc_like_the_old_cron(db, monkeypatch, now, due):
    """Production: tenant time zone Europe/Zurich, digest Monday 09 (UTC in
    the UI). The hourly tick fires at 09:00 UTC, the hour the old cron did —
    deploy day neither skips the digest nor sends it twice (the old cron at
    09:00 and a restart before 10:00: the 10:00 tick is past the grace)."""
    import scheduler
    from tests.conftest import put_settings

    await put_settings(db, timezone="Europe/Zurich", digest_day="0", digest_hour="9")

    class FakeDT(datetime):
        @classmethod
        def utcnow(cls):
            return now

    monkeypatch.setattr(scheduler, "datetime", FakeDT)
    assert await scheduler._digest_slot_due(db) is due


# ── Metrics ──────────────────────────────────────────────────────────────────

async def test_per_tenant_metrics_carry_the_job_name():
    from prometheus_client import REGISTRY

    @instrument_job("tenant_metric_probe")
    @per_tenant_job
    async def job():
        pass

    with _tenants(A, B):
        await job()
    assert tenant_jobs.stats.get("tenant_metric_probe", B).runs == 1
    assert REGISTRY.get_sample_value(
        "nodeglow_tenant_job_runs_total",
        {"job": "tenant_metric_probe", "tenant": "2", "status": "success"}) == 1.0


def test_tenant_label_cardinality_is_bounded(monkeypatch):
    monkeypatch.setattr(tenant_jobs, "TENANT_METRIC_LABELS", 2)
    st = tenant_jobs.TenantJobStats()
    assert [st.label_for(t) for t in (5, 6, 7, 5)] == ["5", "6", "other", "5"]


async def test_system_status_shows_the_tenants_job_counters():
    from tests.test_routers.conftest import make_client

    tenant_jobs.stats.record("correlation", 1, 0.25, None)
    tenant_jobs.stats.record("correlation", 2, 0.5, RuntimeError("CANARY-B secret host"))
    async with make_client() as (client, _sf):
        data = (await client.get("/api/system/status")).json()
    assert data["tenant_jobs"]["correlation"]["runs"] == 1
    assert "2" not in data["tenant_jobs"]  # job names only, own tenant
    # Operators see every tenant's counters, never another tenant's error text.
    assert data["tenant_jobs_by_tenant"]["2"]["correlation"]["failures"] == 1
    assert "CANARY-B" not in str(data["tenant_jobs_by_tenant"])


# ── Subnet scans land in the schedule's site ────────────────────────────────

async def test_auto_added_hosts_land_in_the_schedules_site(db):
    from routers.subnet_scanner import auto_add_hosts

    with system_scope("test: second site", audit=False):
        db.add(Site(id=5, tenant_id=1, name="Branch"))
        await db.commit()
    added, _ = await auto_add_hosts(db, [{"ip": "10.5.0.7", "alive": True}], site_id=5)
    assert added == 1
    host = (await db.execute(select(PingHost).where(PingHost.hostname == "10.5.0.7"))).scalar_one()
    assert host.site_id == 5
    added, _ = await auto_add_hosts(db, [{"ip": "10.5.0.8", "alive": True}])
    host = (await db.execute(select(PingHost).where(PingHost.hostname == "10.5.0.8"))).scalar_one()
    assert host.site_id == 1  # the tenant's default site

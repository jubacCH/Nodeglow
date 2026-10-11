"""Session-filter and job-context hardening (review of phases 1–2).

Each test reproduces a path that used to bypass the tenant boundary.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from models.log_template import LogTemplate
from models.ping import PingHost
from models.tenant import Site, Tenant
from services import tenancy
from services.tenancy import TenantContextError, per_tenant_job, system_scope, tenant_scope

A, B = 1, 2


@pytest.fixture
async def sf(db):
    with system_scope("test: tenant B", audit=False):
        db.add(Tenant(id=B, provider_id=1, name="B", slug="b", status="active"))
        await db.flush()
        db.add(Site(id=2, tenant_id=B, name="B"))
        db.add_all([LogTemplate(id=1, tenant_id=A, template_hash="h", template="a", noise_score=1),
                    LogTemplate(id=2, tenant_id=B, template_hash="h", template="b", noise_score=2)])
        db.add_all([PingHost(id=10, tenant_id=A, name="a", hostname="10.0.0.1", site_id=1),
                    PingHost(id=20, tenant_id=B, name="b", hostname="10.0.0.1", site_id=2)])
        await db.commit()
    return async_sessionmaker(db.bind, expire_on_commit=False)


async def _scores(sf):
    with system_scope("test: read back", audit=False):
        async with sf() as s:
            return dict((await s.execute(select(LogTemplate.id, LogTemplate.noise_score))).all())


# ── 1) scheduled jobs start without the adding request's context ────────────

async def test_scheduled_jobs_do_not_inherit_the_tenant_that_scheduled_them():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from scheduler import ContextFreeExecutor

    seen: list[int] = []

    @per_tenant_job
    async def job():
        seen.append(tenancy.current_tenant_id())

    sched = AsyncIOScheduler(executors={"default": ContextFreeExecutor()})
    with patch("services.tenancy.SingleTenancy.active_tenant_ids", new=AsyncMock(return_value=[A, B])):
        sched.start()
        try:
            with tenant_scope(B):  # e.g. a settings save rescheduling the job
                sched.add_job(job, "interval", seconds=3600, id="j",
                              next_run_time=datetime.now() + timedelta(milliseconds=50))
                sched.reschedule_job("j", trigger="date",
                                     run_date=datetime.now() + timedelta(milliseconds=50))
            for _ in range(100):
                if len(seen) >= 2:
                    break
                await asyncio.sleep(0.02)
        finally:
            sched.shutdown(wait=False)
    assert sorted(seen) == [A, B]


def test_the_app_scheduler_uses_the_context_free_executor():
    import scheduler

    assert isinstance(scheduler.scheduler._executors["default"], scheduler.ContextFreeExecutor)


def test_runtime_phpipam_job_declares_its_tenancy():
    from routers.settings.general import run_phpipam_sync

    assert run_phpipam_sync.__tenancy__ == "per_tenant"


# ── 2) ORM bulk UPDATE by primary key ───────────────────────────────────────

async def test_bulk_update_by_primary_key_is_refused_in_a_tenant(sf):
    async with sf() as s:
        with tenant_scope(A):
            with pytest.raises(TenantContextError, match="bulk UPDATE"):
                await s.execute(update(LogTemplate), [{"id": 2, "noise_score": 99}])
    assert (await _scores(sf))[2] == 2


async def test_template_updates_by_id_stay_in_the_tenant(sf):
    from services.log_intelligence import _update_templates_by_id

    async with sf() as s:
        with tenant_scope(A):
            # id 2 belongs to B: named, but never written.
            await _update_templates_by_id(s, [{"id": 1, "noise_score": 50},
                                              {"id": 2, "noise_score": 99, "trend_score": 1.0}])
            await s.commit()
    assert await _scores(sf) == {1: 50, 2: 2}


# ── 3) UPDATE ... SET tenant_id ─────────────────────────────────────────────

async def test_orm_update_cannot_move_rows_between_tenants(sf):
    async with sf() as s:
        with tenant_scope(A):
            with pytest.raises(TenantContextError, match="tenant_id"):
                await s.execute(update(PingHost).where(PingHost.id == 10).values(tenant_id=B))
            with pytest.raises(TenantContextError, match="tenant_id"):
                await s.execute(update(PingHost).where(PingHost.id == 10), {"tenant_id": B})
        with system_scope("test: a deliberate move"):
            await s.execute(update(LogTemplate).where(LogTemplate.id == 1).values(noise_score=3))


# ── 6) ORM INSERT with a foreign tenant / from_select ───────────────────────

async def test_orm_insert_rejects_foreign_tenant_and_from_select(sf):
    async with sf() as s:
        with tenant_scope(A):
            with pytest.raises(TenantContextError):
                await s.execute(insert(LogTemplate).values(tenant_id=B, template_hash="x", template="x"))
            with pytest.raises(TenantContextError):
                await s.execute(insert(LogTemplate).values(
                    [{"tenant_id": A, "template_hash": "y", "template": "y"},
                     {"tenant_id": B, "template_hash": "z", "template": "z"}]))
            with pytest.raises(TenantContextError):
                await s.execute(insert(LogTemplate), [{"tenant_id": B, "template_hash": "w", "template": "w"}])
            with pytest.raises(TenantContextError, match="FROM SELECT"):
                await s.execute(insert(LogTemplate).from_select(
                    ["tenant_id", "template_hash", "template"],
                    select(LogTemplate.tenant_id, LogTemplate.template_hash, LogTemplate.template)))
            # A missing tenant_id is filled with the context's.
            await s.execute(insert(LogTemplate).values(template_hash="filled", template="t"))
            await s.execute(insert(LogTemplate), [{"template_hash": "filled2", "template": "t"}])
            await s.commit()
    with system_scope("test", audit=False):
        async with sf() as s:
            rows = (await s.execute(select(LogTemplate.template_hash, LogTemplate.tenant_id)
                                    .where(LogTemplate.template_hash.like("filled%")))).all()
    assert sorted(tuple(r) for r in rows) == [("filled", A), ("filled2", A)]


# ── 7/8) flush checks the object's tenant, not only the context ─────────────

async def test_pending_row_is_stamped_when_added_not_when_flushed(sf):
    async with sf() as s:
        with tenant_scope(A):
            s.add(PingHost(name="made-in-a", hostname="x"))
        with tenant_scope(B):
            with pytest.raises(TenantContextError, match="carries tenant 1"):
                await s.flush()


async def test_rows_loaded_in_system_scope_cannot_be_changed_from_a_tenant(sf):
    async with sf() as s:
        with system_scope("test: load everything"):
            host_b = await s.get(PingHost, 20)
        with tenant_scope(A):
            host_b.name = "hijacked"
            with pytest.raises(TenantContextError, match="tenant 2"):
                await s.flush()
            await s.rollback()
        with system_scope("test: load everything"):
            host_b = await s.get(PingHost, 20)
        with tenant_scope(A):
            await s.delete(host_b)
            with pytest.raises(TenantContextError, match="tenant 2"):
                await s.flush()


async def test_move_guard_catches_an_unloaded_tenant_id(sf):
    async with sf() as s:
        with tenant_scope(A):
            host = await s.get(PingHost, 10)
            s.expire(host, ["tenant_id"])
            host.tenant_id = B
            with pytest.raises(TenantContextError, match="move"):
                await s.flush()


# ── 9) from_statement(text(...)) ────────────────────────────────────────────

async def test_from_statement_fails_closed_unless_marked(sf):
    async with sf() as s:
        with tenant_scope(A):
            with pytest.raises(TenantContextError, match="from_statement"):
                await s.execute(select(PingHost).from_statement(text("SELECT * FROM ping_hosts")))
            rows = (await s.execute(
                select(PingHost).from_statement(
                    text("SELECT * FROM ping_hosts WHERE tenant_id = :t")
                ).execution_options(nodeglow_tenant_filtered=True),
                {"t": A},
            )).scalars().all()
    assert [h.id for h in rows] == [10]


# ── 4) probe results only for assigned hosts of the probe's tenant ──────────

async def test_probe_results_only_land_for_assigned_hosts(sf):
    from models.agent import Agent
    from routers import agents as agents_router

    async with sf() as s:
        with tenant_scope(A):
            s.add(Agent(id=5, name="probe", hostname="p", token="tok-p", is_probe=True))
            await s.flush()
            s.add(PingHost(id=11, name="assigned", hostname="10.0.0.11", probe_id=5))
            await s.commit()

    @asynccontextmanager
    async def factory():
        async with sf() as sess:
            yield sess

    insert_mock = AsyncMock()
    results = [{"host_id": 11, "success": True, "detail": {"icmp": "ok"}},
               {"host_id": 10, "success": False, "detail": {"icmp": "x"}},   # A's, not assigned
               {"host_id": 20, "success": False, "detail": {"icmp": "x"}}]   # B's
    with patch.object(agents_router, "AsyncSessionLocal", factory), \
         patch("services.clickhouse_client.insert_ping_checks", insert_mock), \
         tenant_scope(A):
        await agents_router._record_probe_results(5, results)
    rows = insert_mock.await_args.args[0]
    assert [r["host_id"] for r in rows] == [11]
    with system_scope("test", audit=False):
        async with sf() as s:
            details = dict((await s.execute(select(PingHost.id, PingHost.check_detail))).all())
    assert details[11] and details[10] is None and details[20] is None


# ── 12) system status LATERAL query carries the tenant ──────────────────────

async def test_system_status_snapshot_query_has_the_tenant_predicate():
    from tests.test_routers.conftest import make_client

    seen: list = []
    from sqlalchemy.ext.asyncio import AsyncSession
    real = AsyncSession.execute

    async def spy(self, stmt, params=None, *a, **kw):
        if "LATERAL" in str(stmt):
            seen.append((str(stmt), params))
        return await real(self, stmt, params, *a, **kw)

    async with make_client() as (client, _sf):
        with patch.object(AsyncSession, "execute", spy):
            await client.get("/api/system/status")
    assert seen and "tenant_id = c.tenant_id" in seen[0][0] and seen[0][1] == {"tenant_id": A}

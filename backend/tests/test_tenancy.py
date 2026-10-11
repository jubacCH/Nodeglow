"""Tenant context and session filter: fail-closed behaviour and isolation.

Two tenants are seeded directly in the database (system scope, explicit
tenant ids). Every query in tenant A's context must return only A's rows —
for plain selects, counts, joins, subqueries, relationship loads and ORM bulk
UPDATE/DELETE — and anything tenant-owned without a context must raise.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, exists, func, select, text, union_all, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased, selectinload

import extensions
from models import (
    AlertRule, Agent, ApiKey, AuditLog, Base, Credential, DiscoveredPort, Incident,
    IncidentEvent, IntegrationConfig, LogTemplate, PingHost, PrecursorPattern, Setting,
    Site, Snapshot, Tenant, Provider, User, UserPreference,
)
from services import tenancy
from services.tenancy import (
    TenantContextError, per_tenant_job, system_job, system_scope, tenant_scope,
)

A, B = 1, 2


@pytest.fixture
async def sf():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        with system_scope("test: seed two tenants"):
            s.add(Provider(id=1, name="P", slug="p"))
            s.add_all([
                Tenant(id=A, provider_id=1, name="A", slug="a"),
                Tenant(id=B, provider_id=1, name="B", slug="b"),
            ])
            s.add_all([Site(id=1, tenant_id=A, name="A"), Site(id=2, tenant_id=B, name="B")])
            s.add(Setting(key="site_name", value="X"))
            s.add(User(id=1, username="u", password_hash="x", role="admin"))
            for tid, base in ((A, 100), (B, 200)):
                tag = "A" if tid == A else "CANARY-B"
                s.add(Agent(id=base, tenant_id=tid, name=f"{tag}-probe", hostname="srv01",
                            token=f"tok-{tid}", is_probe=True))
                s.add(PingHost(id=base, tenant_id=tid, name=f"{tag}-host", hostname="10.0.0.1",
                               probe_id=base))
                s.add(DiscoveredPort(tenant_id=tid, host_id=base, port=22))
                s.add(Incident(id=base, tenant_id=tid, rule="r", title=f"{tag}-incident"))
                s.add(IncidentEvent(tenant_id=tid, incident_id=base, event_type="created",
                                    summary=f"{tag}-event"))
                s.add(LogTemplate(id=base, tenant_id=tid, template_hash="same-hash",
                                  template=f"{tag}-tpl", example=f"{tag} example"))
                s.add(PrecursorPattern(tenant_id=tid, template_id=base, precedes_event="x"))
                s.add(IntegrationConfig(id=base, tenant_id=tid, type="proxmox",
                                        name=f"{tag}-pve", config_json="{}"))
                s.add(Snapshot(tenant_id=tid, entity_type="proxmox", entity_id=base))
                s.add(AlertRule(tenant_id=tid, name=f"{tag}-rule", source_type="ping",
                                field_path="x", operator="gt"))
                s.add(Credential(id=base, tenant_id=tid, name=f"{tag}-cred", type="snmp_v2c",
                                 data_json="x"))
                s.add(ApiKey(tenant_id=tid, name=f"{tag}-key", key_hash=f"h{tid}", prefix="p"))
                s.add(UserPreference(user_id=1, tenant_id=tid))
            s.add(AuditLog(action="installation-level", tenant_id=None))
            await s.commit()
    yield factory
    await engine.dispose()


# ── Fail closed ──────────────────────────────────────────────────────────────

async def test_select_without_context_raises(sf):
    async with sf() as s:
        with pytest.raises(TenantContextError, match="ping_hosts"):
            await s.execute(select(PingHost))


@pytest.mark.parametrize("stmt", [
    lambda: select(func.count()).select_from(PingHost),
    lambda: select(func.count(Incident.id)),
    lambda: select(Setting).where(Setting.value.in_(select(PingHost.name))),
    lambda: select(User).where(exists().where(Agent.name == User.username)),
    lambda: update(PingHost).values(enabled=False),
    lambda: delete(Snapshot),
])
async def test_any_statement_touching_a_tenant_table_without_context_raises(sf, stmt):
    async with sf() as s:
        with pytest.raises(TenantContextError):
            await s.execute(stmt())


async def test_get_without_context_raises(sf):
    async with sf() as s:
        with pytest.raises(TenantContextError):
            await s.get(PingHost, 100)


async def test_insert_without_context_raises(sf):
    async with sf() as s:
        s.add(PingHost(name="x", hostname="x"))
        with pytest.raises(TenantContextError, match="without tenant context"):
            await s.flush()


async def test_global_tables_need_no_context(sf):
    async with sf() as s:
        assert (await s.execute(select(Setting.value))).scalar_one() == "X"
        assert (await s.execute(select(User.username))).scalar_one() == "u"
        s.add(Setting(key="k", value="v"))
        await s.commit()


async def test_raw_sql_is_not_filtered(sf):
    """text() is out of scope for the ORM filter (RLS, phase 7) — documented gap."""
    async with sf() as s:
        rows = (await s.execute(text("SELECT count(*) FROM ping_hosts"))).scalar_one()
        assert rows == 2


# ── Isolation in tenant context ──────────────────────────────────────────────

MODELS = [PingHost, Agent, DiscoveredPort, Incident, IncidentEvent, LogTemplate,
          PrecursorPattern, IntegrationConfig, Snapshot, AlertRule, Credential, ApiKey,
          UserPreference, Site]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
async def test_tenant_a_sees_only_its_rows(sf, model):
    async with sf() as s:
        with tenant_scope(A):
            rows = (await s.execute(select(model))).scalars().all()
            count = (await s.execute(select(func.count()).select_from(model))).scalar_one()
    assert rows and all(r.tenant_id == A for r in rows)
    assert count == len(rows)


async def test_joins_subqueries_and_aliases_are_filtered(sf):
    async with sf() as s:
        with tenant_scope(A):
            joined = (await s.execute(
                select(PingHost.name, DiscoveredPort.port)
                .join(DiscoveredPort, DiscoveredPort.host_id == PingHost.id)
            )).all()
            assert [r.name for r in joined] == ["A-host"]

            # Join on a non-tenant column: B's host must not pair with A's port.
            cross = (await s.execute(
                select(PingHost.name).join(Agent, Agent.hostname == "srv01")
            )).scalars().all()
            assert cross == ["A-host"]

            sub = (await s.execute(
                select(Incident.title).where(
                    Incident.id.in_(select(IncidentEvent.incident_id))
                )
            )).scalars().all()
            assert sub == ["A-incident"]

            scalar = (await s.execute(
                select(select(func.count(PingHost.id)).scalar_subquery())
            )).scalar_one()
            assert scalar == 1

            exist = (await s.execute(
                select(func.count()).select_from(Incident).where(
                    exists().where(IncidentEvent.incident_id == Incident.id)
                )
            )).scalar_one()
            assert exist == 1

            h2 = aliased(PingHost)
            al = (await s.execute(select(h2.name))).scalars().all()
            assert al == ["A-host"]

            u = union_all(select(PingHost.name), select(Agent.name)).subquery()
            names = sorted((await s.execute(select(u.c.name))).scalars().all())
            assert names == ["A-host", "A-probe"]

            cte = select(LogTemplate.example).cte("t")
            examples = (await s.execute(select(cte.c.example))).scalars().all()
            assert examples == ["A example"]


async def test_get_of_other_tenants_row_returns_none(sf):
    async with sf() as s:
        with tenant_scope(A):
            assert await s.get(PingHost, 200) is None
            assert (await s.get(PingHost, 100)).name == "A-host"


async def test_relationship_loads_are_filtered(sf):
    async with sf() as s:
        with tenant_scope(A):
            incidents = (await s.execute(
                select(Incident).options(selectinload(Incident.events))
            )).scalars().all()
    assert [e.summary for i in incidents for e in i.events] == ["A-event"]


async def test_bulk_update_and_delete_touch_only_own_tenant(sf):
    async with sf() as s:
        with tenant_scope(A):
            await s.execute(update(PingHost).values(enabled=False))
            await s.execute(delete(Snapshot))
            await s.commit()
        with system_scope("test: verify"):
            hosts = {h.id: h.enabled for h in (await s.execute(select(PingHost))).scalars()}
            snaps = (await s.execute(select(Snapshot.tenant_id))).scalars().all()
    assert hosts == {100: False, 200: True}
    assert snaps == [B]


async def test_insert_stamps_tenant_and_default_site(sf):
    async with sf() as s:
        with tenant_scope(A):
            h = PingHost(name="new", hostname="n")
            s.add(h)
            await s.commit()
    assert h.tenant_id == A and h.site_id == 1


async def test_insert_for_another_tenant_raises(sf):
    async with sf() as s:
        with tenant_scope(A):
            s.add(PingHost(name="x", hostname="x", tenant_id=B))
            with pytest.raises(TenantContextError, match="carries tenant 2"):
                await s.flush()


async def test_moving_a_row_between_tenants_raises(sf):
    async with sf() as s:
        with tenant_scope(A):
            h = await s.get(PingHost, 100)
            h.tenant_id = B
            with pytest.raises(TenantContextError, match="cannot move"):
                await s.flush()


async def test_orm_insert_statement_without_context_raises(sf):
    from sqlalchemy.dialects.sqlite import insert

    async with sf() as s:
        with pytest.raises(TenantContextError, match="INSERT"):
            await s.execute(insert(LogTemplate).values(template_hash="h", template="t"))


# ── System scope ─────────────────────────────────────────────────────────────

async def test_system_scope_sees_every_tenant(sf):
    async with sf() as s:
        with system_scope("test"):
            assert (await s.execute(select(func.count()).select_from(PingHost))).scalar_one() == 2
            # Installation-level audit rows (tenant NULL) are visible only here.
            assert (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 1
        with tenant_scope(A):
            assert (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 0


async def test_system_scope_insert_needs_explicit_tenant(sf):
    async with sf() as s:
        with system_scope("test"):
            s.add(PingHost(name="x", hostname="x"))
            with pytest.raises(TenantContextError, match="explicit tenant_id"):
                await s.flush()
            await s.rollback()
            s.add(PingHost(name="x", hostname="x", tenant_id=B))
            s.add(AuditLog(action="installation-level"))  # nullable tenant: allowed
            await s.commit()


def test_system_scope_requires_a_reason():
    with pytest.raises(ValueError):
        with system_scope(""):
            pass


def test_scopes_nest_and_restore():
    assert tenancy.current() is None
    with tenant_scope(A):
        with system_scope("inner"):
            assert tenancy.in_system_scope()
        assert tenancy.current_tenant_id() == A
    assert tenancy.current() is None
    with pytest.raises(TenantContextError):
        tenancy.current_tenant_id()


async def test_bound_session_is_the_fallback_context(sf):
    async with sf() as s:
        tenancy.bind_session(s, B)
        names = (await s.execute(select(PingHost.name))).scalars().all()
        assert names == ["CANARY-B-host"]
        with tenant_scope(A):  # an explicit context wins over the binding
            names = (await s.execute(select(PingHost.name))).scalars().all()
        assert names == ["A-host"]


# ── Job helpers and backend ──────────────────────────────────────────────────

async def test_per_tenant_job_runs_once_per_tenant_in_its_scope(sf, monkeypatch):
    class TwoTenants(tenancy.SingleTenancy):
        async def active_tenant_ids(self):
            return [A, B]

    monkeypatch.setattr(extensions.registry, "tenancy_backend", TwoTenants())
    seen = []

    @per_tenant_job
    async def job():
        async with sf() as s:
            seen.append((tenancy.current_tenant_id(),
                         (await s.execute(select(PingHost.name))).scalars().all()))

    await job()
    assert seen == [(A, ["A-host"]), (B, ["CANARY-B-host"])]


async def test_per_tenant_job_isolates_failures(monkeypatch):
    class TwoTenants(tenancy.SingleTenancy):
        async def active_tenant_ids(self):
            return [A, B]

    monkeypatch.setattr(extensions.registry, "tenancy_backend", TwoTenants())
    ran = []

    @per_tenant_job
    async def job():
        ran.append(tenancy.current_tenant_id())
        if tenancy.current_tenant_id() == A:
            raise RuntimeError("tenant A broke")

    with pytest.raises(RuntimeError, match="tenant A broke"):
        await job()
    assert ran == [A, B]  # B still ran


async def test_system_job_runs_in_system_scope():
    @system_job("test job")
    async def job():
        return tenancy.current()

    ctx = await job()
    assert ctx.system and ctx.reason == "test job"
    assert tenancy.current() is None


async def test_single_tenancy_backend_defaults():
    b = tenancy.backend()
    assert isinstance(b, tenancy.SingleTenancy)
    assert await b.resolve_request_tenant(None, object()) == 1
    assert await b.resolve_request_tenant(None, None) is None
    assert await b.active_tenant_ids() == [1]
    assert b.can_create_tenant() is False


def test_tenancy_backend_is_registrable():
    reg = extensions.Registry()
    custom = tenancy.SingleTenancy()
    reg.set_tenancy_backend(custom)
    assert reg.tenancy_backend is custom
    with pytest.raises(RuntimeError):
        reg.set_tenancy_backend(custom)


async def test_ensure_default_tenant_is_idempotent():
    from models import ensure_default_tenant

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Setting(key="site_name", value="ACME"))
        await s.commit()
        await ensure_default_tenant(s)
        await ensure_default_tenant(s)
        with system_scope("test"):
            tenants = (await s.execute(select(Tenant.id, Tenant.name))).all()
            sites = (await s.execute(select(Site.id, Site.tenant_id))).all()
    await engine.dispose()
    assert [tuple(t) for t in tenants] == [(1, "ACME")]
    assert [tuple(x) for x in sites] == [(1, 1)]

"""Purge of a deleted tenant (owner decision 6): every PostgreSQL row of it
(all tenant-owned tables, children first), its memberships and the users of
only this tenant, its ClickHouse rows; resumable, audited, crypto-shred hook.

Seed: the isolation suite's — one row of every tenant-owned table in tenant
A (1) and B (2). B is purged; A must be byte-for-byte unchanged. Runs on
SQLite here and on PostgreSQL as ``nodeglow_app`` with RLS on in the router
PostgreSQL mode.
"""
from __future__ import annotations

import re
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from models.audit import AuditLog
from models.settings import Session, User
from models.tenant import Tenant, TenantMembership
from services import clickhouse_client as ch
from services.tenancy import system_scope, tenant_tables
from tests.test_routers.test_tenant_isolation import (
    A, B, BASE, FakeClickHouse, _REAL_CH_QUERY, _REAL_CH_SCALAR, _seed, _session, _tenant_rows,
    ch_call_is_scoped,
)
from tests.test_routers.conftest import make_client


class PurgeClickHouse(FakeClickHouse):
    """Counts answer 3 rows per tenant table until a DELETE for that table
    and tenant ran; records everything (scoping is checked per call)."""

    def __init__(self, fail_deletes: bool = False):
        super().__init__()
        self.deleted: set[tuple[str, int]] = set()
        self.fail_deletes = fail_deletes
        self.responders.append((r"SELECT count\(\) FROM (\w+) WHERE", self._count))

    def _count(self, tid, params):
        return []  # replaced per call in query()

    async def query(self, sql, parameters=None, settings=None, **kw):
        m = re.search(r"SELECT count\(\) FROM (\w+) WHERE", sql)
        if m:
            await super().query(sql, parameters, settings, **kw)
            tid = int((parameters or {}).get(ch.TENANT_PARAM, 0))
            n = 0 if (m.group(1), tid) in self.deleted else 3
            from tests.test_routers.test_tenant_isolation import _Result

            return _Result([{"c": n}])
        return await super().query(sql, parameters, settings, **kw)

    async def command(self, sql, parameters=None, settings=None, **kw):
        await super().command(sql, parameters, settings, **kw)
        m = re.match(r"DELETE FROM (\w+) WHERE", sql)
        if m:
            if self.fail_deletes:
                raise ConnectionError("ClickHouse is down")
            self.deleted.add((m.group(1), int((parameters or {}).get(ch.TENANT_PARAM, 0))))


@contextmanager
def clickhouse(fake):
    async def get_client():
        return fake

    with patch("services.clickhouse_client.get_client", new=get_client), \
            patch("services.clickhouse_client.query", new=_REAL_CH_QUERY), \
            patch("services.clickhouse_client.query_scalar", new=_REAL_CH_SCALAR):
        yield fake


@pytest.fixture
async def env():
    from services import shared_state

    shared_state.reset()
    async with make_client(fake_user=None) as (client, sf):
        await _seed(sf)
        async with sf() as s:
            # A user of A and B (stays), B's own user (BASE[B], goes), sessions.
            shared = User(username="shared", password_hash="x", role="admin", auth_source="local")
            s.add(shared)
            await s.flush()
            s.add_all([TenantMembership(user_id=shared.id, tenant_id=A, role="readonly"),
                       TenantMembership(user_id=shared.id, tenant_id=B, role="admin")])
            await s.commit()
            shared_id = shared.id
        await _session(sf, BASE[B], B)
        await _session(sf, shared_id, B)
        async with sf() as s:
            t = await s.get(Tenant, B)
            t.status, t.delete_after = "deleting", datetime.utcnow() - timedelta(minutes=1)
            await s.commit()
        yield client, sf, shared_id
    shared_state.reset()


async def _count_tenant_rows(sf, tid: int) -> dict[str, int]:
    out = {}
    async with sf() as s:
        with system_scope("test: count", audit=False):
            for t in tenant_tables():
                n = len((await s.execute(select(t.c.tenant_id).where(t.c.tenant_id == tid))).all())
                if n:
                    out[t.name] = n
    return out


async def test_purge_removes_every_row_of_the_tenant_and_nothing_else(env):
    from nodeglow_ee.tenancy import purge

    _client, sf, shared_id = env
    before_a = await _tenant_rows(sf, A)
    assert len(await _count_tenant_rows(sf, B)) == len(tenant_tables())  # seeded everywhere
    with clickhouse(PurgeClickHouse()) as fake:
        report = await purge.purge_tenant(B)

    assert await _count_tenant_rows(sf, B) == {}
    after_a = await _tenant_rows(sf, A)
    # A's rows unchanged; its users / memberships / sessions too, except the
    # shared user's membership of B (gone with B) — A's own entries stay.
    for table in before_a:
        if table in ("users", "tenant_memberships", "sessions"):
            continue
        assert after_a[table] == before_a[table], table
    async with sf() as s:
        assert await s.get(Tenant, B) is None
        assert await s.get(User, BASE[B]) is None           # B's own user: purged
        assert await s.get(User, shared_id) is not None      # also in A: kept
        assert await s.get(User, BASE[A]) is not None
        members = (await s.execute(select(TenantMembership.tenant_id).where(
            TenantMembership.user_id == shared_id))).scalars().all()
        assert members == [A]
        sessions = (await s.execute(select(Session.user_id, Session.active_tenant_id))).all()
        assert all(uid != BASE[B] for uid, _ in sessions)
        assert all(tid != B for _, tid in sessions)
        with system_scope("test", audit=False):
            [row] = (await s.execute(select(AuditLog).where(AuditLog.action == "tenant.purged"))).scalars().all()
    assert row.tenant_id is None and row.target_id == B
    assert report["users_deleted"] == 1 and report["crypto_shred"] == "unavailable"
    assert all(report["postgres"][t.name] >= 1 for t in tenant_tables())
    # ClickHouse: one scoped count and one DELETE per tenant table, for B.
    assert fake.deleted == {(t, B) for t in ch.TENANT_TABLES}
    assert report["clickhouse"] == {t: 3 for t in ch.TENANT_TABLES}
    for call in fake.calls:
        if call.kind == "query" and "count()" in call.sql:
            assert ch_call_is_scoped(call, B), call.sql
        if call.kind == "command":
            assert call.parameters.get(ch.TENANT_PARAM) == B and ch.TENANT_FILTER in call.sql


async def test_purge_is_refused_unless_due(env):
    from nodeglow_ee.tenancy import purge

    _client, sf, _ = env
    async with sf() as s:
        (await s.get(Tenant, B)).delete_after = datetime.utcnow() + timedelta(days=1)
        await s.commit()
    with pytest.raises(purge.PurgeRefused):
        await purge.purge_tenant(B)
    with pytest.raises(purge.PurgeRefused):
        await purge.purge_tenant(A)
    with pytest.raises(purge.PurgeRefused):
        await purge.purge_tenant(77)
    async with sf() as s:
        t = await s.get(Tenant, B)
        t.status, t.delete_after = "suspended", None
        await s.commit()
    with pytest.raises(purge.PurgeRefused):
        await purge.purge_tenant(B)
    assert len(await _count_tenant_rows(sf, B)) == len(tenant_tables())


async def test_purge_resumes_after_a_failure(env):
    from nodeglow_ee.tenancy import purge

    _client, sf, _ = env
    with clickhouse(PurgeClickHouse(fail_deletes=True)):
        with pytest.raises(ConnectionError):
            await purge.purge_tenant(B)
    async with sf() as s:
        t = await s.get(Tenant, B)
        assert t is not None and t.purge_started_at is not None and '"runs": 1' in t.purge_state
    # ClickHouse first: PostgreSQL is untouched until it has succeeded.
    assert len(await _count_tenant_rows(sf, B)) == len(tenant_tables())
    with clickhouse(PurgeClickHouse()):
        report = await purge.purge_tenant(B)
    assert report["runs"] == 2
    assert await _count_tenant_rows(sf, B) == {}


async def test_crypto_shred_hook_runs_first(env, monkeypatch):
    from nodeglow_ee.tenancy import purge

    _client, sf, _ = env
    calls = []

    async def destroy_tenant_key(db, tenant_id):
        calls.append((tenant_id, len(await _count_tenant_rows(sf, tenant_id))))

    module = types.ModuleType("services.tenant_keys")
    module.destroy_tenant_key = destroy_tenant_key
    monkeypatch.setitem(sys.modules, "services.tenant_keys", module)
    import services

    monkeypatch.setattr(services, "tenant_keys", module, raising=False)
    with clickhouse(PurgeClickHouse()):
        report = await purge.purge_tenant(B)
    assert calls == [(B, len(tenant_tables()))]  # before any row was deleted
    assert report["crypto_shred"] == "destroyed"


async def test_the_scheduled_job_purges_due_tenants_only(env):
    from nodeglow_ee.tenancy import jobs

    _client, sf, _ = env
    async with sf() as s:
        with system_scope("test", audit=False):
            s.add(Tenant(id=3, provider_id=1, name="later", slug="later", status="deleting",
                         delete_after=datetime.utcnow() + timedelta(days=3)))
            await s.commit()
    with clickhouse(PurgeClickHouse()):
        assert await jobs.run_tenant_purges() == {B: "purged"}
    async with sf() as s:
        assert await s.get(Tenant, B) is None and await s.get(Tenant, 3) is not None


async def test_a_purge_survives_the_schedule_through_the_api(env):
    """End to end: the operator schedules the deletion, 30 days pass, the job purges."""
    from nodeglow_ee.tenancy import jobs

    client, sf, _ = env
    async with sf() as s:
        t = await s.get(Tenant, B)
        t.status, t.delete_after = "active", None
        op = User(username="operator", password_hash="x", role="admin", auth_source="local",
                  is_operator=True)
        s.add(op)
        await s.flush()
        s.add(TenantMembership(user_id=op.id, tenant_id=A, role="admin"))
        await s.commit()
        op_id, name = op.id, t.name
    import os

    os.environ["NODEGLOW_TENANTS_WITHOUT_RLS"] = "1"
    try:
        client.cookies.set("nodeglow_session", await _session(sf, op_id, A))
        r = await client.post(f"/api/tenants/{B}/delete", json={"confirm": name})
        assert r.status_code == 200, r.text
    finally:
        os.environ.pop("NODEGLOW_TENANTS_WITHOUT_RLS", None)
    with clickhouse(PurgeClickHouse()):
        assert await jobs.run_tenant_purges() == {}  # not due yet
        async with sf() as s:
            (await s.get(Tenant, B)).delete_after = datetime.utcnow() - timedelta(seconds=1)
            await s.commit()
        assert await jobs.run_tenant_purges() == {B: "purged"}
    assert await _count_tenant_rows(sf, B) == {}
    r = await client.get("/api/tenants")
    assert [t["id"] for t in r.json()["tenants"]] == [A]

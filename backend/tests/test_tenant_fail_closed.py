"""Fail-closed, enumerated (multi-tenancy phase 8, test strategy 4).

Without a tenant (and without an explicit system scope) nothing tenant-owned
can be read or written: every tenant-owned model, every tenant settings key,
and — from phase 6 — every ClickHouse query. Enumerated, so a new table or
key is covered without anyone writing a test for it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from models.base import Base
from models.tenant import TenantScoped
from services import settings_registry as reg
from services.tenancy import TenantContextError, tenant_tables

TENANT_MODELS = sorted(
    (m.class_ for m in Base.registry.mappers if issubclass(m.class_, TenantScoped)),
    key=lambda c: c.__tablename__,
)


def test_the_enumeration_sees_every_tenant_table():
    assert {m.__tablename__ for m in TENANT_MODELS} == {t.name for t in tenant_tables()}
    assert len(TENANT_MODELS) >= 29


@pytest.fixture
async def plain(db):
    """A session without tenant binding (like a job's or a route's)."""
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as s:
        yield s


@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__tablename__)
async def test_reading_without_a_tenant_raises(plain, model):
    with pytest.raises(TenantContextError):
        await plain.execute(select(func.count()).select_from(model))


@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__tablename__)
async def test_writing_without_a_tenant_raises(plain, model):
    obj = model.__new__(model)
    model.__init__(obj)
    plain.add(obj)
    with pytest.raises(TenantContextError):
        await plain.flush()
    await plain.rollback()


@pytest.mark.parametrize("key", sorted(reg.tenant_keys()))
async def test_tenant_setting_without_a_tenant_raises(plain, key):
    from models.settings import get_setting

    with pytest.raises(TenantContextError):
        await get_setting(plain, key)


async def test_clickhouse_query_without_a_tenant_raises():
    from unittest.mock import AsyncMock, patch

    from services import clickhouse_client as ch
    from tests.test_routers.test_tenant_isolation import FakeClickHouse

    with patch.object(ch, "get_client", AsyncMock(return_value=FakeClickHouse())):
        with pytest.raises(TenantContextError):
            await ch.query("SELECT count() FROM ping_checks")

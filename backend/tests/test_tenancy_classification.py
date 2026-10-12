"""Static classification of every table: tenant-owned or global, nothing else.

Like ``PUBLIC_ROUTES`` for the route-auth test: a new table cannot be
forgotten. It is either a ``TenantScoped`` model (with ``tenant_id``, an index
leading with it, composite references to other tenant-owned tables and only
per-tenant uniqueness) or listed in ``models.tenant.GLOBAL_TABLES`` with a
justification.
"""
from __future__ import annotations

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

import models  # noqa: F401 — registers every model
from models.base import Base
from models.tenant import GLOBAL_TABLES, GLOBAL_UNIQUE_COLUMNS, TenantScoped


def _mapped_tables() -> dict[str, type]:
    return {m.local_table.name: m.class_ for m in Base.registry.mappers}


TENANT_TABLES = sorted(
    name for name, cls in _mapped_tables().items() if issubclass(cls, TenantScoped)
)


def test_every_table_is_classified():
    mapped = _mapped_tables()
    unclassified = [
        name for name in Base.metadata.tables
        if name not in GLOBAL_TABLES and not issubclass(mapped.get(name, object), TenantScoped)
    ]
    assert not unclassified, (
        f"Tables neither TenantScoped nor in GLOBAL_TABLES: {unclassified}. "
        "Give the model the TenantScoped mixin, or add it to models.tenant.GLOBAL_TABLES "
        "with the reason it is not tenant-owned."
    )


def test_no_table_is_both():
    both = sorted(set(GLOBAL_TABLES) & set(TENANT_TABLES))
    assert not both, f"Classified as global and tenant-owned at once: {both}"


def test_global_list_has_no_stale_entries():
    stale = sorted(set(GLOBAL_TABLES) - set(Base.metadata.tables))
    assert not stale, f"GLOBAL_TABLES lists tables that no longer exist: {stale}"
    assert all(reason.strip() for reason in GLOBAL_TABLES.values())


def test_expected_counts():
    # 31 tenant-owned (support_grants since 045, tenant_keys since 046), 9
    # global. Changing these numbers is a deliberate act.
    assert (len(TENANT_TABLES), len(GLOBAL_TABLES)) == (31, 9), TENANT_TABLES


@pytest.mark.parametrize("name", TENANT_TABLES)
def test_tenant_table_shape(name):
    table = Base.metadata.tables[name]
    cls = _mapped_tables()[name]
    col = table.c.get("tenant_id")
    assert col is not None, f"{name}: TenantScoped without tenant_id"
    assert col.nullable == bool(cls.__tenant_nullable__), f"{name}: tenant_id nullability"
    assert col.server_default is None, f"{name}: tenant_id must not have a server default"
    assert any(fk.column.table.name == "tenants" for fk in col.foreign_keys), \
        f"{name}: tenant_id must reference tenants.id"

    leading = []
    if table.primary_key.columns and "tenant_id" in table.primary_key.columns.keys():
        leading.append("pk")
    for ix in table.indexes:
        exprs = list(ix.expressions)
        if exprs and getattr(exprs[0], "name", None) == "tenant_id":
            leading.append(ix.name)
    for c in table.constraints:
        if isinstance(c, UniqueConstraint) and list(c.columns.keys())[:1] == ["tenant_id"]:
            leading.append(c.name)
    assert leading, f"{name}: no index leads with tenant_id"


@pytest.mark.parametrize("name", TENANT_TABLES)
def test_references_between_tenant_tables_are_composite(name):
    table = Base.metadata.tables[name]
    for fkc in table.constraints:
        if not isinstance(fkc, ForeignKeyConstraint):
            continue
        target = fkc.referred_table.name
        if target not in TENANT_TABLES:
            continue
        cols = list(fkc.columns.keys())
        assert cols[0] == "tenant_id" and len(cols) == 2, (
            f"{name} -> {target} via {cols}: a reference to a tenant-owned table must be "
            "the composite (tenant_id, col) -> (tenant_id, id) — use models.tenant.tenant_fk"
        )
        assert [e.column.name for e in fkc.elements] == ["tenant_id", "id"]


@pytest.mark.parametrize("name", TENANT_TABLES)
def test_uniqueness_is_per_tenant(name):
    table = Base.metadata.tables[name]
    for col in table.columns:
        if col.unique:
            assert (name, col.name) in GLOBAL_UNIQUE_COLUMNS, (
                f"{name}.{col.name} is unique across the installation; make it "
                "(tenant_id, ...) or justify it in GLOBAL_UNIQUE_COLUMNS"
            )
    for ix in table.indexes:
        if not ix.unique:
            continue
        exprs = list(ix.expressions)
        first = getattr(exprs[0], "name", None)
        if first == "tenant_id":
            continue
        single = len(exprs) == 1 and (name, first) in GLOBAL_UNIQUE_COLUMNS
        assert single, f"{name}: unique index {ix.name} does not lead with tenant_id"
    for c in table.constraints:
        if isinstance(c, UniqueConstraint):
            keys = list(c.columns.keys())
            assert keys[0] == "tenant_id" or (
                len(keys) == 1 and (name, keys[0]) in GLOBAL_UNIQUE_COLUMNS
            ), f"{name}: unique constraint {c.name} on {keys} is not per tenant"

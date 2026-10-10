"""Tenancy schema: providers, tenants, sites, memberships, tenant settings.

See ``docs/specs/2026-10-10-multi-tenancy-design.md``. The core always has
exactly one provider (id 1), one tenant (id 1) and one site (id 1); they are
created by migration 039 and, on a fresh install, by ``ensure_default_tenant``.

``TenantScoped`` marks a model whose rows belong to a tenant. The session
filter in ``services.tenancy`` adds ``tenant_id = <current tenant>`` to every
ORM statement against such a model and stamps ``tenant_id`` on new rows; the
classification test (``tests/test_tenancy_classification.py``) requires every
table to be either tenant-scoped or listed in ``GLOBAL_TABLES``.
"""
from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    Column, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text,
    Boolean, UniqueConstraint, select,
)
from sqlalchemy.orm import declared_attr

from models.base import Base

DEFAULT_PROVIDER_ID = 1
DEFAULT_TENANT_ID = 1
DEFAULT_SITE_ID = 1

# Every table that is NOT tenant-owned, with the reason. A new table must be
# either a TenantScoped model or listed here (tests/test_tenancy_classification.py).
GLOBAL_TABLES: dict[str, str] = {
    "settings": "installation settings; tenant keys move to tenant_settings (phase 3)",
    "users": "identities are global so one person can be a member of several tenants",
    "sessions": "per user; the active tenant is resolved per request (phase 4)",
    "snmp_mibs": "MIB catalogue shared by every tenant; uploads become operator-only",
    "snmp_oids": "parsed from snmp_mibs, shared catalogue",
    "providers": "tenancy root: providers group tenants",
    "tenants": "the isolation boundary itself",
    "tenant_memberships": "maps users to tenants; read to resolve the tenant",
    "provider_memberships": "maps users to providers (ee); read before a tenant is known",
}

# Unique constraints on tenant-owned tables that stay installation-wide on
# purpose: looked up before the tenant is known, the row found names it — or
# keyed by a globally unique id.
GLOBAL_UNIQUE_COLUMNS: dict[tuple[str, str], str] = {
    ("agents", "token"): "agent auth token, resolves the tenant",
    ("api_keys", "key_hash"): "API key, resolves the tenant",
    ("agent_install_tokens", "token_hash"): "install token, resolves the tenant",
    ("snmp_host_configs", "host_id"): "host ids are unique across the installation",
}


class TenantScoped:
    """Mixin: the row belongs to exactly one tenant.

    ``tenant_id`` is NOT NULL with no server default — a new row gets its
    tenant from the tenant context, never by accident. Class attributes:

    * ``__tenant_nullable__`` — ``tenant_id`` may be NULL (``audit_logs``:
      NULL is an installation-level entry). Such rows are invisible in every
      tenant context; only ``system_scope()`` sees them.
    * ``__tenant_index__`` — create the plain ``ix_<table>_tenant_id`` index.
      Off where another index already leads with ``tenant_id`` (a
      ``UNIQUE (tenant_id, id)`` or a tenant-prefixed composite).
    """

    __tenant_nullable__: ClassVar[bool] = False
    __tenant_index__: ClassVar[bool] = True

    @declared_attr
    def tenant_id(cls):  # noqa: N805 — declared_attr receives the class
        # Also evaluated on the bare mixin (the tenant filter's criteria lambda
        # is analysed against TenantScoped itself), which has no table.
        table = getattr(cls, "__tablename__", None)
        return Column(
            Integer,
            ForeignKey("tenants.id", name=f"fk_{table}_tenant" if table else None),
            nullable=cls.__tenant_nullable__,
            index=cls.__tenant_index__,
        )


def tenant_parent_unique(table: str) -> UniqueConstraint:
    """``UNIQUE (tenant_id, id)`` — what composite child FKs reference."""
    return UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id")


def tenant_fk(
    columns: str, parent: str, *, name: str, ondelete: str | None = None,
) -> ForeignKeyConstraint:
    """Composite FK ``(tenant_id, <columns>) -> <parent>(tenant_id, id)``.

    A child can then only point at a parent of its own tenant, whatever the
    application does. ``ondelete="SET NULL"`` is rendered as
    ``SET NULL (<column>)`` so only the reference is cleared, not ``tenant_id``
    (PostgreSQL 15+); SQLite cannot express that and does not enforce foreign
    keys in this code base anyway, so such a constraint is PostgreSQL-only DDL.
    """
    fk = ForeignKeyConstraint(
        ["tenant_id", columns],
        [f"{parent}.tenant_id", f"{parent}.id"],
        name=name,
        ondelete=f"SET NULL ({columns})" if ondelete == "SET NULL" else ondelete,
    )
    if ondelete == "SET NULL":
        fk.ddl_if(dialect="postgresql")
    return fk


class Provider(Base):
    """An MSP — an access grouping, not an isolation boundary. One on-prem."""

    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    slug = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Tenant(Base):
    """A customer — THE isolation boundary."""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(
        Integer, ForeignKey("providers.id", name="fk_tenants_provider"),
        nullable=False, index=True,
    )
    name = Column(String(128), nullable=False)
    slug = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active")  # active | suspended | deleting
    plan = Column(String(32), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "slug", name="uq_tenants_provider_slug"),
    )


class Site(TenantScoped, Base):
    """A network location inside a tenant (where an IP becomes unambiguous)."""

    __tablename__ = "sites"
    __tenant_index__ = False  # covered by uq_sites_tenant_id_id

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (tenant_parent_unique("sites"),)


class TenantMembership(Base):
    """A user's role in one tenant (admin | editor | readonly).

    Written by migration 039 from ``users.role``; ``users.role`` stays the
    source of truth until the auth binding (phase 4) switches over.
    """

    __tablename__ = "tenant_memberships"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="fk_tenant_memberships_user"),
        primary_key=True,
    )
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE", name="fk_tenant_memberships_tenant"),
        primary_key=True, index=True,
    )
    role = Column(String(16), nullable=False, default="readonly")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_memberships_creator"),
        nullable=True,
    )


class ProviderMembership(Base):
    """A user's role across a provider's tenants (msp_admin | msp_operator | msp_readonly).

    Schema only: provider roles are an ``ee`` feature and nothing in the core
    reads this table.
    """

    __tablename__ = "provider_memberships"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="fk_provider_memberships_user"),
        primary_key=True,
    )
    provider_id = Column(
        Integer,
        ForeignKey("providers.id", ondelete="CASCADE", name="fk_provider_memberships_provider"),
        primary_key=True, index=True,
    )
    role = Column(String(16), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TenantSetting(TenantScoped, Base):
    """Tenant-scoped settings. Empty until the settings split (phase 3)."""

    __tablename__ = "tenant_settings"

    # Overrides the mixin's column: part of the primary key, which also covers
    # every tenant_id lookup.
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", name="fk_tenant_settings_tenant"), primary_key=True,
    )
    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    encrypted = Column(Boolean, nullable=False, default=False)


async def ensure_default_tenant(db, *, name: str | None = None) -> None:
    """Create provider 1, tenant 1 and site 1 if they do not exist yet.

    The migration chain does this in 039; a fresh install builds its schema
    with ``create_all()`` instead and needs the same rows, because every
    tenant-owned row references tenant 1. Idempotent.
    """
    if True:
        if await db.get(Provider, DEFAULT_PROVIDER_ID) is None:
            db.add(Provider(id=DEFAULT_PROVIDER_ID, name="Default", slug="default"))
            await db.flush()
        if await db.get(Tenant, DEFAULT_TENANT_ID) is None:
            if name is None:
                from models.settings import Setting

                row = (await db.execute(
                    select(Setting.value).where(Setting.key == "site_name")
                )).scalar_one_or_none()
                name = row or "Default"
            db.add(Tenant(
                id=DEFAULT_TENANT_ID, provider_id=DEFAULT_PROVIDER_ID,
                name=name[:128], slug="default", status="active",
            ))
            await db.flush()
        if await db.get(Site, DEFAULT_SITE_ID) is None:
            db.add(Site(id=DEFAULT_SITE_ID, tenant_id=DEFAULT_TENANT_ID, name="Default"))
            await db.flush()
        if db.get_bind().dialect.name == "postgresql":
            # The rows above were inserted with explicit ids, which does not
            # advance the serial sequences: the next tenant would collide on 1.
            from sqlalchemy import text

            for table in ("providers", "tenants", "sites"):
                await db.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"GREATEST((SELECT MAX(id) FROM {table}), 1))"
                ))
        await db.commit()

"""Multi-tenancy, step 1: providers, tenants, sites, memberships, tenant settings.

Creates the tenancy tables and the rows every existing installation maps onto:
provider 1, tenant 1 (named after the ``site_name`` setting) and site 1
("Default"). Every existing user becomes a member of tenant 1 with the role
from ``users.role``; that column stays the source of truth until the auth
binding switches over, and is kept for one release after that for rollback.

``tenant_settings`` is created empty — moving keys out of ``settings`` is the
settings split, a later step. Nothing here changes behaviour.

See docs/specs/2026-10-10-multi-tenancy-design.md ("Migration").

Revision ID: 039
Revises: 038
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("providers"):
        op.create_table(
            "providers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("slug", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if not _has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider_id", sa.Integer(),
                      sa.ForeignKey("providers.id", name="fk_tenants_provider"),
                      nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("slug", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("plan", sa.String(32), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider_id", "slug", name="uq_tenants_provider_slug"),
        )
        op.create_index("ix_tenants_provider_id", "tenants", ["provider_id"])
    if not _has_table("sites"):
        op.create_table(
            "sites",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("tenant_id", sa.Integer(),
                      sa.ForeignKey("tenants.id", name="fk_sites_tenant"), nullable=False),
            sa.UniqueConstraint("tenant_id", "id", name="uq_sites_tenant_id_id"),
        )
    if not _has_table("tenant_memberships"):
        op.create_table(
            "tenant_memberships",
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE",
                                    name="fk_tenant_memberships_user"),
                      primary_key=True),
            sa.Column("tenant_id", sa.Integer(),
                      sa.ForeignKey("tenants.id", ondelete="CASCADE",
                                    name="fk_tenant_memberships_tenant"),
                      primary_key=True),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL",
                                    name="fk_tenant_memberships_creator"),
                      nullable=True),
        )
        op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    if not _has_table("provider_memberships"):
        op.create_table(
            "provider_memberships",
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE",
                                    name="fk_provider_memberships_user"),
                      primary_key=True),
            sa.Column("provider_id", sa.Integer(),
                      sa.ForeignKey("providers.id", ondelete="CASCADE",
                                    name="fk_provider_memberships_provider"),
                      primary_key=True),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_provider_memberships_provider_id", "provider_memberships", ["provider_id"]
        )
    if not _has_table("tenant_settings"):
        op.create_table(
            "tenant_settings",
            sa.Column("tenant_id", sa.Integer(),
                      sa.ForeignKey("tenants.id", name="fk_tenant_settings_tenant"),
                      primary_key=True),
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("encrypted", sa.Boolean(), nullable=False),
        )

    _seed_defaults()


def _seed_defaults() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()

    if bind.execute(sa.text("SELECT 1 FROM providers WHERE id = 1")).first() is None:
        bind.execute(
            sa.text("INSERT INTO providers (id, name, slug, created_at) "
                    "VALUES (1, 'Default', 'default', :now)"),
            {"now": now},
        )
    if bind.execute(sa.text("SELECT 1 FROM tenants WHERE id = 1")).first() is None:
        name = bind.execute(
            sa.text("SELECT value FROM settings WHERE key = 'site_name'")
        ).scalar()
        bind.execute(
            sa.text("INSERT INTO tenants (id, provider_id, name, slug, status, created_at) "
                    "VALUES (1, 1, :name, 'default', 'active', :now)"),
            {"name": (name or "Default")[:128], "now": now},
        )
    if bind.execute(sa.text("SELECT 1 FROM sites WHERE id = 1")).first() is None:
        bind.execute(
            sa.text("INSERT INTO sites (id, tenant_id, name, created_at) "
                    "VALUES (1, 1, 'Default', :now)"),
            {"now": now},
        )
    if bind.dialect.name == "postgresql":
        # Explicit ids do not advance the serial sequences.
        for table in ("providers", "tenants", "sites"):
            bind.execute(sa.text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT MAX(id) FROM {table}), 1))"
            ))

    # Every existing user becomes a member of the one tenant, with today's role.
    bind.execute(
        sa.text(
            "INSERT INTO tenant_memberships (user_id, tenant_id, role, created_at) "
            "SELECT u.id, 1, COALESCE(NULLIF(u.role, ''), 'admin'), :now FROM users u "
            "WHERE NOT EXISTS (SELECT 1 FROM tenant_memberships m "
            "                  WHERE m.user_id = u.id AND m.tenant_id = 1)"
        ),
        {"now": now},
    )


def downgrade() -> None:
    for table in ("tenant_settings", "provider_memberships", "tenant_memberships",
                  "sites", "tenants", "providers"):
        if _has_table(table):
            op.drop_table(table)

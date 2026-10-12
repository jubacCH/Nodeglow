"""Multi-tenancy, phase 9 (ee): tenant lifecycle and support access.

* ``tenants`` gains the lifecycle columns: ``suspended_at``,
  ``delete_after`` (a scheduled deletion is purged after it, owner decision
  6: 30 days suspended), ``deletion_requested_at`` / ``_by``, and
  ``purge_started_at`` / ``purge_state`` (a purge in progress, resumed after
  a restart). ``plan`` (retention tier, owner decision 5) exists since 039.
* ``support_grants`` (tenant-owned): time-limited support access a tenant
  admin grants to the installation operators (owner decision 8). Gets the
  ``tenant_isolation`` policy like every tenant-owned table (and RLS on when
  ``NODEGLOW_RLS=on``); ``migrate.py`` re-applies the whole setup from the
  live classification on every start anyway.

The core only stores these; the ee package reads and writes them. Nothing
changes for an existing install.

Revision ID: 045
Revises: 044
"""
import sqlalchemy as sa
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None

_TENANT_COLUMNS = (
    ("suspended_at", sa.DateTime()),
    ("delete_after", sa.DateTime()),
    ("deletion_requested_at", sa.DateTime()),
    ("deletion_requested_by", sa.Integer()),
    ("purge_started_at", sa.DateTime()),
    ("purge_state", sa.Text()),
)


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    for name, type_ in _TENANT_COLUMNS:
        if not _has_column("tenants", name):
            op.add_column("tenants", sa.Column(name, type_, nullable=True))

    if not _has_table("support_grants"):
        op.create_table(
            "support_grants",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("granted_by", sa.Integer(), nullable=True),
            sa.Column("granted_by_name", sa.String(64), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by", sa.Integer(), nullable=True),
            sa.Column("revoked_by_name", sa.String(64), nullable=True),
            sa.Column("ended_audited_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_support_grants_tenant"),
        )
        op.create_index("ix_support_grants_tenant_id", "support_grants", ["tenant_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        import config
        from services import pg_rls

        pg_rls.apply(bind, ("support_grants",), enabled=config.RLS_ENABLED,
                     login=pg_rls.app_login())


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table("support_grants"):
        if bind.dialect.name == "postgresql":
            from services import pg_rls

            pg_rls.remove(bind, ("support_grants",))
        op.drop_index("ix_support_grants_tenant_id", table_name="support_grants")
        op.drop_table("support_grants")
    for name, _type in reversed(_TENANT_COLUMNS):
        if _has_column("tenants", name):
            op.drop_column("tenants", name)

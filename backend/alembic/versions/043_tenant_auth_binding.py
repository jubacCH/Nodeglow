"""Multi-tenancy, auth binding: memberships are the source of truth.

* ``sessions.active_tenant_id`` — the tenant a browser session works in.
* ``users.is_operator`` — installation operator (manages installation
  settings and, in ee, providers). Every existing admin becomes one: in the
  core the admin of the one tenant administers the installation.
* ``audit_logs.provider_id`` / ``acting_via`` — the provider of the tenant
  and how the actor had access. Existing rows get the provider of their
  tenant; ``acting_via`` stays NULL (unknown) for them.
* Memberships of tenant 1 are re-synced from ``users.role`` (between 039
  and 043 only that column was written, so 039's copies are stale where a
  role changed) and backfilled for users created after 039 (setup wizard,
  users API, LDAP auto-provisioning wrote none until now). ``users.role`` is kept in sync with the default
  tenant's membership for one release, so a downgrade still has every role.

Revision ID: 043
Revises: 042
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column("sessions", "active_tenant_id"):
        op.add_column("sessions", sa.Column("active_tenant_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_sessions_active_tenant", "sessions", "tenants",
            ["active_tenant_id"], ["id"], ondelete="SET NULL",
        )

    if not _has_column("users", "is_operator"):
        op.add_column("users", sa.Column(
            "is_operator", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
    bind.execute(sa.text(
        "UPDATE users SET is_operator = true "
        "WHERE COALESCE(NULLIF(role, ''), 'admin') = 'admin'"
    ))

    if not _has_column("audit_logs", "provider_id"):
        op.add_column("audit_logs", sa.Column("provider_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_audit_logs_provider", "audit_logs", "providers", ["provider_id"], ["id"],
        )
    if not _has_column("audit_logs", "acting_via"):
        op.add_column("audit_logs", sa.Column("acting_via", sa.String(16), nullable=True))
    bind.execute(sa.text(
        "UPDATE audit_logs SET provider_id = "
        "(SELECT t.provider_id FROM tenants t WHERE t.id = audit_logs.tenant_id) "
        "WHERE tenant_id IS NOT NULL AND provider_id IS NULL"
    ))

    # Between 039 and 043 only users.role was written (users API, LDAP login,
    # setup): the memberships 039 created are stale wherever a role changed
    # since. users.role wins for the default tenant.
    bind.execute(sa.text(
        "UPDATE tenant_memberships SET role = ("
        "  SELECT COALESCE(NULLIF(u.role, ''), 'admin') FROM users u "
        "  WHERE u.id = tenant_memberships.user_id) "
        "WHERE tenant_id = 1 AND EXISTS ("
        "  SELECT 1 FROM users u WHERE u.id = tenant_memberships.user_id "
        "  AND COALESCE(NULLIF(u.role, ''), 'admin') <> tenant_memberships.role)"
    ))

    # Users created since 039 have no membership yet.
    bind.execute(
        sa.text(
            "INSERT INTO tenant_memberships (user_id, tenant_id, role, created_at) "
            "SELECT u.id, 1, COALESCE(NULLIF(u.role, ''), 'admin'), :now FROM users u "
            "WHERE NOT EXISTS (SELECT 1 FROM tenant_memberships m WHERE m.user_id = u.id)"
        ),
        {"now": datetime.utcnow()},
    )


def downgrade() -> None:
    bind = op.get_bind()
    # users.role was kept in sync; refresh it once more from the default
    # tenant's memberships so nothing granted after the upgrade is lost.
    bind.execute(sa.text(
        "UPDATE users SET role = (SELECT m.role FROM tenant_memberships m "
        "WHERE m.user_id = users.id AND m.tenant_id = 1) "
        "WHERE EXISTS (SELECT 1 FROM tenant_memberships m "
        "WHERE m.user_id = users.id AND m.tenant_id = 1)"
    ))
    if _has_column("audit_logs", "acting_via"):
        op.drop_column("audit_logs", "acting_via")
    if _has_column("audit_logs", "provider_id"):
        op.drop_constraint("fk_audit_logs_provider", "audit_logs", type_="foreignkey")
        op.drop_column("audit_logs", "provider_id")
    if _has_column("users", "is_operator"):
        op.drop_column("users", "is_operator")
    if _has_column("sessions", "active_tenant_id"):
        op.drop_constraint("fk_sessions_active_tenant", "sessions", type_="foreignkey")
        op.drop_column("sessions", "active_tenant_id")

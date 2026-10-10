"""Multi-tenancy, step 2: ``tenant_id`` on every tenant-owned table.

Adds ``tenant_id`` (and ``site_id`` on hosts, agents, install tokens and scan
schedules), backfills tenant 1 / site 1, then makes ``tenant_id`` NOT NULL
with **no server default**: a new row gets its tenant from the application's
tenant context, never by accident. ``audit_logs.tenant_id`` stays nullable —
NULL marks an installation-level entry.

``user_preferences`` becomes per user *and* tenant: its primary key changes
from ``(user_id)`` to ``(user_id, tenant_id)``.

Every step checks first. Some tables (``agents`` among them) were historically
created by ``create_all()`` rather than by a migration, so on some databases a
column may already exist; and a table that does not exist is skipped.

Revision ID: 040
Revises: 039
"""
import sqlalchemy as sa
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

# table -> True when the plain ix_<table>_tenant_id index is wanted. False
# where another index or unique constraint leads with tenant_id (revision 041).
TENANT_TABLES: dict[str, bool] = {
    "ping_hosts": False,
    "integration_configs": True,
    "snapshots": False,
    "syslog_views": True,
    "incidents": False,
    "incident_events": True,
    "log_templates": False,
    "host_baselines": False,
    "precursor_patterns": False,
    "fleet_patterns": True,
    "agents": False,
    "agent_install_tokens": True,
    "subnet_scan_schedules": False,
    "subnet_scan_logs": True,
    "credentials": False,
    "snmp_host_configs": True,
    "snmp_results": True,
    "api_keys": True,
    "notification_logs": True,
    "alert_rules": True,
    "discovered_ports": False,
    "audit_logs": True,
    "ai_usage_logs": True,
    "backup_jobs": False,
    "backup_history": True,
    "maintenance_windows": True,
}
NULLABLE_TENANT = {"audit_logs"}
SITE_TABLES = ("ping_hosts", "agents", "agent_install_tokens", "subnet_scan_schedules")


def _insp():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_insp().get_table_names())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in _insp().get_indexes(table))


def _has_fk(table: str, name: str) -> bool:
    return any(fk["name"] == name for fk in _insp().get_foreign_keys(table))


def _add_tenant_id(table: str, with_index: bool) -> None:
    if not _has_column(table, "tenant_id"):
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute(f"UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL")
    if table not in NULLABLE_TENANT:
        op.alter_column(table, "tenant_id", existing_type=sa.Integer(), nullable=False)
    if not _has_fk(table, f"fk_{table}_tenant"):
        op.create_foreign_key(f"fk_{table}_tenant", table, "tenants", ["tenant_id"], ["id"])
    if with_index and not _has_index(table, f"ix_{table}_tenant_id"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def upgrade() -> None:
    tables = _tables()
    for table, with_index in TENANT_TABLES.items():
        if table in tables:
            _add_tenant_id(table, with_index)

    for table in SITE_TABLES:
        if table in tables:
            if not _has_column(table, "site_id"):
                op.add_column(table, sa.Column("site_id", sa.Integer(), nullable=True))
            op.execute(f"UPDATE {table} SET site_id = 1 WHERE site_id IS NULL")

    if "user_preferences" in tables and not _has_column("user_preferences", "tenant_id"):
        op.add_column("user_preferences", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.execute("UPDATE user_preferences SET tenant_id = 1 WHERE tenant_id IS NULL")
        op.alter_column("user_preferences", "tenant_id", existing_type=sa.Integer(),
                        nullable=False)
        pk = _insp().get_pk_constraint("user_preferences")
        op.drop_constraint(pk["name"] or "user_preferences_pkey", "user_preferences",
                           type_="primary")
        op.create_primary_key("user_preferences_pkey", "user_preferences",
                              ["user_id", "tenant_id"])
        op.create_foreign_key("fk_user_preferences_tenant", "user_preferences", "tenants",
                              ["tenant_id"], ["id"])
        op.create_index("ix_user_preferences_tenant_id", "user_preferences", ["tenant_id"])


def downgrade() -> None:
    tables = _tables()
    if "user_preferences" in tables and _has_column("user_preferences", "tenant_id"):
        # Keep one row per user (tenant 1's) so the old primary key fits.
        op.execute("DELETE FROM user_preferences WHERE tenant_id <> 1")
        op.drop_index("ix_user_preferences_tenant_id", table_name="user_preferences")
        op.drop_constraint("fk_user_preferences_tenant", "user_preferences", type_="foreignkey")
        op.drop_constraint("user_preferences_pkey", "user_preferences", type_="primary")
        op.drop_column("user_preferences", "tenant_id")
        op.create_primary_key("user_preferences_pkey", "user_preferences", ["user_id"])

    for table in SITE_TABLES:
        if table in tables and _has_column(table, "site_id"):
            op.drop_column(table, "site_id")

    for table, with_index in TENANT_TABLES.items():
        if table not in tables or not _has_column(table, "tenant_id"):
            continue
        if with_index and _has_index(table, f"ix_{table}_tenant_id"):
            op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        if _has_fk(table, f"fk_{table}_tenant"):
            op.drop_constraint(f"fk_{table}_tenant", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

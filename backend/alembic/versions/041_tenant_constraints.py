"""Multi-tenancy, step 3: per-tenant uniqueness and same-tenant references.

* Parents get ``UNIQUE (tenant_id, id)`` and every real foreign key between
  tenant-owned tables becomes composite — ``(tenant_id, child_col) ->
  parent (tenant_id, id)`` — so a row can only reference a row of its own
  tenant, whatever the application does. The two ``SET NULL`` references
  (``ping_hosts.probe_id``, ``snmp_host_configs.credential_id``) use
  PostgreSQL's ``ON DELETE SET NULL (column)`` so only the reference is
  cleared, never ``tenant_id``.
* Global unique indexes on tenant-owned tables become per tenant
  (``(tenant_id, ...)``). ``agents.token``, ``api_keys.key_hash`` and
  ``agent_install_tokens.token_hash`` stay global on purpose: they are looked
  up before the tenant is known, and the row they find names the tenant.
* The hot-path composite indexes of ``snapshots`` and ``incidents`` lead with
  ``tenant_id``, since every tenant-scoped query now filters on it.

Old foreign keys are found by their columns, not their names: databases built
by ``create_all()`` and by the migration chain named them differently.

Revision ID: 041
Revises: 040
"""
import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None

PARENTS = (
    "ping_hosts", "agents", "backup_jobs", "incidents", "log_templates",
    "subnet_scan_schedules", "credentials",
)

# (child table, column, parent table, constraint name, ondelete)
COMPOSITE_FKS = (
    ("ping_hosts", "parent_id", "ping_hosts", "fk_ping_hosts_parent", None),
    ("ping_hosts", "probe_id", "agents", "fk_ping_hosts_probe_id", "SET NULL"),
    ("ping_hosts", "site_id", "sites", "fk_ping_hosts_site", None),
    ("agents", "site_id", "sites", "fk_agents_site", None),
    ("agent_install_tokens", "site_id", "sites", "fk_agent_install_tokens_site", None),
    ("subnet_scan_schedules", "site_id", "sites", "fk_subnet_scan_schedules_site", None),
    ("subnet_scan_logs", "schedule_id", "subnet_scan_schedules",
     "fk_subnet_scan_logs_schedule", "CASCADE"),
    ("backup_history", "job_id", "backup_jobs", "fk_backup_history_job", "CASCADE"),
    ("discovered_ports", "host_id", "ping_hosts", "fk_discovered_ports_host", "CASCADE"),
    ("incident_events", "incident_id", "incidents", "fk_incident_events_incident", None),
    ("precursor_patterns", "template_id", "log_templates",
     "fk_precursor_patterns_template", None),
    ("snmp_host_configs", "host_id", "ping_hosts", "fk_snmp_host_configs_host", "CASCADE"),
    ("snmp_host_configs", "credential_id", "credentials",
     "fk_snmp_host_configs_credential", "SET NULL"),
    ("snmp_results", "host_id", "ping_hosts", "fk_snmp_results_host", "CASCADE"),
)

# name -> (table, old columns, new columns, unique). Strings in the column
# lists are column names, sa.text() entries are expressions.
INDEXES = {
    "ix_agents_hostname_lower": (
        "agents", [sa.text("lower(hostname)")],
        ["tenant_id", sa.text("lower(hostname)")], True),
    "ix_log_templates_template_hash": (
        "log_templates", ["template_hash"], ["tenant_id", "template_hash"], True),
    "ix_baseline_host_time": (
        "host_baselines", ["host_key", "hour_of_day", "day_of_week"],
        ["tenant_id", "host_key", "hour_of_day", "day_of_week"], True),
    "ix_disc_port_host_port": (
        "discovered_ports", ["host_id", "port"], ["tenant_id", "host_id", "port"], True),
    "ix_precursor_tpl_event": (
        "precursor_patterns", ["template_id", "precedes_event"],
        ["tenant_id", "template_id", "precedes_event"], True),
    "ix_snap_type_entity_ts": (
        "snapshots", ["entity_type", "entity_id", sa.text("timestamp DESC")],
        ["tenant_id", "entity_type", "entity_id", sa.text("timestamp DESC")], False),
    "ix_snap_type_ts": (
        "snapshots", ["entity_type", "timestamp"],
        ["tenant_id", "entity_type", "timestamp"], False),
    "ix_snap_latest_per_entity": (
        "snapshots", ["entity_type", "entity_id", sa.text("id DESC")],
        ["tenant_id", "entity_type", "entity_id", sa.text("id DESC")], False),
    "ix_incident_status": ("incidents", ["status"], ["tenant_id", "status"], False),
    "ix_incident_rule_hash": (
        "incidents", ["rule", "host_ids_hash"], ["tenant_id", "rule", "host_ids_hash"], False),
}


def _insp():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_insp().get_table_names())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in _insp().get_indexes(table))


def _unique_constraints(table: str) -> set[str]:
    return {u["name"] for u in _insp().get_unique_constraints(table)}


def _fks(table: str) -> list[dict]:
    return _insp().get_foreign_keys(table)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _ondelete(column: str, ondelete: str | None) -> str | None:
    if ondelete == "SET NULL":
        return f"SET NULL ({column})"
    return ondelete


def _recreate_index(name: str, table: str, columns: list, unique: bool) -> None:
    if _has_index(table, name):
        op.drop_index(name, table_name=table)
    op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    tables = _tables()

    for table in PARENTS:
        if table in tables and f"uq_{table}_tenant_id_id" not in _unique_constraints(table):
            op.create_unique_constraint(f"uq_{table}_tenant_id_id", table, ["tenant_id", "id"])

    for child, column, parent, name, ondelete in COMPOSITE_FKS:
        if child not in tables or parent not in tables or not _has_column(child, column):
            continue
        for fk in _fks(child):
            if fk["constrained_columns"] in ([column], ["tenant_id", column]):
                op.drop_constraint(fk["name"], child, type_="foreignkey")
        if ondelete == "SET NULL" and not _is_pg():
            continue  # column-list SET NULL is PostgreSQL-only, as in the models
        op.create_foreign_key(
            name, child, parent, ["tenant_id", column], ["tenant_id", "id"],
            ondelete=_ondelete(column, ondelete),
        )

    for name, (table, _old, new, unique) in INDEXES.items():
        if table in tables:
            _recreate_index(name, table, new, unique)


def downgrade() -> None:
    tables = _tables()

    for name, (table, old, _new, unique) in INDEXES.items():
        if table in tables:
            _recreate_index(name, table, old, unique)

    for child, column, parent, name, ondelete in COMPOSITE_FKS:
        if child not in tables or not any(fk["name"] == name for fk in _fks(child)):
            continue
        op.drop_constraint(name, child, type_="foreignkey")
        if parent != "sites":
            op.create_foreign_key(name, child, parent, [column], ["id"], ondelete=ondelete)

    for table in PARENTS:
        if table in tables and f"uq_{table}_tenant_id_id" in _unique_constraints(table):
            op.drop_constraint(f"uq_{table}_tenant_id_id", table, type_="unique")

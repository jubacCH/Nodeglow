"""Multi-tenancy, phase 7: PostgreSQL row-level security and the role split.

* Role ``nodeglow_app`` (runtime DML, ``NOBYPASSRLS``, owns nothing) is
  created if missing, with the privileges of the connecting (owning) user;
  it gets SELECT/INSERT/UPDATE/DELETE on every table and the sequences, now
  and by default privileges later. When ``DATABASE_URL`` names its own login
  (different from the owner URL), that login is created or its password
  updated. Without CREATEROLE this is logged and skipped — never fatal.
* Every tenant-owned table gets the policy ``tenant_isolation`` (``FOR ALL``;
  tenant from ``nodeglow.tenant_id``, bypass with ``nodeglow.system = on``).
* ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` only with ``NODEGLOW_RLS=on``;
  otherwise the policies exist and RLS stays off (every existing install).
  ``migrate.py`` re-applies this on each start, so flipping the flag later
  needs no new revision.

Separate revision (spec, migration step 6) so it can be rolled back without
undoing the tenant backfill. The downgrade drops the policies and switches
RLS off; roles and grants stay, so an app already running as
``nodeglow_app`` keeps working.

Revision ID: 044
Revises: 043
"""
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None

# Tenant-owned tables at this revision (models.tenant classification);
# frozen here, the live list is applied by migrate.py.
TENANT_TABLES = (
    "agent_install_tokens", "agents", "ai_usage_logs", "alert_rules", "api_keys",
    "audit_logs", "backup_history", "backup_jobs", "credentials", "discovered_ports",
    "fleet_patterns", "host_baselines", "incident_events", "incidents",
    "integration_configs", "log_templates", "maintenance_windows", "notification_logs",
    "ping_hosts", "precursor_patterns", "sites", "snapshots", "snmp_host_configs",
    "snmp_results", "subnet_scan_logs", "subnet_scan_schedules", "syslog_views",
    "tenant_settings", "user_preferences",
)


def upgrade() -> None:
    import config
    from services import pg_rls

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    pg_rls.apply(bind, TENANT_TABLES, enabled=config.RLS_ENABLED, login=pg_rls.app_login())


def downgrade() -> None:
    from services import pg_rls

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    pg_rls.remove(bind, TENANT_TABLES)

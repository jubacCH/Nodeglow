"""Agent deploy through a probe (part C).

See ``docs/specs/2026-11-19-probe-agent-deploy-design.md``.

* ``agent_commands`` (tenant-owned, RLS): structured commands for a probe,
  delivered with its heartbeat. Composite tenant FK to ``agents`` with
  ``ON DELETE CASCADE`` (PostgreSQL only, like every composite FK here).
* ``agents.deploy_capability``: the probe's last report of its local opt-in.
* ``ssh_host_keys.probe_id``: host keys as seen from a probe; the unique key
  becomes (tenant, probe or 0 for the server, host, port).
* ``agent_deploy_jobs.probe_id`` / ``command_id``: jobs carried by a probe.

Downgrade: drops the table and the columns and restores the old unique key
(it fails while two probes hold a key for the same address).

Revision ID: 057
Revises: 056
"""
import sqlalchemy as sa
from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None

COMMANDS = "agent_commands"
HOST_KEYS = "ssh_host_keys"
JOBS = "agent_deploy_jobs"
UNIQUE = "uq_ssh_host_keys_address"
PROBE_FK = "fk_ssh_host_keys_probe"
COMMANDS_FK = "fk_agent_commands_agent"


def _insp():
    return sa.inspect(op.get_bind())


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _unique_names(table: str) -> set[str]:
    names = {u["name"] for u in _insp().get_unique_constraints(table)}
    names |= {i["name"] for i in _insp().get_indexes(table) if i.get("unique")}
    return names


def upgrade() -> None:
    if not _has_column("agents", "deploy_capability"):
        op.add_column("agents", sa.Column("deploy_capability", sa.Text(), nullable=True))

    for column in ("probe_id", "command_id"):
        if not _has_column(JOBS, column):
            op.add_column(JOBS, sa.Column(column, sa.Integer(), nullable=True))

    if not _has_column(HOST_KEYS, "probe_id"):
        op.add_column(HOST_KEYS, sa.Column("probe_id", sa.Integer(), nullable=True))
    # The old key (tenant, host, port) becomes (tenant, probe or 0, host, port).
    if UNIQUE in {u["name"] for u in _insp().get_unique_constraints(HOST_KEYS)}:
        with op.batch_alter_table(HOST_KEYS) as batch:
            batch.drop_constraint(UNIQUE, type_="unique")
    if UNIQUE not in _unique_names(HOST_KEYS):
        op.create_index(UNIQUE, HOST_KEYS,
                        ["tenant_id", sa.text("coalesce(probe_id, 0)"), "host", "port"], unique=True)
    if _is_pg() and PROBE_FK not in {fk["name"] for fk in _insp().get_foreign_keys(HOST_KEYS)}:
        op.create_foreign_key(PROBE_FK, HOST_KEYS, "agents", ["tenant_id", "probe_id"],
                              ["tenant_id", "id"], ondelete="CASCADE")

    if not _insp().has_table(COMMANDS):
        op.create_table(
            COMMANDS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("agent_id", sa.Integer(), nullable=False),
            sa.Column("op", sa.String(32), nullable=False),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_commands_tenant"),
        )
        op.create_index("ix_agent_commands_agent", COMMANDS, ["tenant_id", "agent_id", "status"])
        if _is_pg():
            op.create_foreign_key(COMMANDS_FK, COMMANDS, "agents", ["tenant_id", "agent_id"],
                                  ["tenant_id", "id"], ondelete="CASCADE")

    if _is_pg():
        import config
        from services import pg_rls

        pg_rls.apply(op.get_bind(), [COMMANDS], enabled=config.RLS_ENABLED,
                     login=pg_rls.app_login())


def downgrade() -> None:
    if _insp().has_table(COMMANDS):
        if _is_pg():
            from services import pg_rls

            pg_rls.remove(op.get_bind(), [COMMANDS])
        op.drop_table(COMMANDS)

    if _is_pg() and PROBE_FK in {fk["name"] for fk in _insp().get_foreign_keys(HOST_KEYS)}:
        op.drop_constraint(PROBE_FK, HOST_KEYS, type_="foreignkey")
    if UNIQUE in _unique_names(HOST_KEYS):
        op.drop_index(UNIQUE, table_name=HOST_KEYS)
    if _has_column(HOST_KEYS, "probe_id"):
        with op.batch_alter_table(HOST_KEYS) as batch:
            batch.drop_column("probe_id")
            batch.create_unique_constraint(UNIQUE, ["tenant_id", "host", "port"])

    for column in ("command_id", "probe_id"):
        if _has_column(JOBS, column):
            with op.batch_alter_table(JOBS) as batch:
                batch.drop_column(column)
    if _has_column("agents", "deploy_capability"):
        with op.batch_alter_table("agents") as batch:
            batch.drop_column("deploy_capability")

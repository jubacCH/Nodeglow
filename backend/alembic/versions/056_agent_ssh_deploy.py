"""Agent deploy over SSH (part B).

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``.

* ``ssh_host_keys`` (tenant-owned, RLS): the host key an admin confirmed per
  ``host:port``. Unique per tenant and address.
* ``agent_deploy_jobs`` (tenant-owned, RLS): one row per target of a deploy
  run, grouped by ``run_id``. No secrets in either table.

Downgrade: drops both tables.

Revision ID: 056
Revises: 055
"""
import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None

HOST_KEYS = "ssh_host_keys"
JOBS = "agent_deploy_jobs"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    if not _inspector().has_table(HOST_KEYS):
        op.create_table(
            HOST_KEYS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("key_type", sa.String(64), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("fingerprint", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_ssh_host_keys_tenant"),
            sa.UniqueConstraint("tenant_id", "host", "port", name="uq_ssh_host_keys_address"),
        )
    if not _inspector().has_table(JOBS):
        op.create_table(
            JOBS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("target", sa.String(255), nullable=False),
            sa.Column("agent_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_deploy_jobs_tenant"),
        )
        op.create_index("ix_agent_deploy_jobs_run", JOBS, ["tenant_id", "run_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        import config
        from services import pg_rls

        pg_rls.apply(bind, [HOST_KEYS, JOBS], enabled=config.RLS_ENABLED, login=pg_rls.app_login())


def downgrade() -> None:
    bind = op.get_bind()
    for table in (JOBS, HOST_KEYS):
        if _inspector().has_table(table):
            if bind.dialect.name == "postgresql":
                from services import pg_rls

                pg_rls.remove(bind, [table])
            op.drop_table(table)

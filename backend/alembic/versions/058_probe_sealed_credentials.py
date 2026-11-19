"""Sealed deploy credentials for probes (part C).

See ``docs/specs/2026-11-19-probe-sealed-credentials-design.md``.

* ``probe_credentials`` (tenant-owned, RLS): credentials sealed to a probe's
  public key, stored under the tenant key. Composite tenant FK to ``agents``
  with ``ON DELETE CASCADE`` (PostgreSQL only, like every composite FK here).
* ``agents.seal_*``: the probe's public key, the last key it reported and who
  trusted it when.
* ``agent_commands.credential_id``: the credential a command uses (no FK).
* ``agent_install_tokens.probe``: the token enrols a probe.

Downgrade: drops the table and the columns.

Revision ID: 058
Revises: 057
"""
import sqlalchemy as sa
from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None

TABLE = "probe_credentials"
FK = "fk_probe_credentials_probe"
UNIQUE = "uq_probe_credentials_name"

AGENT_COLUMNS = (
    ("seal_public_key", sa.Text()),
    ("seal_key_reported", sa.Text()),
    ("seal_key_trusted_at", sa.DateTime()),
    ("seal_key_trusted_by", sa.String(255)),
)


def _insp():
    return sa.inspect(op.get_bind())


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    for name, type_ in AGENT_COLUMNS:
        if not _has_column("agents", name):
            op.add_column("agents", sa.Column(name, type_, nullable=True))

    if not _has_column("agent_commands", "credential_id"):
        op.add_column("agent_commands", sa.Column("credential_id", sa.Integer(), nullable=True))

    if not _has_column("agent_install_tokens", "probe"):
        op.add_column("agent_install_tokens",
                      sa.Column("probe", sa.Boolean(), nullable=False, server_default=sa.false()))

    if not _insp().has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("probe_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("username", sa.String(255), nullable=False),
            sa.Column("auth", sa.String(16), nullable=False),
            sa.Column("hosts", sa.Text(), nullable=False),
            sa.Column("key_fingerprint", sa.String(64), nullable=False),
            sa.Column("sealed", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_probe_credentials_tenant"),
        )
        op.create_index(UNIQUE, TABLE, ["tenant_id", "probe_id", "name"], unique=True)
        if _is_pg():
            op.create_foreign_key(FK, TABLE, "agents", ["tenant_id", "probe_id"],
                                  ["tenant_id", "id"], ondelete="CASCADE")

    if _is_pg():
        import config
        from services import pg_rls

        pg_rls.apply(op.get_bind(), [TABLE], enabled=config.RLS_ENABLED,
                     login=pg_rls.app_login())


def downgrade() -> None:
    if _insp().has_table(TABLE):
        if _is_pg():
            from services import pg_rls

            pg_rls.remove(op.get_bind(), [TABLE])
        op.drop_table(TABLE)

    for table, column in (("agent_install_tokens", "probe"), ("agent_commands", "credential_id"),
                          *(("agents", n) for n, _ in reversed(AGENT_COLUMNS))):
        if _has_column(table, column):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)

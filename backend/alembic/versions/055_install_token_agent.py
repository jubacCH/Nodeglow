"""Install tokens bound to an agent (agent reinstall).

See ``docs/specs/2026-11-16-agent-reinstall-design.md``.

* ``agent_install_tokens.agent_id``: when set, the token re-enrols exactly
  this existing agent (keeping its id) and is single use. NULL for every
  existing token, which keep behaving as before.
* Composite tenant FK to ``agents`` with ``ON DELETE CASCADE`` (PostgreSQL
  only, like every composite FK here): a token for a deleted agent is useless.

Downgrade: drops the FK, the index and the column.

Revision ID: 055
Revises: 054
"""
import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None

TABLE = "agent_install_tokens"
FK_NAME = "fk_agent_install_tokens_agent"
INDEX_NAME = "ix_agent_install_tokens_agent_id"


def _insp():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in _insp().get_indexes(table))


def _fks(table: str) -> set[str]:
    return {fk["name"] for fk in _insp().get_foreign_keys(table)}


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _has_column(TABLE, "agent_id"):
        op.add_column(TABLE, sa.Column("agent_id", sa.Integer(), nullable=True))
    if not _has_index(TABLE, INDEX_NAME):
        op.create_index(INDEX_NAME, TABLE, ["agent_id"])
    if _is_pg() and FK_NAME not in _fks(TABLE):
        op.create_foreign_key(
            FK_NAME, TABLE, "agents",
            ["tenant_id", "agent_id"], ["tenant_id", "id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    if _is_pg() and FK_NAME in _fks(TABLE):
        op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    if _has_index(TABLE, INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name=TABLE)
    if _has_column(TABLE, "agent_id"):
        op.drop_column(TABLE, "agent_id")

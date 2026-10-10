"""Per-user preferences, starting with the last dashboard visit.

``user_preferences`` holds one row per user (created on first write), so the
dashboard can say what changed since the user last looked. No rows are
created here: a missing row means "never recorded".

Revision ID: 038
Revises: 037
"""
import sqlalchemy as sa
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("user_preferences"):
        op.create_table(
            "user_preferences",
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("dashboard_seen_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_table("user_preferences"):
        op.drop_table("user_preferences")

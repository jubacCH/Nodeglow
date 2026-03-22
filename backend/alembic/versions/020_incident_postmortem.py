"""Add postmortem columns to incidents table.

Revision ID: 020
Revises: 019
"""
revision = "020"
down_revision = "019"

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa


def _has_column(inspector, table, column):
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _has_column(inspector, "incidents", "postmortem"):
        with op.batch_alter_table("incidents") as batch_op:
            batch_op.add_column(sa.Column("postmortem", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("postmortem_generated_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_column("postmortem_generated_at")
        batch_op.drop_column("postmortem")

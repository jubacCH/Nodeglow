"""Watched services: store the state agents report for them.

``agents.watched_services`` (017) has held the list since long ago, but nothing
read it. ``agents.service_states`` keeps the latest reported state per watched
service together with its failure streak, so incident evaluation survives a
backend restart. NULL for every existing row: nothing changes until an agent
that knows the feature reports.

Revision ID: 036_agent_services
Revises: 034
"""
import sqlalchemy as sa
from alembic import op

revision = "036_agent_services"
down_revision = "034"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column("agents", "service_states"):
        op.add_column("agents", sa.Column("service_states", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("agents", "service_states"):
        op.drop_column("agents", "service_states")

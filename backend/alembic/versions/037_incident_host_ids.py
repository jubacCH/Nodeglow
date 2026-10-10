"""Affected hosts on incidents.

``incidents.host_ids_hash`` only ever allowed deduplication: the hash cannot be
reversed, so no screen could say which hosts an incident is about.
``incidents.host_ids`` stores the sorted ids as a JSON list. It stays NULL for
every existing row — no backfill: NULL means "not recorded", which the API
reports as unknown rather than as an incident without hosts.

Revision ID: 037
Revises: 036
"""
import sqlalchemy as sa
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column("incidents", "host_ids"):
        op.add_column("incidents", sa.Column("host_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("incidents", "host_ids"):
        op.drop_column("incidents", "host_ids")

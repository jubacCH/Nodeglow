"""Add maintenance_until column to ping_hosts.

Revision ID: 014
Revises: 013
"""
revision = "014"
down_revision = "013"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("ping_hosts", sa.Column("maintenance_until", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("ping_hosts", "maintenance_until")

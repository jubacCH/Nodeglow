"""Add auth_source and display_name to users table.

Revision ID: 021
Revises: 020
"""
revision = "021"
down_revision = "020"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("users", sa.Column("auth_source", sa.String(16), server_default="local"))
    op.add_column("users", sa.Column("display_name", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("users", "display_name")
    op.drop_column("users", "auth_source")

"""Add pending_command column to agents for remote commands.

Revision ID: 016
Revises: 015
"""
revision = "016"
down_revision = "015"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("agents", sa.Column("pending_command", sa.String(32), nullable=True))

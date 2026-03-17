"""Add watched_services column to agents.

Revision ID: 017
Revises: 016
"""
revision = "017"
down_revision = "016"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("agents", sa.Column("watched_services", sa.Text(), nullable=True))

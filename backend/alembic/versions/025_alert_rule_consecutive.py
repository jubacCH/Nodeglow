"""Add required_consecutive column to alert_rules.

Revision ID: 025
Revises: 024
"""
revision = "025"
down_revision = "024"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "alert_rules",
        sa.Column("required_consecutive", sa.Integer(), nullable=False, server_default="2"),
    )


def downgrade():
    op.drop_column("alert_rules", "required_consecutive")

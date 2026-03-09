"""Add subnet_scan_logs table for scan history.

Revision ID: 008
Revises: 007
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subnet_scan_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("schedule_id", sa.Integer, sa.ForeignKey("subnet_scan_schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("alive", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("hosts_added", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_scan_log_schedule_ts", "subnet_scan_logs", ["schedule_id", sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_index("ix_scan_log_schedule_ts", table_name="subnet_scan_logs")
    op.drop_table("subnet_scan_logs")

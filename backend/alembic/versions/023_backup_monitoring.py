"""Add backup_jobs and backup_history tables for backup monitoring.

Revision ID: 023
Revises: 022
Create Date: 2026-04-03
"""
revision = "023"
down_revision = "022"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_config_id", sa.Integer(), nullable=True),
        sa.Column("target_name", sa.String(256), nullable=False),
        sa.Column("target_vmid", sa.Integer(), nullable=True),
        sa.Column("storage_name", sa.String(128), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(32), server_default="unknown"),
        sa.Column("last_duration_sec", sa.Integer(), nullable=True),
        sa.Column("last_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("expected_frequency_hours", sa.Integer(), server_default="24"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_backup_jobs_source_type", "backup_jobs", ["source_type"])
    op.create_index("ix_backup_jobs_source", "backup_jobs", ["source_type", "source_config_id"])

    op.create_table(
        "backup_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_backup_history_job_id", "backup_history", ["job_id"])
    op.create_index("ix_backup_history_timestamp", "backup_history", ["timestamp"])
    op.create_index("ix_backup_history_job_ts", "backup_history", ["job_id", sa.text("timestamp DESC")])


def downgrade():
    op.drop_table("backup_history")
    op.drop_table("backup_jobs")

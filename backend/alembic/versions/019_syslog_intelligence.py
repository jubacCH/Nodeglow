"""Add syslog intelligence columns: trends, diversity, fleet patterns, precursor lead times.

Revision ID: 019
Revises: 018
"""
revision = "019"
down_revision = "018"

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa


def _has_column(inspector, table, column):
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    # LogTemplate: trend detection columns
    if not _has_column(inspector, "log_templates", "trend_direction"):
        with op.batch_alter_table("log_templates") as batch_op:
            batch_op.add_column(sa.Column("trend_direction", sa.String(16), server_default="stable"))
            batch_op.add_column(sa.Column("trend_score", sa.Float(), server_default="0.0"))
            batch_op.add_column(sa.Column("severity_mode", sa.SmallInteger(), nullable=True))

    # HostBaseline: template diversity columns
    if not _has_column(inspector, "host_baselines", "avg_template_count"):
        with op.batch_alter_table("host_baselines") as batch_op:
            batch_op.add_column(sa.Column("avg_template_count", sa.Float(), server_default="0.0"))
            batch_op.add_column(sa.Column("std_template_count", sa.Float(), server_default="0.0"))

    # PrecursorPattern: min/max lead time
    if not _has_column(inspector, "precursor_patterns", "min_lead_time_sec"):
        with op.batch_alter_table("precursor_patterns") as batch_op:
            batch_op.add_column(sa.Column("min_lead_time_sec", sa.Integer(), server_default="0"))
            batch_op.add_column(sa.Column("max_lead_time_sec", sa.Integer(), server_default="0"))

    # FleetPattern: cross-host correlation
    if "fleet_patterns" not in existing_tables:
        op.create_table(
            "fleet_patterns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("template_hash", sa.String(32), nullable=False, index=True),
            sa.Column("host_count", sa.Integer(), server_default="0"),
            sa.Column("source_ips", sa.Text(), server_default=""),
            sa.Column("first_seen", sa.DateTime()),
            sa.Column("last_checked", sa.DateTime()),
            sa.Column("is_baseline", sa.Boolean(), server_default="0"),
            sa.Column("status", sa.String(16), server_default="active"),
        )
        op.create_index("ix_fleet_hash_status", "fleet_patterns", ["template_hash", "status"])


def downgrade():
    op.drop_table("fleet_patterns")
    with op.batch_alter_table("precursor_patterns") as batch_op:
        batch_op.drop_column("min_lead_time_sec")
        batch_op.drop_column("max_lead_time_sec")
    with op.batch_alter_table("host_baselines") as batch_op:
        batch_op.drop_column("avg_template_count")
        batch_op.drop_column("std_template_count")
    with op.batch_alter_table("log_templates") as batch_op:
        batch_op.drop_column("trend_direction")
        batch_op.drop_column("trend_score")
        batch_op.drop_column("severity_mode")

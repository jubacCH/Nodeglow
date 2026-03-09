"""Add credentials, SNMP tables, and subnet scan schedules.

Revision ID: 006
Revises: 005
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Credentials
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, index=True),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # SNMP MIBs
    op.create_table(
        "snmp_mibs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("filename", sa.String(256)),
        sa.Column("oid_count", sa.Integer(), default=0),
        sa.Column("raw_text", sa.Text()),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # SNMP OIDs
    op.create_table(
        "snmp_oids",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("oid", sa.String(256), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("mib_name", sa.String(128), index=True),
        sa.Column("syntax", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("is_table", sa.Boolean(), default=False),
    )

    # SNMP Host Configs
    op.create_table(
        "snmp_host_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("ping_hosts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("credential_id", sa.Integer(), sa.ForeignKey("credentials.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("port", sa.Integer(), default=161),
        sa.Column("oids_json", sa.Text()),
        sa.Column("thresholds_json", sa.Text()),
        sa.Column("poll_interval", sa.Integer(), default=60),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("last_poll", sa.DateTime(), nullable=True),
        sa.Column("last_ok", sa.Boolean(), nullable=True),
    )

    # SNMP Results
    op.create_table(
        "snmp_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("ping_hosts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now(), index=True),
        sa.Column("data_json", sa.Text()),
    )
    op.create_index("ix_snmp_results_host_ts", "snmp_results", ["host_id", sa.text("timestamp DESC")])

    # Subnet Scan Schedules
    op.create_table(
        "subnet_scan_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("interval_m", sa.Integer(), default=60),
        sa.Column("auto_add", sa.Boolean(), default=True),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("last_scan", sa.DateTime(), nullable=True),
        sa.Column("last_alive", sa.Integer(), nullable=True),
        sa.Column("last_total", sa.Integer(), nullable=True),
        sa.Column("last_added", sa.Integer(), default=0),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("subnet_scan_schedules")
    op.drop_index("ix_snmp_results_host_ts", table_name="snmp_results")
    op.drop_table("snmp_results")
    op.drop_table("snmp_host_configs")
    op.drop_table("snmp_oids")
    op.drop_table("snmp_mibs")
    op.drop_table("credentials")

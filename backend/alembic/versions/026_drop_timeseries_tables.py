"""Drop time-series tables migrated to ClickHouse.

ping_results, agent_snapshots, and bandwidth_samples now live in ClickHouse
(`ping_checks`, `agent_metrics`, `bandwidth_metrics`). This migration removes
them from Postgres entirely.

Revision ID: 026
Revises: 025
"""
revision = "026"
down_revision = "025"

from alembic import op
import sqlalchemy as sa


def upgrade():
    # Drop indexes first (if Postgres complains about dependencies)
    for tbl in ("ping_results", "agent_snapshots", "bandwidth_samples"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")


def downgrade():
    # Best-effort recreate of the original schemas. We don't restore data —
    # ClickHouse is the source of truth post-cutover.
    op.create_table(
        "ping_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("ping_hosts.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
    )
    op.create_index("ix_ping_results_timestamp", "ping_results", ["timestamp"])
    op.create_index("ix_ping_results_host_ts", "ping_results", ["host_id", "timestamp"])

    op.create_table(
        "agent_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer(), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("cpu_pct", sa.Float(), nullable=True),
        sa.Column("mem_pct", sa.Float(), nullable=True),
        sa.Column("mem_used_mb", sa.Float(), nullable=True),
        sa.Column("mem_total_mb", sa.Float(), nullable=True),
        sa.Column("disk_pct", sa.Float(), nullable=True),
        sa.Column("load_1", sa.Float(), nullable=True),
        sa.Column("load_5", sa.Float(), nullable=True),
        sa.Column("load_15", sa.Float(), nullable=True),
        sa.Column("uptime_s", sa.Integer(), nullable=True),
        sa.Column("rx_bytes", sa.Float(), nullable=True),
        sa.Column("tx_bytes", sa.Float(), nullable=True),
        sa.Column("data_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "bandwidth_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("interface_name", sa.String(128), nullable=False),
        sa.Column("rx_bytes", sa.BigInteger(), default=0),
        sa.Column("tx_bytes", sa.BigInteger(), default=0),
        sa.Column("rx_rate_bps", sa.BigInteger(), default=0),
        sa.Column("tx_rate_bps", sa.BigInteger(), default=0),
    )

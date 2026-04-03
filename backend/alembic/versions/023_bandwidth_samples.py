"""Add bandwidth_samples table for traffic monitoring.

Revision ID: 023
Revises: 022
"""
revision = "023"
down_revision = "022"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "bandwidth_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("interface_name", sa.String(128), nullable=False),
        sa.Column("rx_bytes", sa.BigInteger(), server_default="0"),
        sa.Column("tx_bytes", sa.BigInteger(), server_default="0"),
        sa.Column("rx_rate_bps", sa.BigInteger(), server_default="0"),
        sa.Column("tx_rate_bps", sa.BigInteger(), server_default="0"),
    )
    op.create_index("ix_bw_timestamp", "bandwidth_samples", ["timestamp"])
    op.create_index("ix_bw_source_ts", "bandwidth_samples", ["source_type", "source_id", "timestamp"])
    op.create_index("ix_bw_source_iface", "bandwidth_samples", ["source_type", "source_id", "interface_name"])


def downgrade():
    op.drop_index("ix_bw_source_iface", "bandwidth_samples")
    op.drop_index("ix_bw_source_ts", "bandwidth_samples")
    op.drop_index("ix_bw_timestamp", "bandwidth_samples")
    op.drop_table("bandwidth_samples")

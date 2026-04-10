"""Add cluster_group column to integration_configs.

Used to mark multiple integrations as members of the same logical source
(e.g. two Proxmox nodes in a cluster, two UniFi controllers behind an LB).
The scheduler picks one as the active "primary" each cycle and routes all
writes through it; the others poll for failover but skip duplicate writes.

Revision ID: 027
Revises: 026
"""
revision = "027"
down_revision = "026"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "integration_configs",
        sa.Column("cluster_group", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_integration_configs_cluster_group",
        "integration_configs",
        ["cluster_group"],
    )


def downgrade():
    op.drop_index("ix_integration_configs_cluster_group", table_name="integration_configs")
    op.drop_column("integration_configs", "cluster_group")

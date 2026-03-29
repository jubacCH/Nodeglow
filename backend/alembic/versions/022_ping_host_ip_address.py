"""Add ip_address to ping_hosts and migrate IPs from hostname.

Revision ID: 022
Revises: 021
"""
revision = "022"
down_revision = "021"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("ping_hosts", sa.Column("ip_address", sa.String(), nullable=True))

    # Migrate: if hostname looks like an IP, move it to ip_address
    # and try to set hostname to the name (which is usually the friendly name)
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE ping_hosts
        SET ip_address = hostname
        WHERE hostname ~ '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$'
    """))


def downgrade():
    op.drop_column("ping_hosts", "ip_address")

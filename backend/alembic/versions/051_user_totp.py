"""users: two-factor sign-in (TOTP).

``totp_secret`` is the shared secret, encrypted with the installation key.
It is set when a user starts the setup and only counts once
``totp_enabled_at`` is set (after the first valid code). ``totp_last_counter``
is the last time step a code was accepted for, so a code cannot be replayed.
``totp_recovery`` holds the HMACs of the unused recovery codes.

Revision ID: 051
Revises: 050
"""
import sqlalchemy as sa
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("totp_last_counter", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("totp_recovery", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "totp_recovery")
    op.drop_column("users", "totp_last_counter")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret")

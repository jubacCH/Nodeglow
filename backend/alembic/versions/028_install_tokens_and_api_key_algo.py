"""Add agent_install_tokens table and hash_algo column on api_keys.

- agent_install_tokens: per-issue enrollment credentials replacing the
  shared agent_enrollment_key global setting.
- api_keys.hash_algo: tracks whether a key hash uses modern HMAC-SHA256
  or legacy plain SHA256. New/migrated keys are tagged 'hmac'; existing
  rows remain NULL until they are touched, and a scheduled cleanup job
  disables untouched NULL-algo keys older than 30 days.

Revision ID: 028
Revises: 027
"""
revision = "028"
down_revision = "027"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "agent_install_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("hostname_pattern", sa.String(length=256), nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("used_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_install_tokens_token_hash",
        "agent_install_tokens",
        ["token_hash"],
        unique=True,
    )

    op.add_column(
        "api_keys",
        sa.Column("hash_algo", sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column("api_keys", "hash_algo")
    op.drop_index("ix_agent_install_tokens_token_hash", table_name="agent_install_tokens")
    op.drop_table("agent_install_tokens")

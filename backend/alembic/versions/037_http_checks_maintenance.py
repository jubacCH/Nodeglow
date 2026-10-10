"""HTTP check options, check failure reasons and maintenance windows.

``ping_hosts.http_options`` holds per-host options for http/https checks
(method, path/URL, expected status codes, keywords, timeout, redirects, TLS
verification) as JSON. NULL means the defaults, which reproduce how HTTP
checks behaved before — nothing changes on upgrade.

``ping_hosts.check_errors`` holds why the failed checks of the last cycle
failed ({"https": "status 503"}), so the UI and incidents can say more than
"HTTPS failed".

``maintenance_windows`` holds recurring (weekly) and one-off windows that put
hosts into maintenance without touching the per-host flag.

Revision ID: 037_http_checks_maintenance
Revises: 034
"""
import sqlalchemy as sa
from alembic import op

revision = "037_http_checks_maintenance"
down_revision = "034"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_table(table: str) -> bool:
    return table in _insp().get_table_names()


def upgrade() -> None:
    if not _has_column("ping_hosts", "http_options"):
        op.add_column("ping_hosts", sa.Column("http_options", sa.Text(), nullable=True))
    if not _has_column("ping_hosts", "check_errors"):
        op.add_column("ping_hosts", sa.Column("check_errors", sa.Text(), nullable=True))

    if not _has_table("maintenance_windows"):
        op.create_table(
            "maintenance_windows",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("kind", sa.String(16), nullable=False, server_default="weekly"),
            sa.Column("weekdays", sa.String(32), nullable=True),
            sa.Column("start_time", sa.String(5), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("all_hosts", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("host_ids", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_table("maintenance_windows"):
        op.drop_table("maintenance_windows")
    if _has_column("ping_hosts", "check_errors"):
        op.drop_column("ping_hosts", "check_errors")
    if _has_column("ping_hosts", "http_options"):
        op.drop_column("ping_hosts", "http_options")

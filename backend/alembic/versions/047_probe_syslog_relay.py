"""Probe syslog relay (multi-tenancy phase 10).

A probe can listen for syslog inside the customer network and forward it over
its own agent token, so the message lands in the probe's tenant and site
without the backend ever guessing from a source address. Three columns on
``agents``:

* ``syslog_relay_enabled`` — the relay is switched on for this probe
  (``false`` for every existing row: nothing changes until an admin enables
  it);
* ``syslog_relay_port`` — UDP/TCP port the probe listens on, NULL = the
  agent's default (514);
* ``syslog_relay_status`` — the relay's last reported counters (JSON).

``agents`` is built by ``create_all`` on fresh installs, hence the
``_has_column`` guards (as in 034/035).

Revision ID: 047
Revises: 046
"""
import sqlalchemy as sa
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column("agents", "syslog_relay_enabled"):
        op.add_column(
            "agents",
            sa.Column("syslog_relay_enabled", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
    if not _has_column("agents", "syslog_relay_port"):
        op.add_column("agents", sa.Column("syslog_relay_port", sa.Integer(), nullable=True))
    if not _has_column("agents", "syslog_relay_status"):
        op.add_column("agents", sa.Column("syslog_relay_status", sa.Text(), nullable=True))


def downgrade() -> None:
    for column in ("syslog_relay_status", "syslog_relay_port", "syslog_relay_enabled"):
        if _has_column("agents", column):
            op.drop_column("agents", column)

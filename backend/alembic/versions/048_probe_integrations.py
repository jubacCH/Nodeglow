"""Probe-side integrations, P1: who collects a config.

See ``docs/specs/2026-10-11-probe-side-integrations.md`` ("Data model").

``integration_configs``:

* ``collector`` — ``'core'`` | ``'probe'``, NOT NULL, default ``'core'``:
  every existing config stays collected by the core, nothing changes on
  upgrade;
* ``probe_id`` — the probe that collects it, a composite tenant FK to
  ``agents`` like ``ping_hosts.probe_id`` (``ON DELETE SET NULL (probe_id)``,
  PostgreSQL only), so a config can never name another tenant's probe;
* ``config_rev`` — bumped on every config change; a result is stored only if
  it was started with the current rev and collector;
* ``CHECK (collector = 'probe' OR probe_id IS NULL)`` and a value check on
  ``collector``.

``snapshots``: ``collected_by`` (probe id, NULL = core; soft reference) and
``collected_at`` (the probe's collection time, clock-corrected).

Both tables are tenant-owned and already under RLS; no new policy. Fresh
installs build them with ``create_all``, hence the guards.

Note for merging: a parallel branch (mobile backend) may also claim revision
048. Whichever lands second renumbers to 049 and chains after the other.

Revision ID: 048
Revises: 047
"""
import sqlalchemy as sa
from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None

FK_NAME = "fk_integration_configs_probe_id"
CHECKS = {
    "ck_integration_configs_collector": "collector IN ('core', 'probe')",
    "ck_integration_configs_probe_collector": "collector = 'probe' OR probe_id IS NULL",
}


def _insp():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in _insp().get_indexes(table))


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _checks(table: str) -> set[str]:
    return {c["name"] for c in _insp().get_check_constraints(table)}


def _fks(table: str) -> set[str]:
    return {fk["name"] for fk in _insp().get_foreign_keys(table)}


def upgrade() -> None:
    if not _has_column("integration_configs", "collector"):
        op.add_column("integration_configs", sa.Column(
            "collector", sa.String(16), nullable=False, server_default="core"))
    if not _has_column("integration_configs", "probe_id"):
        op.add_column("integration_configs", sa.Column("probe_id", sa.Integer(), nullable=True))
    if not _has_column("integration_configs", "config_rev"):
        op.add_column("integration_configs", sa.Column(
            "config_rev", sa.Integer(), nullable=False, server_default="1"))
    if not _has_index("integration_configs", "ix_integration_configs_probe_id"):
        op.create_index("ix_integration_configs_probe_id", "integration_configs", ["probe_id"])

    if _is_pg():
        existing = _checks("integration_configs")
        for name, sql in CHECKS.items():
            if name not in existing:
                op.create_check_constraint(name, "integration_configs", sql)
        if FK_NAME not in _fks("integration_configs"):
            # Column-list SET NULL is PostgreSQL 15+ (as in revision 041).
            op.create_foreign_key(
                FK_NAME, "integration_configs", "agents",
                ["tenant_id", "probe_id"], ["tenant_id", "id"],
                ondelete="SET NULL (probe_id)",
            )

    if not _has_column("snapshots", "collected_by"):
        op.add_column("snapshots", sa.Column("collected_by", sa.Integer(), nullable=True))
    if not _has_column("snapshots", "collected_at"):
        op.add_column("snapshots", sa.Column("collected_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Before 048 every config is collected by the core. Dropping the column
    # under a probe config would hand it to the core silently — targets it
    # was never meant to reach. Move such configs to the server first.
    if _has_column("integration_configs", "collector"):
        probe_configs = op.get_bind().execute(sa.text(
            "SELECT count(*) FROM integration_configs WHERE collector <> 'core'")).scalar()
        if probe_configs:
            raise RuntimeError(
                f"{probe_configs} integration config(s) are collected by a probe; move them "
                "to the server (collector = 'core') before downgrading below 048")

    for column in ("collected_at", "collected_by"):
        if _has_column("snapshots", column):
            op.drop_column("snapshots", column)

    if _is_pg():
        if FK_NAME in _fks("integration_configs"):
            op.drop_constraint(FK_NAME, "integration_configs", type_="foreignkey")
        existing = _checks("integration_configs")
        for name in CHECKS:
            if name in existing:
                op.drop_constraint(name, "integration_configs", type_="check")
    if _has_index("integration_configs", "ix_integration_configs_probe_id"):
        op.drop_index("ix_integration_configs_probe_id", table_name="integration_configs")
    for column in ("config_rev", "probe_id", "collector"):
        if _has_column("integration_configs", column):
            op.drop_column("integration_configs", column)

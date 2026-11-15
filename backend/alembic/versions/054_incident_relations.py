"""Evidence on incidents (concept F, phase 3, PR 1).

See ``docs/specs/2026-11-15-concept-f-phase-3-design.md``.

* ``incident_relations`` (tenant-owned, RLS like every tenant table): how an
  incident is related to something and how that is known — ``confirmed``
  (structural link), ``rule`` (the rule that fired) or ``suspected``. One
  English sentence with the numbers in ``summary``. Rows go with their
  incident (``ON DELETE CASCADE`` on the composite tenant FK).
* ``incidents.cause_kind`` / ``incidents.cause_id``: the one object the
  incident's hosts share. Nullable, no backfill: open incidents get it on the
  next correlation run that refreshes them.

Downgrade: drops the table and the two columns.

Revision ID: 054
Revises: 053
"""
import sqlalchemy as sa
from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None

TABLE = "incident_relations"
CAUSE_COLUMNS = (("cause_kind", sa.String(16)), ("cause_id", sa.Integer()))


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in _inspector().get_columns(table))


def upgrade() -> None:
    for name, type_ in CAUSE_COLUMNS:
        if not _has_column("incidents", name):
            op.add_column("incidents", sa.Column(name, type_, nullable=True))

    if not _inspector().has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("incident_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("subject_kind", sa.String(16), nullable=True),
            sa.Column("subject_id", sa.Integer(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_incident_relations_tenant"),
            sa.ForeignKeyConstraint(["tenant_id", "incident_id"], ["incidents.tenant_id", "incidents.id"],
                                    name="fk_incident_relations_incident", ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "incident_id", "kind", "source", "subject_kind", "subject_id",
                                name="uq_incident_relations_key"),
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        import config
        from services import pg_rls

        pg_rls.apply(bind, [TABLE], enabled=config.RLS_ENABLED, login=pg_rls.app_login())


def downgrade() -> None:
    bind = op.get_bind()
    if _inspector().has_table(TABLE):
        if bind.dialect.name == "postgresql":
            from services import pg_rls

            pg_rls.remove(bind, [TABLE])
        op.drop_table(TABLE)
    for name, _type in reversed(CAUSE_COLUMNS):
        if _has_column("incidents", name):
            op.drop_column("incidents", name)

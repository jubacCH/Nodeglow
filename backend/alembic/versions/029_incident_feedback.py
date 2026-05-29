"""Add operator feedback columns to incidents.

Phase 3 closed feedback loop: operators label incidents 'real' or 'noise'.
'noise' verdicts on learned_precursor incidents feed the precursor template
back into the predictor blacklist. ``precursor_template`` stores that mapping.

Revision ID: 029
Revises: 028
"""
revision = "029"
down_revision = "028"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("incidents", sa.Column("feedback", sa.String(length=16), nullable=True))
    op.add_column("incidents", sa.Column("feedback_at", sa.DateTime, nullable=True))
    op.add_column("incidents", sa.Column("feedback_by", sa.String(length=128), nullable=True))
    op.add_column("incidents", sa.Column("precursor_template", sa.Text, nullable=True))
    op.create_index("ix_incident_feedback", "incidents", ["feedback"])


def downgrade():
    op.drop_index("ix_incident_feedback", table_name="incidents")
    op.drop_column("incidents", "precursor_template")
    op.drop_column("incidents", "feedback_by")
    op.drop_column("incidents", "feedback_at")
    op.drop_column("incidents", "feedback")

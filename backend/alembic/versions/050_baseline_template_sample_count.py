"""host_baselines: the template diversity counts its own samples.

``sample_count`` was shared by two estimators. The diversity pass incremented
it on "host:<id>" rows several times an hour without touching the rate, and
the volume pass used the same number as its n. A slot therefore looked trained
(n >= 3) at a rate of zero, and the first real hour was folded in with weight
0.2: mean 0.2x, deviation 0.4x. Every host-keyed volume baseline sat far below
the real rate and "Log volume anomaly" flapped on ordinary traffic.

Adds ``template_sample_count``, seeded from the shared counter so content
detection keeps working, and starts the volume statistics of the host-keyed
rows over. They cannot be repaired in place; the any-day slot is usable again
after three completed days. Address-keyed rows were never counted by the
diversity pass and keep their statistics.

``host_baselines`` is under forced RLS, so the data step runs in the system
scope. The downgrade drops the column; the reset is not undone.

Revision ID: 050
Revises: 049
"""
import sqlalchemy as sa
from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host_baselines",
        sa.Column("template_sample_count", sa.Integer(), nullable=False, server_default="0"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SELECT set_config('nodeglow.system', 'on', true)"))
    bind.execute(sa.text(
        "UPDATE host_baselines SET template_sample_count = COALESCE(sample_count, 0)"
    ))
    bind.execute(sa.text(
        "UPDATE host_baselines SET avg_rate = 0, std_rate = 0, sample_count = 0 "
        "WHERE host_key LIKE 'host:%'"
    ))


def downgrade() -> None:
    with op.batch_alter_table("host_baselines") as batch_op:
        batch_op.drop_column("template_sample_count")

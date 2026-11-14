"""Single sign-on (ee), the test sign-in: three columns on ``sso_flows``.

* ``redirect_uri`` — sent with the authorization request; the token
  request must repeat it exactly.
* ``config_hash`` — the provider's settings the flow started with; a test
  sign-in counts only for those.
* ``result_json`` — the outcome of a test sign-in (claims and the decision
  preview, never a token), read by the admin who ran it. Flows are deleted an
  hour after they start, and their results with them.

Nullable, no backfill: the table holds only short-lived rows. The downgrade
drops the columns.

Revision ID: 053
Revises: 052
"""
import sqlalchemy as sa
from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None

COLUMNS = (
    ("redirect_uri", sa.String(600)),
    ("config_hash", sa.String(64)),
    ("result_json", sa.Text()),
)


def _has_column(column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns("sso_flows"))


def upgrade() -> None:
    for name, type_ in COLUMNS:
        if not _has_column(name):
            op.add_column("sso_flows", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(COLUMNS):
        if _has_column(name):
            op.drop_column("sso_flows", name)

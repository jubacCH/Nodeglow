"""Speedtest snapshots: server_name is the operator, server_location the place.

Both parsers wrote them the other way round ("City, Country" as the name, the
operator as the location). The parsers are fixed; this swaps the two fields in
the stored speedtest snapshots so the latest one reads right after an upgrade.
Swapping is its own inverse: the downgrade runs the same swap.

Data only, no schema change. ``snapshots`` is under forced RLS, so the swap
runs in the system scope.

Revision ID: 049
Revises: 048
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def _swap(bind) -> None:
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SELECT set_config('nodeglow.system', 'on', true)"))
    rows = bind.execute(sa.text(
        "SELECT id, data_json FROM snapshots WHERE entity_type = 'speedtest' AND data_json IS NOT NULL"
    )).all()
    for snap_id, raw in rows:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict) or "server_name" not in data:
            continue
        data["server_name"], data["server_location"] = data.get("server_location", ""), data["server_name"]
        bind.execute(sa.text("UPDATE snapshots SET data_json = :d WHERE id = :i"),
                     {"d": json.dumps(data), "i": snap_id})


def upgrade() -> None:
    _swap(op.get_bind())


def downgrade() -> None:
    _swap(op.get_bind())

"""Migration 049 swaps the speedtest server fields in stored snapshots."""
import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "049_speedtest_server_fields.py"


def _module():
    spec = importlib.util.spec_from_file_location("m049", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(sa.text("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, entity_type TEXT, data_json TEXT)"))
        c.execute(sa.text("INSERT INTO snapshots VALUES (1, 'speedtest', :a), (2, 'unifi', :b), (3, 'speedtest', NULL)"),
                  {"a": json.dumps({"server_name": "Nidau, Switzerland", "server_location": "Quickline AG"}),
                   "b": json.dumps({"server_name": "x"})})
    return engine


def _read(c, snap_id):
    raw = c.execute(sa.text("SELECT data_json FROM snapshots WHERE id = :i"), {"i": snap_id}).scalar()
    return json.loads(raw) if raw else None


def test_upgrade_swaps_speedtest_fields_only_and_downgrade_restores():
    mod, engine = _module(), _db()
    with engine.begin() as c:
        mod._swap(c)
        assert _read(c, 1) == {"server_name": "Quickline AG", "server_location": "Nidau, Switzerland"}
        assert _read(c, 2) == {"server_name": "x"}
        assert _read(c, 3) is None
        mod._swap(c)
        assert _read(c, 1) == {"server_name": "Nidau, Switzerland", "server_location": "Quickline AG"}

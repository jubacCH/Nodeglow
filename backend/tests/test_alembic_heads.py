"""The migration graph has exactly one head.

Parallel branches each chaining a migration onto the same parent leave
several heads after a merge; ``migrate.py`` upgrades to ``head`` and alembic
then refuses ("Multiple head revisions are present") — the backend container
would not start. This catches a forgotten re-chain in CI instead.
"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parent.parent


def _scripts() -> ScriptDirectory:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_the_migrations_have_a_single_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, f"several alembic heads, re-chain the migrations: {sorted(heads)}"


def test_every_revision_chains_to_the_base():
    scripts = _scripts()
    revisions = list(scripts.walk_revisions())
    bases = [r.revision for r in revisions if r.down_revision is None]
    assert len(bases) == 1, bases


def test_the_tenancy_revisions_are_linear():
    """044 -> 045 (phase 9) -> 046 (tenant keys) -> 047 (phase 10)
    -> 048 (probe-side integrations P1) -> 049 (speedtest server fields)."""
    scripts = _scripts()
    chain = {r: scripts.get_revision(r).down_revision for r in ("045", "046", "047", "048", "049")}
    assert chain == {"045": "044", "046": "045", "047": "046", "048": "047", "049": "048"}
    assert list(scripts.get_heads()) == ["049"]

"""Replay recorded vendor responses into collect() and compare with the
expected snapshot — the same check the probe collector pack runs
(tools/probe-pack/replay.py, imported here by path).

A collector that requests an endpoint or parameter the fixture lacks, stops
requesting one it has, or shapes its data differently fails here. When the
change is intended, regenerate the expected file and review the diff:
tools/probe-pack/README.md ("Fixtures").
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "integrations"
_spec = importlib.util.spec_from_file_location(
    "probe_pack_replay", ROOT / "tools" / "probe-pack" / "replay.py")
replay = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = replay  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(replay)


@pytest.mark.parametrize("name", ["proxmox", "unifi", "synology"])
async def test_collect_replays_fixture(name):
    fixture = replay.load_fixture(FIXTURES / f"{name}.json")
    res = await replay.run_fixture(fixture)

    assert res["ok"], res["problems"]
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert replay.canonical(res["data"]) == replay.canonical(expected)


async def test_unmatched_request_fails_loudly():
    fixture = replay.load_fixture(FIXTURES / "synology.json")
    fixture["requests"] = [r for r in fixture["requests"]
                           if r.get("params", {}).get("api") != "SYNO.System.SystemLoad"]
    res = await replay.run_fixture(fixture)

    assert not res["ok"]
    assert any("SYNO.System.SystemLoad" in p for p in res["problems"])

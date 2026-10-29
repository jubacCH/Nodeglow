"""Parsing the UniFi NAS system payload.

UniFi OS 6 reports no RAID state, and an empty drive bay comes back as a disk
with state "nodisk". Neither is a failure: both used to show up as one (RAIDs
"Down", the empty bay as a disk error).
"""
from integrations.unas import parse_unas_data

GiB = 1024**3

RAW = {
    "ustorage": {
        "disks": [
            {"slot": 1, "serial": "A1", "model": "WDC", "state": "normal", "healthy": "good",
             "size": 932 * GiB, "temperature": 35, "bad_sector": 0},
            {"slot": 2, "serial": "", "model": "", "state": "nodisk", "healthy": "",
             "size": 0, "temperature": None},
        ],
        "space": [],
    },
    "storage": [
        {"id": 1, "type": "raid", "mountPoint": "/volume/a", "size": 100 * GiB, "used": 50 * GiB,
         "raid": {"level": "", "state": ""}},
    ],
}


def test_empty_bay_is_not_a_disk_error():
    data = parse_unas_data(RAW)
    bay = next(d for d in data["disks"] if d["name"] == "Slot 2")
    assert bay["empty"] is True
    assert bay["ok"] is None
    assert data["totals"]["disks_total"] == 1
    assert data["totals"]["disks_ok"] == 1
    assert data["totals"]["disks_error"] == 0


def test_raid_without_state_is_unknown_not_down():
    data = parse_unas_data(RAW)
    assert data["raids"][0]["healthy"] is None
    assert data["totals"]["raids_healthy"] is None


def test_raid_with_state_is_judged():
    raw = {**RAW, "storage": [{**RAW["storage"][0], "raid": {"level": "raid1", "state": "degraded"}}]}
    data = parse_unas_data(raw)
    assert data["raids"][0]["healthy"] is False
    assert data["totals"]["raids_healthy"] == 0

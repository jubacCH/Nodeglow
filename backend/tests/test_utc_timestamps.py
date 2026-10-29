"""API timestamps are UTC with a Z (ratchet).

The schema stores naive UTC. ``dt.isoformat()`` on such a value gives
"2026-10-29T11:14:05", which a browser reads as *local* time. That made every
integration look an hour old and "Stale" (fixed 2026-10-29). Routers format
datetimes with ``services.host_state.iso()``.

The baseline lists the bare ``.isoformat()`` calls that still exist. Some are
dates or aware values, most are the same bug waiting to be noticed. A file may
only go down; a new bare call fails this test.
"""
import re
from pathlib import Path

ROUTERS = Path(__file__).resolve().parent.parent / "routers"
BARE_ISOFORMAT = re.compile(r"\.isoformat\(\)(?!\s*\+\s*[\"']Z[\"'])")

BASELINE = {
    "agents.py": 5,
    "api_v1.py": 35,
    "dashboard.py": 1,
    "digest.py": 2,
    "ping.py": 1,
    "settings/api_keys.py": 2,
    "settings/general.py": 1,
    "settings/notifications.py": 1,
    "snmp.py": 2,
    "syslog.py": 2,
    "users.py": 1,
}


def _counts() -> dict[str, int]:
    out = {}
    for path in sorted(ROUTERS.rglob("*.py")):
        n = len(BARE_ISOFORMAT.findall(path.read_text(encoding="utf-8")))
        if n:
            out[path.relative_to(ROUTERS).as_posix()] = n
    return out


def test_no_new_bare_isoformat_in_routers():
    grown = {f: n for f, n in _counts().items() if n > BASELINE.get(f, 0)}
    assert not grown, (
        f"New bare .isoformat() in {grown}: use services.host_state.iso() so the "
        "browser reads the time as UTC, not as local time."
    )


def test_the_baseline_only_goes_down():
    """Lower the baseline when a file is fixed, so it cannot creep back."""
    counts = _counts()
    stale = {f: (n, counts.get(f, 0)) for f, n in BASELINE.items() if counts.get(f, 0) < n}
    assert not stale, f"Lower BASELINE to the current counts (baseline, now): {stale}"

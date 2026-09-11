"""The per-source quota that keeps one noisy source from crowding out others.

Syslog can supply the full limit on its own, and its rows are the newest, so a
plain "merge, sort, truncate" handed it every slot — the incidents and config
changes a year-long window is opened for never survived the cut.
"""
from routers.api_v1 import _apply_source_quota


def _events(kind: str, n: int, start: int = 0):
    """n events of one type, newest first, with sortable timestamps."""
    return [
        {"type": kind, "ts": f"2026-09-{30 - (start + i) % 29:02d}T00:00:00"}
        for i in range(n)
    ]


def _by_type(events):
    out = {}
    for e in events:
        out[e["type"]] = out.get(e["type"], 0) + 1
    return out


def test_everything_fits_under_the_limit():
    buckets = {"status": _events("status", 5), "incident": _events("incident", 3)}
    assert len(_apply_source_quota(buckets, 300)) == 8


def test_noisy_source_cannot_crowd_out_the_others():
    """The production shape: syslog alone exceeds the limit."""
    buckets = {
        "syslog": _events("syslog", 300),
        "incident": _events("incident", 20),
        "change": _events("change", 1),
        "status": _events("status", 7),
    }
    got = _by_type(_apply_source_quota(buckets, 300))
    assert got["incident"] == 20, "incidents must survive a flood of syslog"
    assert got["change"] == 1
    assert got["status"] == 7
    assert sum(got.values()) == 300, "the limit should still be used in full"


def test_unused_share_is_handed_to_sources_that_need_it():
    """A lone source still gets the whole budget — no artificial scarcity."""
    buckets = {"syslog": _events("syslog", 500)}
    assert len(_apply_source_quota(buckets, 300)) == 300


def test_leftovers_go_to_whoever_has_more():
    """Two sources, one small: the big one absorbs the unused slots."""
    buckets = {"syslog": _events("syslog", 500), "change": _events("change", 2)}
    got = _by_type(_apply_source_quota(buckets, 100))
    assert got["change"] == 2
    assert got["syslog"] == 98


def test_empty_input():
    assert _apply_source_quota({}, 300) == []

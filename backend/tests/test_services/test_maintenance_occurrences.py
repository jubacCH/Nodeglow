"""Occurrences of maintenance windows that start inside a range."""
from datetime import datetime, time

from services.maintenance import WindowSpec, occurrences_between


def test_weekly_occurrences_in_local_time():
    # Sundays 02:00 Europe/Zurich, 2 h. Oct 2026: CEST (UTC+2) until the 25th.
    w = WindowSpec(id=1, name="patch", kind="weekly", weekdays=frozenset({6}),
                   start_time=time(2, 0), duration_minutes=120, tz="Europe/Zurich")
    occ = occurrences_between(w, datetime(2026, 10, 1), datetime(2026, 11, 2))
    starts = [s for s, _ in occ]
    assert starts[0] == datetime(2026, 10, 4, 0, 0)
    # 25 Oct: 02:00 happens twice; the first (still CEST) is used.
    assert datetime(2026, 10, 25, 0, 0) in starts
    assert datetime(2026, 11, 1, 1, 0) in starts  # after the switch: UTC+1
    assert len(starts) == 5


def test_once_window_and_range_edges():
    w = WindowSpec(id=2, name="fw", kind="once", starts_at=datetime(2026, 10, 10, 14),
                   ends_at=datetime(2026, 10, 10, 15))
    assert occurrences_between(w, datetime(2026, 10, 10, 13), datetime(2026, 10, 10, 15)) == [
        (datetime(2026, 10, 10, 14), datetime(2026, 10, 10, 15))]
    assert occurrences_between(w, datetime(2026, 10, 10, 14, 1), datetime(2026, 10, 11)) == []


def test_disabled_and_empty_range():
    w = WindowSpec(id=3, name="x", kind="once", enabled=False,
                   starts_at=datetime(2026, 10, 10), ends_at=datetime(2026, 10, 11))
    assert occurrences_between(w, datetime(2026, 1, 1), datetime(2027, 1, 1)) == []
    w2 = WindowSpec(id=4, name="y", kind="once", starts_at=datetime(2026, 10, 10),
                    ends_at=datetime(2026, 10, 11))
    assert occurrences_between(w2, datetime(2026, 10, 12), datetime(2026, 10, 11)) == []

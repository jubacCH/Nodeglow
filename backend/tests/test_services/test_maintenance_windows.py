"""Maintenance windows: schedule evaluation incl. time zones, DST and overnight."""
from datetime import datetime, time

import pytest

from services import maintenance as m


class Host:
    def __init__(self, host_id=1, maintenance=False, maintenance_until=None):
        self.id = host_id
        self.maintenance = maintenance
        self.maintenance_until = maintenance_until


def weekly(days, start, minutes, tz="UTC", **kw):
    return m.WindowSpec(id=kw.pop("id", 1), name="w", kind="weekly", weekdays=frozenset(days),
                        start_time=start, duration_minutes=minutes, tz=tz,
                        all_hosts=kw.pop("all_hosts", True), **kw)


SUN, FRI = 6, 4


# ── weekly, plain ────────────────────────────────────────────────────────────

def test_weekly_window_in_utc():
    w = weekly([SUN], time(2, 0), 120)
    assert not m.is_window_active(w, datetime(2026, 10, 11, 1, 59))
    assert m.is_window_active(w, datetime(2026, 10, 11, 2, 0))
    assert m.is_window_active(w, datetime(2026, 10, 11, 3, 59))
    assert not m.is_window_active(w, datetime(2026, 10, 11, 4, 0))   # end is exclusive
    assert not m.is_window_active(w, datetime(2026, 10, 12, 2, 30))  # Monday


def test_overnight_window_spans_into_next_day():
    # Friday 22:00 for 4h → until Saturday 02:00. Saturday is not a listed day,
    # the occurrence started Friday and must still count.
    w = weekly([FRI], time(22, 0), 240)
    assert m.is_window_active(w, datetime(2026, 10, 9, 23, 0))   # Fri
    assert m.is_window_active(w, datetime(2026, 10, 10, 1, 59))  # Sat
    assert not m.is_window_active(w, datetime(2026, 10, 10, 2, 0))
    assert not m.is_window_active(w, datetime(2026, 10, 9, 21, 59))


def test_multi_day_window():
    w = weekly([FRI], time(18, 0), 3 * 24 * 60)   # Fri 18:00 → Mon 18:00
    assert m.is_window_active(w, datetime(2026, 10, 12, 17, 0))
    assert not m.is_window_active(w, datetime(2026, 10, 12, 18, 0))


def test_window_in_local_time_zone():
    # 02:00 Zurich in October (CEST, UTC+2) is 00:00 UTC.
    w = weekly([SUN], time(2, 0), 60, tz="Europe/Zurich")
    assert m.is_window_active(w, datetime(2026, 10, 11, 0, 30))
    assert not m.is_window_active(w, datetime(2026, 10, 11, 2, 30))


def test_local_day_decides_not_utc_day():
    # Monday 01:00 in Tokyo is Sunday 16:00 UTC.
    w = weekly([0], time(1, 0), 60, tz="Asia/Tokyo")
    assert m.is_window_active(w, datetime(2026, 10, 11, 16, 30))  # Sunday in UTC
    assert not m.is_window_active(w, datetime(2026, 10, 12, 16, 30))


# ── DST ──────────────────────────────────────────────────────────────────────

def test_wall_clock_start_survives_dst_change():
    w = weekly([SUN], time(2, 0), 120, tz="Europe/Zurich")
    # Before the switch (CET, UTC+1): 02:00 local = 01:00 UTC.
    assert m.is_window_active(w, datetime(2026, 3, 22, 1, 0))
    assert not m.is_window_active(w, datetime(2026, 3, 22, 0, 30))
    # After the switch (CEST, UTC+2): 02:00 local = 00:00 UTC.
    assert m.is_window_active(w, datetime(2026, 4, 5, 0, 0))
    assert not m.is_window_active(w, datetime(2026, 4, 5, 2, 0))


def test_nonexistent_start_on_spring_forward_day():
    # 2026-03-29 clocks jump 02:00 → 03:00 in Zurich; 02:30 does not exist.
    # It resolves to the instant 01:30 UTC (= 03:30 CEST) and lasts 60 real minutes.
    w = weekly([SUN], time(2, 30), 60, tz="Europe/Zurich")
    assert not m.is_window_active(w, datetime(2026, 3, 29, 1, 15))
    assert m.is_window_active(w, datetime(2026, 3, 29, 1, 45))
    assert not m.is_window_active(w, datetime(2026, 3, 29, 2, 30))


def test_ambiguous_start_on_fall_back_day_uses_first_occurrence():
    # 2026-10-25 02:30 happens twice in Zurich; the first (CEST) is 00:30 UTC.
    w = weekly([SUN], time(2, 30), 30, tz="Europe/Zurich")
    assert m.is_window_active(w, datetime(2026, 10, 25, 0, 45))
    assert not m.is_window_active(w, datetime(2026, 10, 25, 1, 45))


def test_duration_is_absolute_across_dst():
    # Sat 23:00 local for 4h, the night clocks go back: ends 03:00 *real* hours
    # later = 02:00 local CET = 01:00 UTC.
    w = weekly([5], time(23, 0), 240, tz="Europe/Zurich")
    # 23:00 CEST on Oct 24 = 21:00 UTC; +4h = 01:00 UTC Oct 25.
    assert m.is_window_active(w, datetime(2026, 10, 25, 0, 59))
    assert not m.is_window_active(w, datetime(2026, 10, 25, 1, 0))


# ── one-off ──────────────────────────────────────────────────────────────────

def test_one_off_window():
    w = m.WindowSpec(id=1, name="upgrade", kind="once", all_hosts=True,
                     starts_at=datetime(2026, 10, 10, 20, 0), ends_at=datetime(2026, 10, 10, 22, 0))
    assert not m.is_window_active(w, datetime(2026, 10, 10, 19, 59))
    assert m.is_window_active(w, datetime(2026, 10, 10, 20, 0))
    assert not m.is_window_active(w, datetime(2026, 10, 10, 22, 0))


def test_to_naive_utc_reads_naive_values_as_window_local():
    assert m.to_naive_utc("2026-07-01T10:00", "Europe/Zurich") == datetime(2026, 7, 1, 8, 0)
    assert m.to_naive_utc("2026-07-01T10:00Z", "Europe/Zurich") == datetime(2026, 7, 1, 10, 0)
    assert m.to_naive_utc("2026-01-01T10:00+01:00", "UTC") == datetime(2026, 1, 1, 9, 0)
    with pytest.raises(ValueError):
        m.to_naive_utc("tomorrow", "UTC")


# ── next occurrence ──────────────────────────────────────────────────────────

def test_next_occurrence():
    w = weekly([SUN], time(2, 0), 60, tz="Europe/Zurich")
    start, end = m.next_occurrence(w, datetime(2026, 10, 10, 12, 0))
    assert start == datetime(2026, 10, 11, 0, 0) and end == datetime(2026, 10, 11, 1, 0)
    once = m.WindowSpec(id=2, name="x", kind="once", starts_at=datetime(2026, 1, 1),
                        ends_at=datetime(2026, 1, 2))
    assert m.next_occurrence(once, datetime(2026, 6, 1)) is None


# ── hosts ────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 11, 2, 30)   # inside the Sunday 02:00 UTC window


def test_scope_explicit_hosts():
    w = weekly([SUN], time(2, 0), 60, all_hosts=False, host_ids=frozenset({7}))
    assert m.is_in_maintenance(Host(7), NOW, [w])
    assert not m.is_in_maintenance(Host(8), NOW, [w])
    assert m.active_window_for(Host(7), NOW, [w]) is w


def test_scope_all_hosts():
    w = weekly([SUN], time(2, 0), 60)
    assert m.is_in_maintenance(Host(123), NOW, [w])


def test_disabled_window_has_no_effect():
    w = weekly([SUN], time(2, 0), 60, enabled=False)
    assert not m.is_in_maintenance(Host(1), NOW, [w])


def test_manual_flag_still_works():
    assert m.is_in_maintenance(Host(maintenance=True), NOW)
    assert m.is_in_maintenance(Host(maintenance=True, maintenance_until=datetime(2026, 10, 11, 3)), NOW)
    # An end that has passed no longer counts, even before the job clears the flag.
    assert not m.is_in_maintenance(Host(maintenance=True, maintenance_until=datetime(2026, 10, 11, 2)), NOW)
    assert not m.is_in_maintenance(Host(), NOW)


def test_api_fields():
    w = weekly([SUN], time(2, 0), 60, all_hosts=False, host_ids=frozenset({1}))
    f = m.api_fields(Host(1), NOW, [w])
    assert f == {"maintenance": True, "maintenance_manual": False,
                 "maintenance_window": {"id": 1, "name": "w", "ends_at": "2026-10-11T03:00:00Z"}}
    assert m.api_fields(Host(2), NOW, [w])["maintenance"] is False


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn,value", [
    (m.parse_weekdays, "7"), (m.parse_weekdays, "mon"),
    (m.parse_start_time, "25:00"), (m.parse_start_time, "2pm"), (m.parse_start_time, "12:5"),
    (m.parse_tz, "Mars/Olympus"), (m.parse_host_ids, "[1, true]"), (m.parse_host_ids, "{}"),
])
def test_parsers_reject(fn, value):
    with pytest.raises(ValueError):
        fn(value)


def test_malformed_row_is_inert():
    class Row:
        id, name, kind, enabled = 1, "bad", "weekly", True
        weekdays, start_time, duration_minutes = "1,9", "02:00", 60
        starts_at = ends_at = None
        timezone, all_hosts, host_ids = "UTC", True, None

    spec = m.spec_from_row(Row())
    assert spec.enabled is False
    assert not m.is_in_maintenance(Host(), NOW, [spec])

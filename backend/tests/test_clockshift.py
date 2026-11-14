"""The clock-ahead run must really be ahead.

``tests/clockshift.py`` moves the clock for the CI job that looks for tests
with an expiry date. If the plugin stopped working, that job would pass for
lack of anything shifted. These tests fail it instead.
"""
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from tests import clockshift

SHIFT = float(os.environ.get("NG_SHIFT_DAYS") or 0)
ahead = pytest.mark.skipif(not SHIFT, reason="only in the clock-ahead run (NG_SHIFT_DAYS)")


def test_without_the_variable_the_clock_is_the_real_one():
    if SHIFT:
        pytest.skip("this is the clock-ahead run")
    assert datetime is clockshift.real_datetime and date is clockshift.real_date
    assert time.time is clockshift.real_time


@ahead
def test_every_clock_the_code_reads_is_ahead():
    offset = timedelta(days=SHIFT)
    slack = timedelta(minutes=5)
    real_now = clockshift.real_datetime.utcnow()
    assert abs(datetime.utcnow() - real_now - offset) < slack
    assert abs(datetime.now(timezone.utc).replace(tzinfo=None) - real_now - offset) < slack
    assert abs(time.time() - clockshift.real_time() - offset.total_seconds()) < slack.total_seconds()
    assert date.today() == (clockshift.real_datetime.now() + offset).date()


@ahead
def test_the_code_under_test_reads_the_shifted_clock():
    """A module that took ``datetime`` before the plugin loaded would not."""
    from services import backup_monitor

    assert backup_monitor.datetime is clockshift.ShiftedDatetime


@ahead
def test_real_datetimes_still_count_as_datetimes():
    """Drivers hand back real ones; isinstance checks in the code must hold."""
    assert isinstance(clockshift.real_datetime(2026, 1, 1), datetime)
    assert isinstance(clockshift.real_date(2026, 1, 1), date)
    assert isinstance(datetime.utcnow() - datetime(2026, 1, 1), timedelta)

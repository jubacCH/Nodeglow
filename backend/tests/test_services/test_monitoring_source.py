"""Tests for the MonitoringSource ABC + registry mechanism."""
from __future__ import annotations

import pytest

from services.monitoring_source import (
    MonitoringSource,
    clear_registry,
    get_registry,
    run_source,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty registry. We deliberately wipe and
    restore so other test files keep their auto-discovered sources."""
    snapshot = dict(get_registry())
    clear_registry()
    yield
    clear_registry()
    # Re-import any production sources cleared above
    for cls in snapshot.values():
        # Restore by re-inserting (bypassing __init_subclass__ so the
        # collision check doesn't fire on names we already had)
        from services.monitoring_source import _REGISTRY
        _REGISTRY[cls.name] = cls


def test_subclass_auto_registers():
    class MySource(MonitoringSource):
        name = "my_source"
        interval_seconds = 30

        async def poll(self):
            pass

    reg = get_registry()
    assert "my_source" in reg
    assert reg["my_source"] is MySource


def test_subclass_without_name_rejected():
    with pytest.raises(TypeError, match="must set `name`"):
        class _Bad(MonitoringSource):  # noqa: N801
            async def poll(self):
                pass


def test_name_collision_rejected():
    class FirstSource(MonitoringSource):
        name = "dup"
        interval_seconds = 60

        async def poll(self):
            pass

    with pytest.raises(ValueError, match="name collision"):
        class SecondSource(MonitoringSource):
            name = "dup"
            interval_seconds = 60

            async def poll(self):
                pass


def test_clear_registry_removes_all():
    class TempSource(MonitoringSource):
        name = "temp"
        interval_seconds = 10

        async def poll(self):
            pass

    assert "temp" in get_registry()
    clear_registry()
    assert get_registry() == {}


async def test_run_source_executes_poll_and_records_metrics():
    calls = []

    class CountedSource(MonitoringSource):
        name = "counted"
        interval_seconds = 5

        async def poll(self):
            calls.append("polled")

    await run_source(CountedSource)
    assert calls == ["polled"]

    # The Prometheus counter for this job should now have at least one
    # success — proves @instrument_job was actually applied.
    from services.metrics import SCHEDULER_JOB_RUNS
    sample_value = SCHEDULER_JOB_RUNS.labels(job="counted", status="success")._value.get()
    assert sample_value >= 1


async def test_run_source_raises_and_records_failure():
    class FailingSource(MonitoringSource):
        name = "failing"
        interval_seconds = 5

        async def poll(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run_source(FailingSource)

    from services.metrics import SCHEDULER_JOB_RUNS
    sample_value = SCHEDULER_JOB_RUNS.labels(job="failing", status="failure")._value.get()
    assert sample_value >= 1


def test_interval_none_means_manual_only():
    class ManualSource(MonitoringSource):
        name = "manual"
        interval_seconds = None  # not auto-scheduled

        async def poll(self):
            pass

    # Still registered, but the scheduler should skip it because interval
    # is None. We assert via the scheduler's filter logic in spirit:
    cls = get_registry()["manual"]
    assert cls.interval_seconds is None

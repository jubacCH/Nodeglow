"""Integration collection runs concurrently and no collector can stall the rest.

Collections used to run one after another with no timeout, so one hanging API
or a minute-long speedtest held up every other integration on the box.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import select

from integrations._base import BaseIntegration, CollectorResult
from models.integration import IntegrationConfig, Snapshot


class _Fast(BaseIntegration):
    name = "fake_fast"
    display_name = "Fast"
    config_fields = []

    async def collect(self) -> CollectorResult:
        return CollectorResult(success=True, data={"ok": 1})


class _Hangs(BaseIntegration):
    name = "fake_hangs"
    display_name = "Hangs"
    config_fields = []
    collect_timeout_seconds = 0.2

    async def collect(self) -> CollectorResult:
        await asyncio.sleep(30)
        return CollectorResult(success=True, data={})


class _Slow(BaseIntegration):
    name = "fake_slow"
    display_name = "Slow"
    config_fields = []
    started: list[float] = []

    async def collect(self) -> CollectorResult:
        _Slow.started.append(time.monotonic())
        await asyncio.sleep(0.3)
        return CollectorResult(success=True, data={})


@pytest.fixture
def fakes():
    from integrations import _registry, get_registry

    get_registry()
    for cls in (_Fast, _Hangs, _Slow):
        _registry[cls.name] = cls
    _Slow.started = []
    yield
    for cls in (_Fast, _Hangs, _Slow):
        _registry.pop(cls.name, None)


async def _cfg(db, type_: str, name: str) -> IntegrationConfig:
    from services.integration import encrypt_config

    cfg = IntegrationConfig(type=type_, name=name, config_json=encrypt_config({}), enabled=True)
    db.add(cfg)
    await db.flush()
    return cfg


async def _latest(db, type_: str, entity_id: int) -> Snapshot | None:
    return (await db.execute(
        select(Snapshot)
        .where(Snapshot.entity_type == type_, Snapshot.entity_id == entity_id)
        .order_by(Snapshot.id.desc()).limit(1)
    )).scalar_one_or_none()


def _session(db):
    @asynccontextmanager
    async def ctx():
        yield db
    return ctx


async def test_hanging_collector_times_out_without_blocking_others(db, fakes):
    import scheduler

    hang = await _cfg(db, "fake_hangs", "hangs")
    fast = await _cfg(db, "fake_fast", "fast")
    await db.commit()

    t0 = time.monotonic()
    with patch.object(scheduler, "AsyncSessionLocal", _session(db)):
        await scheduler.run_integration_checks()
    elapsed = time.monotonic() - t0

    assert elapsed < 5, "a hanging collector held up the cycle"
    snap_fast = await _latest(db, "fake_fast", fast.id)
    snap_hang = await _latest(db, "fake_hangs", hang.id)
    assert snap_fast is not None and snap_fast.ok is True
    assert snap_hang is not None and snap_hang.ok is False
    assert "timed out" in (snap_hang.error or "")
    assert not scheduler._inflight_cfgs


async def test_collections_overlap(db, fakes):
    import scheduler

    for i in range(3):
        await _cfg(db, "fake_slow", f"slow{i}")
    await db.commit()

    t0 = time.monotonic()
    with patch.object(scheduler, "AsyncSessionLocal", _session(db)):
        await scheduler.run_integration_checks()
    elapsed = time.monotonic() - t0

    assert len(_Slow.started) == 3
    # Serially this is >= 0.9 s; concurrently ~0.3 s.
    assert max(_Slow.started) - min(_Slow.started) < 0.2
    assert elapsed < 0.85


def test_collect_timeout_resolution():
    import scheduler

    assert scheduler.collect_timeout_for(_Hangs, "fake_hangs") == 0.2
    assert scheduler.collect_timeout_for(_Fast, "speedtest") == scheduler._TYPE_COLLECT_TIMEOUTS["speedtest"]
    assert scheduler.collect_timeout_for(_Fast, "fake_fast") == scheduler.INTEGRATION_COLLECT_TIMEOUT


def test_scheduler_job_defaults_are_sane():
    import scheduler

    assert scheduler.JOB_DEFAULTS["coalesce"] is True
    assert scheduler.JOB_DEFAULTS["max_instances"] == 1
    assert scheduler.JOB_DEFAULTS["misfire_grace_time"] >= 30

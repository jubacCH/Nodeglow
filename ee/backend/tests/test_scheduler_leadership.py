"""Tests for the enterprise scheduler coordinator (leader election)."""
import asyncio

import pytest

from nodeglow_ee.ha import coordinator as coord_mod
from nodeglow_ee.ha import leader_lock
from nodeglow_ee.ha.coordinator import LeaderElectionCoordinator


class FakeScheduler:
    def __init__(self):
        self.calls = {"resume": 0, "pause": 0, "start": []}

    def resume(self):
        self.calls["resume"] += 1

    def pause(self):
        self.calls["pause"] += 1

    def start(self, paused=False):
        self.calls["start"].append(paused)


@pytest.fixture
def coord():
    c = LeaderElectionCoordinator(instance_id="test:1")
    c._scheduler = FakeScheduler()
    leader_lock.reset()
    yield c
    c.stop()
    leader_lock.reset()


def test_becoming_leader_resumes_once(coord):
    coord.apply_leadership(True)
    assert coord.is_leader is True
    assert coord._scheduler.calls["resume"] == 1
    # Idempotent: staying leader does not resume again.
    coord.apply_leadership(True)
    assert coord._scheduler.calls["resume"] == 1


def test_losing_leadership_pauses_once(coord):
    coord.apply_leadership(True)
    coord.apply_leadership(False)
    assert coord.is_leader is False
    assert coord._scheduler.calls["pause"] == 1
    # Idempotent: staying non-leader does not pause again.
    coord.apply_leadership(False)
    assert coord._scheduler.calls["pause"] == 1


def test_non_leader_from_start_does_nothing(coord):
    coord.apply_leadership(False)
    assert coord._scheduler.calls == {"resume": 0, "pause": 0, "start": []}
    assert coord.is_leader is False


def test_steps_aside_without_redis(coord, monkeypatch):
    from services import shared_state
    monkeypatch.setattr(shared_state, "_redis_url", lambda: "")
    assert coord.wants_control() is False
    monkeypatch.setattr(shared_state, "_redis_url", lambda: "redis://r:6379/0")
    assert coord.wants_control() is True


async def test_start_runs_paused_then_resumes_as_leader(monkeypatch):
    monkeypatch.setattr(coord_mod, "LEADER_RENEW_SECONDS", 0.01)
    leader_lock.reset()
    c = LeaderElectionCoordinator(instance_id="test:2")
    sched = FakeScheduler()
    await c.start(sched)
    try:
        assert sched.calls["start"] == [True]
        for _ in range(50):
            if c.is_leader:
                break
            await asyncio.sleep(0.01)
        assert c.is_leader is True and sched.calls["resume"] == 1
    finally:
        c.stop()
        leader_lock.reset()


async def test_redis_error_keeps_current_state(monkeypatch, coord):
    async def boom(*a, **kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr(leader_lock, "try_acquire_leader", boom)
    monkeypatch.setattr(coord_mod, "LEADER_RENEW_SECONDS", 0.01)
    coord.apply_leadership(True)
    task = asyncio.create_task(coord._loop())
    await asyncio.sleep(0.05)
    task.cancel()
    # Neither paused (split-brain safe for a healthy leader) nor crashed.
    assert coord.is_leader is True and coord._scheduler.calls["pause"] == 0


async def test_core_scheduler_hands_over_to_the_coordinator(monkeypatch):
    """start_scheduler delegates to a registered coordinator that wants control."""
    import scheduler as sched
    from extensions import Registry

    reg = Registry()
    started = []

    class Coord:
        def wants_control(self):
            return True

        async def start(self, s):
            started.append(s)

        def stop(self):
            started.append("stopped")

    reg.set_scheduler_coordinator(Coord())
    monkeypatch.setattr(sched.extensions, "registry", reg)

    async def fake_get_setting(_db, key, default=None):
        return default

    monkeypatch.setattr("database.get_setting", fake_get_setting, raising=False)
    monkeypatch.setattr(sched.scheduler, "start", lambda *a, **kw: started.append("core-start"))
    try:
        await sched.start_scheduler()
    except Exception:
        pass  # SNMP seeding etc. may fail against the test DB; jobs are registered first
    finally:
        sched.scheduler.remove_all_jobs()
    assert started and started[0] is sched.scheduler
    assert "core-start" not in started

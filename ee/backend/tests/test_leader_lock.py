"""Tests for the enterprise scheduler leader lock (in-memory backend, fake clock)."""
import pytest

from nodeglow_ee.ha import leader_lock


@pytest.fixture(autouse=True)
def _fresh():
    leader_lock.reset()
    yield
    leader_lock.reset()


def _clock_at(value):
    """Install a fake clock returning a mutable [value]; return the list."""
    box = [value]
    leader_lock.set_clock(lambda: box[0])
    return box


async def test_single_holder_excludes_others():
    _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    # B cannot take it while A's lease is live.
    assert await leader_lock.try_acquire_leader("sched", "B", 30) is False


async def test_holder_can_renew():
    t = _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    t[0] = 10.0
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    # B still locked out after A renewed.
    assert await leader_lock.try_acquire_leader("sched", "B", 30) is False


async def test_failover_after_expiry():
    t = _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    # A goes away and does not renew; lease expires.
    t[0] = 31.0
    assert await leader_lock.try_acquire_leader("sched", "B", 30) is True
    # A is now the non-leader.
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is False


async def test_release_frees_the_lock():
    _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    await leader_lock.release_leader("sched", "A")
    assert await leader_lock.try_acquire_leader("sched", "B", 30) is True


async def test_release_by_non_holder_is_noop():
    _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("sched", "A", 30) is True
    await leader_lock.release_leader("sched", "B")  # B does not hold it
    # A still holds it.
    assert await leader_lock.try_acquire_leader("sched", "B", 30) is False


async def test_independent_locks_do_not_interfere():
    _clock_at(0.0)
    assert await leader_lock.try_acquire_leader("lock1", "A", 30) is True
    assert await leader_lock.try_acquire_leader("lock2", "B", 30) is True

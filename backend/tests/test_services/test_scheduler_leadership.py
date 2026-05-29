"""Tests for the scheduler leadership resume/pause transitions."""
import pytest

import scheduler


@pytest.fixture(autouse=True)
def _reset_leader_state(monkeypatch):
    calls = {"resume": 0, "pause": 0}
    monkeypatch.setattr(scheduler.scheduler, "resume", lambda: calls.__setitem__("resume", calls["resume"] + 1))
    monkeypatch.setattr(scheduler.scheduler, "pause", lambda: calls.__setitem__("pause", calls["pause"] + 1))
    monkeypatch.setattr(scheduler, "_is_leader", False)
    return calls


def test_becoming_leader_resumes_once(_reset_leader_state):
    scheduler._apply_leadership(True)
    assert scheduler._is_leader is True
    assert _reset_leader_state["resume"] == 1
    # Idempotent: staying leader does not resume again.
    scheduler._apply_leadership(True)
    assert _reset_leader_state["resume"] == 1


def test_losing_leadership_pauses_once(_reset_leader_state):
    scheduler._apply_leadership(True)
    scheduler._apply_leadership(False)
    assert scheduler._is_leader is False
    assert _reset_leader_state["pause"] == 1
    # Idempotent: staying non-leader does not pause again.
    scheduler._apply_leadership(False)
    assert _reset_leader_state["pause"] == 1


def test_non_leader_from_start_does_nothing(_reset_leader_state):
    scheduler._apply_leadership(False)
    assert _reset_leader_state["resume"] == 0
    assert _reset_leader_state["pause"] == 0
    assert scheduler._is_leader is False

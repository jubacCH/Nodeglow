"""The heartbeat to an external monitor: an installation without one, or with
one that rejects the requests, says so.

On the production host the setting was empty for weeks. Nothing in the
product mentioned it: an outage of Nodeglow itself would have gone unseen.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import heartbeat

URL = "https://hc.example.com/ping/secret-uuid"


@pytest.fixture(autouse=True)
def _fresh_state():
    heartbeat.reset()
    yield
    heartbeat.reset()


def _client(answer):
    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            return answer(url)
    return FakeClient


def _rejects(code):
    def answer(url):
        def raise_for_status():
            import httpx
            raise httpx.HTTPStatusError(f"Client error '{code}' for url '{url}'", request=None,
                                        response=SimpleNamespace(status_code=code))
        return SimpleNamespace(raise_for_status=raise_for_status)
    return answer


def test_the_setting_is_unset_off_or_a_url(monkeypatch):
    monkeypatch.delenv(heartbeat.ENV, raising=False)
    assert heartbeat.setting() == ("unset", "")
    monkeypatch.setenv(heartbeat.ENV, "   ")
    assert heartbeat.setting() == ("unset", "")
    for off in ("off", "OFF", "none", " disabled "):
        monkeypatch.setenv(heartbeat.ENV, off)
        assert heartbeat.setting() == ("off", "")
    monkeypatch.setenv(heartbeat.ENV, URL)
    assert heartbeat.setting() == ("set", URL)


def test_no_monitor_is_a_warning_and_off_accepts_it(monkeypatch):
    monkeypatch.delenv(heartbeat.ENV, raising=False)
    [problem] = heartbeat.problems()
    assert problem["key"] == "heartbeat:unset" and problem["severity"] == "warning"
    assert problem["title"] == "Nobody is told when Nodeglow itself stops"
    assert heartbeat.ENV in problem["summary"] and '"off"' in problem["summary"]
    assert heartbeat.status(100.0)["mode"] == "unset"

    monkeypatch.setenv(heartbeat.ENV, "off")
    assert heartbeat.problems() == []
    assert heartbeat.status(100.0)["mode"] == "off"


async def test_an_answered_request_is_recorded(monkeypatch):
    import scheduler

    monkeypatch.setenv(heartbeat.ENV, URL)
    monkeypatch.setattr(scheduler, "_postgres_answers", AsyncMock(return_value=True))
    monkeypatch.setattr("httpx.AsyncClient",
                        _client(lambda url: SimpleNamespace(raise_for_status=lambda: None)))
    assert heartbeat.status(0.0)["last_ok_seconds_ago"] is None

    await scheduler.send_heartbeat()
    import time
    state = heartbeat.status(time.time() + 30)
    assert state["mode"] == "set" and state["failures_in_a_row"] == 0
    assert 29 <= state["last_ok_seconds_ago"] <= 32
    assert heartbeat.problems() == []


async def test_a_monitor_that_rejects_the_requests_is_a_critical_problem(monkeypatch, caplog):
    """A deleted check answers 404 for ever. The monitor then no longer
    watches, and Nodeglow went on requesting it without a word."""
    import scheduler

    monkeypatch.setenv(heartbeat.ENV, URL)
    monkeypatch.setattr(scheduler, "_postgres_answers", AsyncMock(return_value=True))
    monkeypatch.setattr("httpx.AsyncClient", _client(_rejects(404)))

    for _ in range(heartbeat.FAILURES_IN_A_ROW - 1):
        await scheduler.send_heartbeat()
    assert heartbeat.problems() == []      # a short fault is not an incident

    with caplog.at_level("WARNING"):
        await scheduler.send_heartbeat()
    [problem] = heartbeat.problems()
    assert problem["key"] == "heartbeat:failing" and problem["severity"] == "critical"
    assert "HTTP 404" in problem["summary"] and "5 requests" in problem["summary"]
    # The check URL is the credential of the monitor: not in the incident,
    # not in the status, not in the log.
    assert "secret-uuid" not in problem["summary"]
    assert "secret-uuid" not in str(heartbeat.status(0.0))
    assert "secret-uuid" not in caplog.text

    monkeypatch.setattr("httpx.AsyncClient",
                        _client(lambda url: SimpleNamespace(raise_for_status=lambda: None)))
    await scheduler.send_heartbeat()
    assert heartbeat.problems() == []


async def test_a_withheld_heartbeat_is_not_a_failure(monkeypatch):
    import scheduler

    monkeypatch.setenv(heartbeat.ENV, URL)
    monkeypatch.setattr(scheduler, "_postgres_answers", AsyncMock(return_value=False))
    for _ in range(10):
        await scheduler.send_heartbeat()
    assert heartbeat.status(0.0)["failures_in_a_row"] == 0


async def test_the_self_check_reports_it_to_the_operator_only(db, monkeypatch):
    from models.tenant import DEFAULT_TENANT_ID
    from services import self_check
    from services.tenancy import tenant_scope

    monkeypatch.delenv(heartbeat.ENV, raising=False)
    with tenant_scope(DEFAULT_TENANT_ID):
        assert [p.key for p in self_check._heartbeat_problems()] == ["heartbeat:unset"]
    with tenant_scope(DEFAULT_TENANT_ID + 1):
        assert self_check._heartbeat_problems() == []

    problems = await self_check.collect_problems(db, SimpleNamespace(get_jobs=lambda: []), 1000.0, 0.0)
    assert "heartbeat:unset" in {p.key for p in problems}

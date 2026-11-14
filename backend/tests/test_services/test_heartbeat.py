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
    # Where to fix it is a place in the product, not a file on the server.
    assert "Settings, System, External monitor" in problem["summary"]
    assert heartbeat.ENV not in problem["summary"]
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
        assert [p.key for p in await self_check._heartbeat_problems(db)] == ["heartbeat:unset"]
    with tenant_scope(DEFAULT_TENANT_ID + 1):
        assert await self_check._heartbeat_problems(db) == []

    problems = await self_check.collect_problems(db, SimpleNamespace(get_jobs=lambda: []), 1000.0, 0.0)
    assert "heartbeat:unset" in {p.key for p in problems}


# ── Set up in the UI ────────────────────────────────────────────────────────


async def test_the_saved_setting_wins_over_the_environment(db, monkeypatch):
    from models.settings import set_setting

    monkeypatch.delenv(heartbeat.ENV, raising=False)
    assert await heartbeat.resolve(db) == ("unset", "", "none")
    monkeypatch.setenv(heartbeat.ENV, "https://env.example.com/ping/1")
    assert await heartbeat.resolve(db) == ("set", "https://env.example.com/ping/1", "environment")

    await set_setting(db, heartbeat.SETTING, URL, encrypted=True)
    assert await heartbeat.resolve(db) == ("set", URL, "setting")
    await set_setting(db, heartbeat.SETTING, "off")
    assert await heartbeat.resolve(db) == ("off", "", "setting")
    # Cleared in the UI: the environment applies again.
    await set_setting(db, heartbeat.SETTING, "")
    assert (await heartbeat.resolve(db))[2] == "environment"


async def test_the_self_check_follows_the_saved_setting(db, monkeypatch):
    from models.settings import set_setting
    from services import self_check

    monkeypatch.delenv(heartbeat.ENV, raising=False)
    assert [p.key for p in await self_check._heartbeat_problems(db)] == ["heartbeat:unset"]
    await set_setting(db, heartbeat.SETTING, "off")
    assert await self_check._heartbeat_problems(db) == []


@pytest.mark.parametrize("url,ok", [
    ("https://hc-ping.com/abc", True),
    ("http://10.20.30.40:3001/api/push/abc", True),      # an Uptime Kuma next door
    ("ftp://hc-ping.com/abc", False),
    ("hc-ping.com/abc", False),
    ("https://", False),
    ("http://169.254.169.254/latest/meta-data", False),  # cloud metadata
    ("http://127.0.0.1:9000/ping", False),               # stops together with Nodeglow
    ("http://0.0.0.0/ping", False),
    ("http://clickhouse:8123/ping", False),              # one of Nodeglow's own services
])
def test_which_urls_can_be_a_monitor(url, ok, monkeypatch):
    import ipaddress

    monkeypatch.setattr("utils.net_safety.resolve_all",
                        lambda host: [ipaddress.ip_address("203.0.113.10")])
    assert (heartbeat.url_problem(url) is None) is ok


async def test_the_settings_endpoints(monkeypatch):
    """Save, read back without the URL, test, switch off, and back to unset."""
    import ipaddress

    from tests.conftest import setting_row
    from tests.test_routers.conftest import make_client

    monkeypatch.delenv(heartbeat.ENV, raising=False)
    monkeypatch.setattr("utils.net_safety.resolve_all",
                        lambda host: [ipaddress.ip_address("203.0.113.10")])
    requested = []

    async def fake_request(url):
        requested.append(url)
        return None if "good" in url else "the monitor answered HTTP 404"
    monkeypatch.setattr(heartbeat, "request", fake_request)

    async with make_client() as (client, sf):
        assert (await client.get("/settings/heartbeat")).json()["mode"] == "unset"
        # Nothing to test, nothing to keep.
        assert (await client.post("/settings/heartbeat/test", json={})).status_code == 400
        assert (await client.post("/settings/heartbeat/save", json={"mode": "url"})).status_code == 400
        refused = await client.post("/settings/heartbeat/save",
                                    json={"mode": "url", "url": "http://169.254.169.254/x"})
        assert refused.status_code == 400 and "metadata" in refused.json()["error"]

        good = "https://hc.example.com/ping/good-secret"
        saved = await client.post("/settings/heartbeat/save", json={"mode": "url", "url": good})
        assert saved.status_code == 200, saved.text
        assert saved.json()["mode"] == "set" and saved.json()["source"] == "setting"
        # The URL is a credential: not in any answer, and encrypted at rest.
        assert "good-secret" not in saved.text
        assert "good-secret" not in (await client.get("/settings/heartbeat")).text
        async with sf() as db:
            row = await setting_row(db, heartbeat.SETTING)
            assert row.encrypted and "good-secret" not in row.value

        tested = await client.post("/settings/heartbeat/test", json={})
        assert tested.json() == {"ok": True, "error": None} and requested == [good]
        assert (await client.get("/settings/heartbeat")).json()["last_ok_seconds_ago"] is not None

        # "url" without a URL keeps the saved one.
        assert (await client.post("/settings/heartbeat/save", json={"mode": "url"})).status_code == 200
        assert (await client.post("/settings/heartbeat/test", json={})).json()["ok"] is True

        bad = await client.post("/settings/heartbeat/save",
                                json={"mode": "url", "url": "https://hc.example.com/ping/gone"})
        # What was counted belongs to the previous target.
        assert bad.json()["last_ok_seconds_ago"] is None
        assert (await client.post("/settings/heartbeat/test", json={})).json() == {
            "ok": False, "error": "the monitor answered HTTP 404"}

        off = await client.post("/settings/heartbeat/save", json={"mode": "off"})
        assert off.json()["mode"] == "off"
        assert (await client.post("/settings/heartbeat/save", json={"mode": "unset"})).json()["mode"] == "unset"
        assert (await client.post("/settings/heartbeat/save", json={"mode": "x"})).status_code == 400


async def test_the_job_requests_the_saved_url(db, monkeypatch):
    """Saved in the UI and nothing in the environment: the job still sends."""
    from contextlib import asynccontextmanager

    import scheduler
    from models.settings import set_setting

    monkeypatch.delenv(heartbeat.ENV, raising=False)
    await set_setting(db, heartbeat.SETTING, URL, encrypted=True)

    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", session)
    monkeypatch.setattr(scheduler, "_postgres_answers", AsyncMock(return_value=True))
    requested = []

    async def fake_request(url):
        requested.append(url)
        return None
    monkeypatch.setattr(heartbeat, "request", fake_request)

    await scheduler.send_heartbeat()
    assert requested == [URL] and heartbeat.status(0.0)["failures_in_a_row"] == 0

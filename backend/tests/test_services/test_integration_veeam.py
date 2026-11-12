"""Veeam Backup & Replication integration: parser, alerts and collect() without a network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import integrations.veeam as veeam
from integrations.veeam import VeeamIntegration, parse_veeam_data

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def ts(hours_ago: float, offset: str = "+01:00", fraction: str = "") -> str:
    """A .NET style timestamp, hours_ago before NOW, expressed in the given offset."""
    sign = 1 if offset[0] == "+" else -1
    off = timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])) * sign
    local = (NOW - timedelta(hours=hours_ago)).astimezone(timezone(off))
    return local.strftime("%Y-%m-%dT%H:%M:%S") + fraction + offset


def job(name, result="Success", status="inactive", last_run=2, **kw):
    d = {"id": f"id-{name}", "name": name, "type": "Backup", "status": status, "lastResult": result,
         "workload": "Vm", "lastRun": ts(last_run) if last_run is not None else None,
         "nextRun": ts(-10), "repositoryId": "r1", "repositoryName": "Default", "objectsCount": 4}
    d.update(kw)
    return d


REPOS = [
    {"id": "r1", "name": "Default Backup Repository", "type": "WinLocal", "hostName": "vbr", "path": "D:\\Backup",
     "capacityGB": 1000.0, "freeGB": 600.0, "usedSpaceGB": 400.0},
]


def parse(jobs, repos=REPOS, sessions=(), server=None, unavailable=None):
    return parse_veeam_data(collected_at=NOW, server=server, jobs=jobs, repositories=repos,
                            sessions=list(sessions) if sessions is not None else None, unavailable=unavailable)


def alerts(data, **overrides):
    inst = VeeamIntegration({})
    inst.threshold_overrides = overrides
    return inst.parse_alerts(data)


# ── Parser ────────────────────────────────────────────────────────────────────


def test_healthy_snapshot():
    d = parse([job("Daily VMs"), job("File backup")],
              server={"name": "vbr", "buildVersion": "12.1.2.172", "vbrId": "x", "patches": ["12.1.2.172"]},
              sessions=[{"id": "s1", "name": "Daily VMs", "sessionType": "Job", "creationTime": ts(2),
                         "endTime": ts(1), "state": "Stopped", "result": {"result": "Success", "message": ""}}])
    assert d["collected_at"] == "2026-11-12T12:00:00+00:00"
    assert d["jobs"][0]["last_run"] == "2026-11-12T10:00:00+00:00"  # +01:00 converted to UTC
    assert d["repositories"][0]["pct"] == 40.0
    assert d["server"]["build"] == "12.1.2.172"
    assert d["sessions"][0]["result"] == "success"
    assert d["totals"] == {"jobs": 2, "jobs_failed": 0, "jobs_warning": 0, "jobs_disabled": 0}
    assert alerts(d) == []


def test_dotnet_seven_digit_fraction_and_z_suffix():
    d = parse([job("A", lastRun="2026-11-12T10:00:00.1234567+00:00", nextRun="2026-11-13T10:00:00Z")])
    assert d["jobs"][0]["last_run"] == "2026-11-12T10:00:00.123456+00:00"
    assert d["jobs"][0]["next_run"] == "2026-11-13T10:00:00+00:00"


def test_timestamp_without_offset_is_missing_not_guessed():
    d = parse([job("A", lastRun="2026-11-01T10:00:00")])
    assert d["jobs"][0]["last_run"] is None
    assert alerts(d) == []


def test_missing_fields_do_not_raise_and_stay_unknown():
    d = parse([{}, {"name": "x"}, "junk"], repos=[{"name": "r"}, {"name": "s", "capacityGB": 0, "freeGB": 0}])
    assert d["jobs"][1]["last_result"] is None and d["jobs"][1]["last_run"] is None
    assert [r["pct"] for r in d["repositories"]] == [None, None]
    assert alerts(d) == []


def test_unfetched_parts_are_none():
    d = parse([job("A")], repos=None, sessions=None, unavailable=["repositories", "sessions"])
    assert d["repositories"] is None and d["sessions"] is None and d["server"] is None
    assert d["unavailable"] == ["repositories", "sessions"]


def test_unknown_result_string_is_none():
    assert parse([job("A", result="Weird")])["jobs"][0]["last_result"] is None


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_failed_and_warning_results():
    d = parse([job("ok"), job("bad", "Failed"), job("meh", "Warning"), job("never", "None", last_run=None)])
    got = {(a.title, a.entity, a.severity) for a in alerts(d)}
    assert got == {("Backup job failed", "bad", "critical"),
                   ("Backup job finished with warnings", "meh", "warning")}
    assert d["totals"]["jobs_failed"] == 1 and d["totals"]["jobs_warning"] == 1


def test_disabled_job_is_not_alerted():
    d = parse([job("old", "Failed", status="disabled", last_run=500)])
    assert alerts(d) == []
    assert d["totals"]["jobs_disabled"] == 1 and d["totals"]["jobs_failed"] == 0


@pytest.mark.parametrize("hours,expect", [(47.9, False), (48, False), (48.01, True), (200, True)])
def test_stale_job_edges(hours, expect):
    a = alerts(parse([job("A", last_run=hours)]))
    assert bool(a) is expect
    if expect:
        assert a[0].title == "Backup job overdue" and a[0].entity == "A"


def test_stale_threshold_zero_disables_and_custom_limit():
    d = parse([job("A", last_run=100)])
    assert alerts(d, job_stale_hours=0) == []
    assert alerts(d, job_stale_hours=120) == []
    assert len(alerts(d, job_stale_hours=99)) == 1


def test_running_job_is_not_stale():
    assert alerts(parse([job("A", status="running", last_run=100)])) == []


def test_failed_and_stale_are_separate_incidents():
    titles = sorted(a.title for a in alerts(parse([job("A", "Failed", last_run=100)])))
    assert titles == ["Backup job failed", "Backup job overdue"]


@pytest.mark.parametrize("free,expected", [(160, None), (150, "warning"), (51, "warning"), (50, "critical")])
def test_repository_threshold_edges(free, expected):
    repo = [{"id": "r", "name": "R", "capacityGB": 1000.0, "freeGB": free, "usedSpaceGB": 1000.0 - free}]
    a = alerts(parse([], repos=repo))
    assert [x.severity for x in a] == ([expected] if expected else [])
    if expected:
        assert a[0].entity == "R"


def test_repository_without_capacity_is_not_alerted():
    assert alerts(parse([], repos=[{"name": "S3", "type": "AmazonS3"}])) == []


def test_alerts_tolerate_garbage():
    inst = VeeamIntegration({})
    for sample in ({}, {"jobs": None}, {"jobs": [None, 3, {}], "repositories": [None, {"pct": "x"}]},
                   {"collected_at": "bad", "jobs": [{"name": "a", "last_run": "bad"}]}):
        assert inst.parse_alerts(sample) == []


# ── collect() through a mocked transport ──────────────────────────────────────


CFG = {"host": "vbr.example.com", "username": "svc", "password": "p4ss", "api_version": "1.2-rev0"}


def install(monkeypatch, routes: dict[str, object], seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path not in routes:
            return httpx.Response(404, json={})
        r = routes[path]
        if isinstance(r, int):
            return httpx.Response(r, json={})
        return httpx.Response(200, json=r)

    def factory(**kwargs):
        for k in ("verify", "cert", "http1", "http2", "limits", "retries", "trust_env"):
            kwargs.pop(k, None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(veeam, "safe_async_client", factory)


def page(items, total=None):
    return {"data": items, "pagination": {"total": len(items) if total is None else total,
                                            "count": len(items), "skip": 0, "limit": 200}}


GOOD = {
    "/api/oauth2/token": {"access_token": "tok123", "token_type": "bearer", "expires_in": 900},
    "/api/oauth2/logout": {},
    "/api/v1/jobs/states": page([job("A"), job("B", "Failed")]),
    "/api/v1/serverInfo": {"name": "vbr", "buildVersion": "12.1", "vbrId": "x", "patches": []},
    "/api/v1/backupInfrastructure/repositories/states": page(REPOS),
    "/api/v1/sessions": page([]),
}


async def test_collect_success_and_headers(monkeypatch):
    seen: list[httpx.Request] = []
    install(monkeypatch, GOOD, seen)
    res = await VeeamIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == []
    assert [j["name"] for j in res.data["jobs"]] == ["A", "B"]
    assert res.data["repositories"][0]["pct"] == 40.0
    assert all(r.headers["x-api-version"] == "1.2-rev0" for r in seen)
    assert all(r.url.port == 9419 for r in seen)
    login = seen[0]
    assert login.url.path == "/api/oauth2/token" and b"grant_type=password" in login.content
    assert "p4ss" not in str(login.url)
    data_calls = [r for r in seen if r.url.path.startswith("/api/v1/")]
    assert all(r.headers["authorization"] == "Bearer tok123" for r in data_calls)
    assert seen[-1].url.path == "/api/oauth2/logout"


async def test_pagination_collects_all_pages(monkeypatch):
    jobs = [job(f"J{i}") for i in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/oauth2/token":
            return httpx.Response(200, json={"access_token": "t"})
        if p == "/api/v1/jobs/states":
            skip = int(request.url.params["skip"])
            chunk = jobs[skip:skip + 2]  # server caps the page at 2
            return httpx.Response(200, json=page(chunk, total=5))
        return httpx.Response(404, json={})

    monkeypatch.setattr(veeam, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await VeeamIntegration(CFG).collect()
    assert res.success and len(res.data["jobs"]) == 5
    assert res.data["unavailable"] == ["repositories", "server info", "sessions"]


@pytest.mark.parametrize("token_status", [400, 401, 500])
async def test_login_failure_is_an_error_without_secrets(monkeypatch, token_status):
    install(monkeypatch, {**GOOD, "/api/oauth2/token": token_status})
    res = await VeeamIntegration(CFG).collect()
    assert res.success is False and str(token_status) in res.error and "p4ss" not in res.error


async def test_login_without_token_is_an_error(monkeypatch):
    install(monkeypatch, {**GOOD, "/api/oauth2/token": {"nope": 1}})
    assert (await VeeamIntegration(CFG).collect()).success is False


@pytest.mark.parametrize("status", [403, 500])
async def test_failing_job_states_is_an_error(monkeypatch, status):
    install(monkeypatch, {**GOOD, "/api/v1/jobs/states": status})
    res = await VeeamIntegration(CFG).collect()
    assert res.success is False and "jobs/states" in res.error


async def test_unreachable_server_is_an_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(veeam, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await VeeamIntegration(CFG).collect()
    assert res.success is False and res.error


async def test_failing_optional_requests_are_unavailable(monkeypatch):
    routes = {**GOOD, "/api/v1/backupInfrastructure/repositories/states": 500, "/api/v1/sessions": 403}
    del routes["/api/v1/serverInfo"]  # 404
    install(monkeypatch, routes)
    res = await VeeamIntegration(CFG).collect()
    assert res.success
    assert res.data["unavailable"] == ["repositories", "server info", "sessions"]
    assert res.data["repositories"] is None and res.data["sessions"] is None and res.data["server"] is None
    # Jobs still alert; the missing repositories do not produce a false "healthy" or alert.
    assert [a.title for a in VeeamIntegration({}).parse_alerts(res.data)] == ["Backup job failed"]


async def test_sessions_answer_without_a_list_is_unavailable_not_empty(monkeypatch):
    install(monkeypatch, {**GOOD, "/api/v1/sessions": {"items": []}})
    res = await VeeamIntegration(CFG).collect()
    assert res.success
    assert res.data["sessions"] is None and res.data["unavailable"] == ["sessions"]

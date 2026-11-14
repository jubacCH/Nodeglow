"""Synology backup integration: parser, alerts and collect() without a network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import integrations.synobackup as sb
from integrations.synobackup import SynobackupIntegration, nas_utc_offset, parse_synobackup_data, to_utc_iso

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now()`` is the day the fixtures were written for.

    ``collect()`` reads the wall clock, and the fixtures carry fixed dates:
    two days after they were written every backup in them was "overdue", and
    tests that expect no alert started to fail on their own.
    """

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)

# The NAS runs on CET (+01:00): its local clock reads 13:00 when NOW is 12:00 UTC.
NAS_TIME = "Thu Nov 12 13:00:00 2026"


def ep(hours_ago: float) -> int:
    return int((NOW - timedelta(hours=hours_ago)).timestamp())


def local(hours_ago: float, fmt: str = "%Y/%m/%d %H:%M") -> str:
    """NAS wall-clock string (CET) for a moment hours_ago before NOW."""
    return (NOW + timedelta(hours=1) - timedelta(hours=hours_ago)).strftime(fmt)


def hb(task_id=1, name="Offsite", result="done", hours_ago=3, state="backupable", **kw):
    d = {"task_id": task_id, "name": name, "state": state, "target_type": "image",
         "last_bkp_result": result, "last_bkp_time": local(hours_ago) if hours_ago is not None else "",
         "next_bkp_time": local(-20)}
    d.update(kw)
    return d


def abb(task_id=10, name="PC fleet", status=2, hours_ago=5, task_status="idle", **kw):
    d = {"task_id": task_id, "task_name": name, "backup_type": 1, "source_type": "pc", "status": task_status,
         "next_trigger_time": ep(-19),
         "last_result": {"status": status, "time_start": ep(hours_ago), "time_end": ep(hours_ago) + 60,
                         "success_count": 3, "warning_count": 0, "error_count": 0}}
    d.update(kw)
    return d


def parse(hyper=None, active=None, nas_time=NAS_TIME, unavailable=None):
    return parse_synobackup_data(collected_at=NOW, nas_time=nas_time, hyper_backup=hyper, active_backup=active,
                                 unavailable=unavailable)


def alerts(data, **overrides):
    inst = SynobackupIntegration({})
    inst.threshold_overrides = overrides
    return inst.parse_alerts(data)


# ── Time handling ─────────────────────────────────────────────────────────────


def test_nas_offset_from_local_clock():
    assert nas_utc_offset(NAS_TIME, NOW) == timedelta(hours=1)
    assert nas_utc_offset("2026-11-12 04:30:00", NOW) == timedelta(hours=-7, minutes=-30)
    assert nas_utc_offset("garbage", NOW) is None
    assert nas_utc_offset(None, NOW) is None
    assert nas_utc_offset("2026-11-14 12:00:00", NOW) is None  # implausible offset


def test_to_utc_iso_formats():
    off = timedelta(hours=1)
    assert to_utc_iso("2026/11/12 03:00", off) == ("2026-11-12T02:00:00+00:00", True)
    assert to_utc_iso("2026-11-12 03:00:30", off) == ("2026-11-12T02:00:30+00:00", True)
    assert to_utc_iso(1794474000, None)[1] is True
    assert to_utc_iso("1794474000", None) == to_utc_iso(1794474000, None)
    assert to_utc_iso(1794474000000, None) == to_utc_iso(1794474000, None)  # milliseconds
    assert to_utc_iso("2026-11-12T03:00:00+01:00", None) == ("2026-11-12T02:00:00+00:00", True)
    assert to_utc_iso("", None) == (None, True) and to_utc_iso(None, None) == (None, True)
    assert to_utc_iso(0, None) == (None, True) and to_utc_iso(-1, None) == (None, True)
    assert to_utc_iso("2026/11/12 03:00", None) == (None, False)  # no clock to place it
    assert to_utc_iso("yesterday-ish", off) == (None, False)


# ── Parser ────────────────────────────────────────────────────────────────────


def test_healthy_both_sources():
    d = parse([hb()], [abb()])
    assert d["sources"] == {"hyper_backup": True, "active_backup": True}
    assert d["unavailable"] == []
    by = {t["source"]: t for t in d["tasks"]}
    assert by["hyper_backup"]["last_result"] == "success"
    assert by["hyper_backup"]["last_run"] == "2026-11-12T09:00:00+00:00"
    assert by["active_backup"]["last_result"] == "success" and by["active_backup"]["counts"]["success_count"] == 3
    assert by["active_backup"]["scheduled"] is True
    assert d["totals"] == {"tasks": 2, "failed": 0, "partial": 0}
    assert alerts(d) == []


def test_empty_source_is_healthy_but_unfetched_source_is_not_listed_as_queried():
    d = parse([], None, unavailable=["Active Backup for Business"])
    assert d["sources"] == {"hyper_backup": True, "active_backup": False}
    assert d["tasks"] == [] and d["unavailable"] == ["Active Backup for Business"]


def test_naive_times_without_a_usable_clock_are_flagged_not_guessed():
    d = parse([hb(hours_ago=100)], nas_time=None)
    t = d["tasks"][0]
    assert t["last_run"] is None and "backup times" in d["unavailable"]
    assert alerts(d) == []  # no age can be computed, so no overdue alert


def test_unrecognised_result_is_unknown_not_success():
    d = parse([hb(result="resuming")])
    t = d["tasks"][0]
    assert t["last_result"] is None and t["last_result_raw"] == "resuming"
    assert alerts(d) == []


def test_missing_fields_do_not_raise():
    d = parse([{}, {"name": "x"}, "junk"], [{}, {"task_name": "y", "last_result": None}, 5])
    assert len(d["tasks"]) == 4
    assert all(t["last_result"] is None and t["last_run"] is None for t in d["tasks"])
    assert alerts(d) == []


@pytest.mark.parametrize("raw,expected", [("done", "success"), ("none", "none"), ("", "none"),
                                          ("failed", "failed"), ("partial_success", "partial"),
                                          ("error", "failed"), ("backingup", None)])
def test_hyper_backup_result_mapping(raw, expected):
    assert parse([hb(result=raw)])["tasks"][0]["last_result"] == expected


@pytest.mark.parametrize("raw,expected", [(2, "success"), (3, "partial"), (4, "failed"), (5, "cancelled"),
                                          (6, "none"), (99, None), (True, None)])
def test_active_backup_result_mapping(raw, expected):
    assert parse(None, [abb(status=raw)])["tasks"][0]["last_result"] == expected


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_failed_and_partial_alerts():
    d = parse([hb(1, "A", "failed"), hb(2, "B", "partial")], [abb(10, "C", 4), abb(11, "D", 3), abb(12, "E", 5)])
    got = {(a.title, a.entity, a.severity) for a in alerts(d)}
    assert got == {
        ("Backup task failed", "Hyper Backup: A", "critical"),
        ("Backup task partially failed", "Hyper Backup: B", "warning"),
        ("Backup task failed", "Active Backup for Business: C", "critical"),
        ("Backup task partially failed", "Active Backup for Business: D", "warning"),
    }
    assert d["totals"]["failed"] == 2 and d["totals"]["partial"] == 2


@pytest.mark.parametrize("hours,expect", [(47.5, False), (48, False), (48.5, True)])
def test_stale_edges_hyper_backup(hours, expect):
    # Hyper Backup strings carry minutes only, so the edge is tested at half-hour steps.
    assert bool(alerts(parse([hb(hours_ago=hours)]))) is expect


@pytest.mark.parametrize("secs,expect", [(0, False), (1, True)])
def test_stale_edge_active_backup_to_the_second(secs, expect):
    a = abb()
    a["last_result"]["time_start"] = ep(48) - secs
    assert bool(alerts(parse(None, [a]))) is expect


def test_stale_zero_disables_and_custom_limit():
    d = parse([hb(hours_ago=100)])
    assert alerts(d, task_stale_hours=0) == []
    assert alerts(d, task_stale_hours=200) == []
    assert [a.title for a in alerts(d, task_stale_hours=99)] == ["Backup task overdue"]


def test_unscheduled_and_running_tasks_are_not_overdue():
    assert alerts(parse(None, [abb(hours_ago=500, task_status="unscheduled")])) == []
    assert alerts(parse(None, [abb(hours_ago=500, task_status="backingup")])) == []
    assert alerts(parse([hb(hours_ago=500, state="backingup")])) == []


def test_never_run_task_is_not_alerted():
    assert alerts(parse([hb(result="none", hours_ago=None)])) == []


def test_alerts_tolerate_garbage():
    inst = SynobackupIntegration({})
    for sample in ({}, {"tasks": None}, {"tasks": [None, 1, {}], "collected_at": "bad"},
                   {"collected_at": "bad", "tasks": [{"name": "a", "last_run": "bad", "last_result": "failed"}]}):
        assert isinstance(inst.parse_alerts(sample), list)
    assert inst.parse_alerts({"tasks": [None, 1, {}]}) == []


# ── collect() through a mocked transport ──────────────────────────────────────

CFG = {"host": "nas.example.com", "port": 5001, "username": "svc", "password": "s3cr3t"}
ALL_APIS = {"SYNO.Backup.Task": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1},
            "SYNO.ActiveBackup.Task": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1}}


def ok(data):
    return {"success": True, "data": data}


def err(code):
    return {"success": False, "error": {"code": code}}


def real_local_now(fmt):
    return (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(fmt)


def hb_item(task_id=1, name="Offsite"):
    return {"task_id": task_id, "name": name, "state": "backupable", "target_type": "image"}


def hb_status(result="done"):
    stamp = (datetime.now(timezone.utc) + timedelta(hours=1) - timedelta(hours=2)).strftime("%Y/%m/%d %H:%M")
    return {"last_bkp_result": result, "last_bkp_time": stamp, "next_bkp_time": stamp}


def install(monkeypatch, answers: dict, seen: list | None = None, login=None):
    """answers: (api, method) -> JSON body, or a callable(request) -> JSON body, or an int HTTP status."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/webapi/auth.cgi":
            if request.method == "POST":
                return httpx.Response(200, json=login if login is not None else ok({"sid": "SID"}))
            return httpx.Response(200, json=ok({}))
        key = (request.url.params.get("api"), request.url.params.get("method"))
        if key not in answers:
            return httpx.Response(200, json=err(103))
        a = answers[key]
        if isinstance(a, int):
            return httpx.Response(a, json={})
        return httpx.Response(200, json=a(request) if callable(a) else a)

    def factory(**kwargs):
        for k in ("verify", "cert", "http1", "http2", "limits", "retries", "trust_env"):
            kwargs.pop(k, None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sb, "safe_async_client", factory)


def base_answers():
    return {
        ("SYNO.API.Info", "query"): ok(ALL_APIS),
        ("SYNO.DSM.Info", "getinfo"): lambda r: ok({"time": real_local_now("%a %b %d %H:%M:%S %Y")}),
        ("SYNO.Backup.Task", "list"): ok({"task_list": [hb_item()]}),
        ("SYNO.Backup.Task", "status"): lambda r: ok(hb_status()),
        ("SYNO.ActiveBackup.Task", "list"): ok({"tasks": [abb()], "total": 1}),
    }


async def test_collect_success_both_sources(monkeypatch):
    seen: list[httpx.Request] = []
    install(monkeypatch, base_answers(), seen)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == []
    by = {t["source"]: t for t in res.data["tasks"]}
    assert by["hyper_backup"]["last_result"] == "success"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(by["hyper_backup"]["last_run"])
    assert timedelta(hours=1, minutes=50) < age < timedelta(hours=2, minutes=10)  # local time converted correctly
    assert by["active_backup"]["last_result"] == "success"
    login = seen[0]
    assert login.method == "POST" and b"s3cr3t" in login.content and "s3cr3t" not in str(login.url)
    status = [r for r in seen if r.url.params.get("method") == "status"][0]
    assert status.url.params["task_id"] == "1" and "last_bkp_result" in status.url.params["additional"]


async def test_login_failure_is_an_error(monkeypatch):
    install(monkeypatch, base_answers(), login=err(400))
    res = await SynobackupIntegration(CFG).collect()
    assert res.success is False and "400" in res.error and "s3cr3t" not in res.error


async def test_api_discovery_failure_is_an_error(monkeypatch):
    answers = base_answers()
    answers[("SYNO.API.Info", "query")] = 500
    install(monkeypatch, answers)
    assert (await SynobackupIntegration(CFG).collect()).success is False


async def test_package_not_installed_goes_to_unavailable(monkeypatch):
    answers = base_answers()
    answers[("SYNO.API.Info", "query")] = ok({"SYNO.Backup.Task": ALL_APIS["SYNO.Backup.Task"]})
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success
    assert res.data["unavailable"] == ["Active Backup for Business"]
    assert res.data["sources"] == {"hyper_backup": True, "active_backup": False}
    assert [t["source"] for t in res.data["tasks"]] == ["hyper_backup"]


async def test_nothing_readable_is_an_error(monkeypatch):
    answers = base_answers()
    answers[("SYNO.API.Info", "query")] = ok({})
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success is False and "Hyper Backup" in res.error and "Active Backup" in res.error


async def test_dsm_error_in_one_source_is_unavailable_other_still_works(monkeypatch):
    answers = base_answers()
    answers[("SYNO.Backup.Task", "list")] = err(105)
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == ["Hyper Backup"]
    assert res.data["sources"]["hyper_backup"] is False
    assert [t["source"] for t in res.data["tasks"]] == ["active_backup"]


async def test_failing_task_status_names_the_task(monkeypatch):
    answers = base_answers()
    answers[("SYNO.Backup.Task", "status")] = err(105)
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == ["Hyper Backup status (Offsite)"]
    t = [x for x in res.data["tasks"] if x["source"] == "hyper_backup"][0]
    assert t["last_result"] is None and t["last_run"] is None


async def test_nas_clock_failure_with_wall_clock_times_is_flagged(monkeypatch):
    answers = base_answers()
    answers[("SYNO.DSM.Info", "getinfo")] = err(105)
    install(monkeypatch, answers)
    # Only here: the other tests derive the NAS clock from the real one.
    monkeypatch.setattr(sb, "datetime", _FrozenDatetime)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == ["backup times"]
    assert [t for t in res.data["tasks"] if t["source"] == "hyper_backup"][0]["last_run"] is None
    assert SynobackupIntegration({}).parse_alerts(res.data) == []


async def test_active_backup_retries_with_filter_on_lost_param(monkeypatch):
    calls = []

    def abb_list(request):
        calls.append(dict(request.url.params))
        return err(114) if "filter" not in request.url.params else ok({"tasks": [abb()]})

    answers = base_answers()
    answers[("SYNO.ActiveBackup.Task", "list")] = abb_list
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == []
    assert len(calls) == 2 and calls[1]["filter"] == "{}"


async def test_unexpected_task_list_shape_is_unavailable_not_empty(monkeypatch):
    answers = base_answers()
    answers[("SYNO.ActiveBackup.Task", "list")] = ok({"something": "else"})
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == ["Active Backup for Business"]
    assert res.data["sources"]["active_backup"] is False


async def test_http_errors_do_not_leak_the_session_id(monkeypatch):
    answers = base_answers()
    answers[("SYNO.Backup.Task", "list")] = 500
    answers[("SYNO.ActiveBackup.Task", "list")] = 502
    install(monkeypatch, answers)
    res = await SynobackupIntegration(CFG).collect()
    assert res.success is False
    assert "SID" not in res.error and "500" in res.error and "502" in res.error

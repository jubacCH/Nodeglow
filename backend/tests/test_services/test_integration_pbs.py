"""Proxmox Backup Server integration: parser, alerts and collect() without a network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import integrations.pbs as pbs
from integrations.pbs import PbsIntegration, parse_pbs_data

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



def ep(hours_ago: float) -> int:
    return int((NOW - timedelta(hours=hours_ago)).timestamp())


USAGE = [
    {"store": "main", "total": 1000, "used": 500, "avail": 500, "backend-type": "filesystem",
     "mount-status": "nonremovable", "estimated-full-date": ep(-24 * 90)},
    {"store": "usb", "backend-type": "filesystem", "mount-status": "notmounted"},
]

GROUPS_MAIN = [
    {"backup-type": "vm", "backup-id": "100", "backup-count": 7, "last-backup": ep(3), "owner": "root@pam", "files": []},
    {"backup-type": "ct", "backup-id": "200", "backup-count": 3, "last-backup": ep(5), "owner": "root@pam"},
]
SNAPS_MAIN = [
    {"backup-type": "vm", "backup-id": "100", "backup-time": ep(3), "verification": {"state": "ok", "upid": "UPID:x"}},
    {"backup-type": "vm", "backup-id": "100", "backup-time": ep(27)},
    {"backup-type": "ct", "backup-id": "200", "backup-time": ep(5)},
]
TASKS = [
    {"upid": "U1", "worker_type": "backup", "worker_id": "main:vm/100", "user": "root@pam",
     "starttime": ep(2), "endtime": ep(2) + 60, "status": "OK"},
    {"upid": "U2", "worker_type": "garbage_collection", "worker_id": "main", "user": "root@pam",
     "starttime": ep(6), "endtime": ep(6) + 60, "status": "TASK ERROR: unable to lock"},
    {"upid": "U3", "worker_type": "syncjob", "worker_id": "main:remote:s", "user": "root@pam",
     "starttime": ep(8), "endtime": ep(8) + 60, "status": "WARNINGS: 2"},
    {"upid": "U4", "worker_type": "backup", "worker_id": "main:ct/200", "user": "root@pam",
     "starttime": ep(10), "endtime": ep(10) + 60, "status": "TASK ERROR: connection reset"},
    {"upid": "U5", "worker_type": "backup", "worker_id": "main:ct/200", "user": "root@pam",
     "starttime": ep(4), "endtime": ep(4) + 60, "status": "OK"},
    {"upid": "U6", "worker_type": "reader", "worker_id": "main:vm/100", "user": "root@pam",
     "starttime": ep(1), "endtime": ep(1) + 5, "status": "TASK ERROR: nope"},
    {"upid": "U7", "worker_type": "backup", "worker_id": "main:vm/101", "user": "root@pam",
     "starttime": ep(0.1)},  # still running
]


def parse(usage=USAGE, groups=None, snaps=None, tasks=TASKS, **kw):
    return parse_pbs_data(
        collected_at=NOW, version={"version": "3.2", "release": "1", "repoid": "abc"},
        node=kw.get("node"), usage=usage,
        groups={"main": GROUPS_MAIN} if groups is None else groups,
        snapshots={"main": SNAPS_MAIN} if snaps is None else snaps,
        tasks=tasks, unavailable=kw.get("unavailable"))


def alerts(data, **overrides):
    inst = PbsIntegration({})
    inst.threshold_overrides = overrides
    return inst.parse_alerts(data)


# ── Parser ────────────────────────────────────────────────────────────────────


def test_healthy_snapshot_shape():
    data = parse(tasks=[TASKS[0]])
    main = data["datastores"][0]
    assert main["name"] == "main" and main["pct"] == 50.0
    assert main["estimated_full"].startswith("2027-02")
    assert data["collected_at"] == "2026-11-12T12:00:00+00:00"
    g = {x["name"]: x for x in main["groups"]}
    assert g["vm/100"]["last_backup"] is not None and g["vm/100"]["verification"] == "ok"
    assert g["ct/200"]["verification"] == "unverified"
    assert main["snapshots"] == {"total": 3, "verified_ok": 1, "verify_failed": 0, "unverified": 2}
    assert data["tasks"]["failed"] == 0
    assert alerts(data) == []


def test_not_mounted_datastore_has_no_numbers_and_no_alert():
    usb = parse()["datastores"][1]
    assert usb["name"] == "usb" and usb["pct"] is None and usb["total"] is None
    assert usb["groups"] is None and usb["snapshots"] is None
    assert [a for a in alerts(parse()) if a.entity == "usb"] == []


def test_avail_minus_one_is_missing():
    d = parse(usage=[{"store": "main", "total": 100, "used": 10, "avail": -1}])
    assert d["datastores"][0]["avail"] is None


def test_tasks_only_relevant_finished_and_superseded_failures():
    t = parse()["tasks"]
    by_id = {i["upid"]: i for i in t["issues"]}
    assert set(by_id) == {"U2", "U3", "U4"}  # reader and running tasks excluded
    assert by_id["U4"]["superseded"] is True  # a later backup of ct/200 succeeded
    assert by_id["U2"]["superseded"] is False
    assert by_id["U3"]["result"] == "warning"
    assert (t["failed"], t["warnings"], t["total"]) == (2, 1, 5)


def test_empty_and_missing_fields_do_not_raise():
    d = parse_pbs_data(collected_at=NOW, version=None, node=None, usage=[{"store": "x"}, "junk", {}],
                       groups={"x": [{"backup-type": "vm"}, 5]}, snapshots={"x": [None, {"backup-id": "1"}]},
                       tasks=[{"worker_type": "backup"}, 7])
    assert d["version"] is None and d["node"] is None
    ds = d["datastores"][0]
    assert ds["pct"] is None and ds["total"] is None
    assert d["tasks"]["total"] == 0
    assert alerts(d) == []


def test_unfetched_parts_are_none_not_empty():
    d = parse(groups={"main": None}, snaps={"main": None}, tasks=None, unavailable=["tasks", "groups (main)"])
    main = d["datastores"][0]
    assert main["groups"] is None and main["snapshots"] is None
    assert d["tasks"] is None
    assert d["unavailable"] == ["groups (main)", "tasks"]
    assert alerts(d) == []


def test_groups_without_snapshots_have_unknown_verification():
    d = parse(snaps={"main": None})
    assert all(g["verification"] is None and g["verify_failed_count"] is None
               for g in d["datastores"][0]["groups"])


# ── Alerts ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("used,expected", [(849, None), (850, "warning"), (949, "warning"), (950, "critical")])
def test_usage_threshold_edges(used, expected):
    d = parse(usage=[{"store": "main", "total": 1000, "used": used, "avail": 1000 - used}], groups={"main": []},
              snaps={"main": []}, tasks=[])
    sev = [a.severity for a in alerts(d)]
    assert sev == ([expected] if expected else [])


def test_datastore_error_alerts():
    d = parse(usage=[{"store": "main", "error": "could not open"}], groups={}, snaps={}, tasks=[])
    a = alerts(d)
    assert len(a) == 1 and a[0].severity == "critical" and a[0].entity == "main"


def test_stale_group_edges():
    groups = [{"backup-type": "vm", "backup-id": "1", "last-backup": ep(48)},
              {"backup-type": "vm", "backup-id": "2", "last-backup": ep(48) - 1},
              {"backup-type": "vm", "backup-id": "3"}]  # never reported a time: no alert
    d = parse(groups={"main": groups}, snaps={"main": []}, tasks=[])
    a = alerts(d)
    assert [x.entity for x in a] == ["main:vm/2"]
    assert alerts(d, stale_hours=0) == []
    assert [x.entity for x in alerts(d, stale_hours=72)] == []


def test_failed_verification_alert():
    snaps = SNAPS_MAIN + [{"backup-type": "ct", "backup-id": "200", "backup-time": ep(30),
                           "verification": {"state": "failed"}}]
    d = parse(snaps={"main": snaps}, tasks=[])
    a = [x for x in alerts(d) if x.title == "Verification failed"]
    assert len(a) == 1 and a[0].entity == "main:ct/200" and a[0].severity == "critical"
    assert d["datastores"][0]["groups"][0]["verification"] in ("ok", "unverified", "failed")


def test_failed_task_alerts_skip_superseded_and_warnings():
    a = alerts(parse())
    task_alerts = [x for x in a if x.title == "PBS task failed"]
    assert [x.entity for x in task_alerts] == ["garbage_collection: main"]


def test_alerts_tolerate_garbage():
    inst = PbsIntegration({})
    for sample in ({}, {"datastores": None}, {"datastores": [None, {}], "tasks": {"issues": [None, 3]}},
                   {"collected_at": "nope", "datastores": [{"groups": [{"last_backup": "bad"}]}]}):
        assert inst.parse_alerts(sample) == []


# ── collect() through a mocked transport ──────────────────────────────────────


def install(monkeypatch, routes: dict[str, object], seen: list | None = None):
    """Route '/path' -> status int or JSON-able data; unknown paths answer 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path.removeprefix("/api2/json")
        if path not in routes:
            return httpx.Response(404, json={"data": None})
        r = routes[path]
        if isinstance(r, int):
            return httpx.Response(r, json={"data": None})
        return httpx.Response(200, json={"data": r})

    def factory(**kwargs):
        for k in ("verify", "cert", "http1", "http2", "limits", "retries", "trust_env"):
            kwargs.pop(k, None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(pbs, "safe_async_client", factory)
    monkeypatch.setattr(pbs, "datetime", _FrozenDatetime)


CFG = {"host": "pbs.example.com", "token_id": "mon@pbs!ng", "token_secret": "s3cr3t"}


async def test_collect_success_sends_token_and_default_port(monkeypatch):
    seen: list[httpx.Request] = []
    install(monkeypatch, {
        "/status/datastore-usage": USAGE[:1], "/version": {"version": "3.2", "release": "1", "repoid": "r"},
        "/nodes/localhost/status": {"uptime": 100, "cpu": 0.1, "memory": {"total": 10, "used": 5}},
        "/nodes/localhost/tasks": [],
        "/admin/datastore/main/namespace": [{"ns": ""}],
        "/admin/datastore/main/groups": GROUPS_MAIN, "/admin/datastore/main/snapshots": SNAPS_MAIN,
    }, seen)
    res = await PbsIntegration(CFG).collect()
    assert res.success and res.data["unavailable"] == []
    assert res.data["tasks"]["total"] == 0 and res.data["node"]["cpu_pct"] == 10.0
    assert all(r.headers["authorization"] == "PBSAPIToken=mon@pbs!ng:s3cr3t" for r in seen)
    assert all(r.url.port == 8007 for r in seen)


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_failing_core_request_is_an_error(monkeypatch, status):
    install(monkeypatch, {"/status/datastore-usage": status, "/version": {"version": "3"}})
    res = await PbsIntegration(CFG).collect()
    assert res.success is False and "s3cr3t" not in res.error and str(status) in res.error


async def test_unreachable_host_is_an_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(pbs, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await PbsIntegration(CFG).collect()
    assert res.success is False and res.error


async def test_failing_optional_requests_are_listed_as_unavailable(monkeypatch):
    install(monkeypatch, {
        "/status/datastore-usage": USAGE[:1],
        "/nodes/localhost/status": 500,  # node status fails
        "/nodes/localhost/tasks": 403,   # tasks fail
        "/admin/datastore/main/namespace": [],
        "/admin/datastore/main/groups": GROUPS_MAIN,
        # snapshots and /version: 404
    })
    res = await PbsIntegration(CFG).collect()
    assert res.success
    assert res.data["unavailable"] == ["node status", "snapshots (main)", "tasks", "version"]
    assert res.data["tasks"] is None and res.data["node"] is None and res.data["version"] is None
    main = res.data["datastores"][0]
    assert main["snapshots"] is None and len(main["groups"]) == 2
    assert PbsIntegration({}).parse_alerts(res.data) == []


def test_totals_are_unknown_when_a_readable_store_lacks_groups_or_snapshots():
    assert parse(snaps={"main": None})["totals"]["verify_failed_groups"] is None
    d = parse(groups={"main": None}, snaps={"main": None})
    assert d["totals"]["groups"] is None and d["totals"]["verify_failed_groups"] is None
    # The unmounted "usb" store has no groups by design and does not count.
    assert parse()["totals"] == {"datastores": 2, "groups": 2, "verify_failed_groups": 0}


def ns_handler(routes: dict, seen: list):
    """Route ('/path', ns) -> data; ns is '' for requests without the parameter."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.url.path.removeprefix("/api2/json"), request.url.params.get("ns", ""))
        if key not in routes:
            return httpx.Response(404, json={"data": None})
        r = routes[key]
        return httpx.Response(r, json={"data": None}) if isinstance(r, int) else httpx.Response(200, json={"data": r})
    return handler


async def test_groups_in_namespaces_are_collected_and_alerted(monkeypatch):
    seen: list[httpx.Request] = []
    old = [{"backup-type": "vm", "backup-id": "100", "last-backup": ep(100)}]
    routes = {
        ("/status/datastore-usage", ""): USAGE[:1],
        ("/admin/datastore/main/namespace", ""): [{"ns": ""}, {"ns": "site-a"}, {"ns": "site-a/sub"}],
        ("/admin/datastore/main/groups", ""): GROUPS_MAIN,
        ("/admin/datastore/main/snapshots", ""): SNAPS_MAIN,
        ("/admin/datastore/main/groups", "site-a"): old,
        ("/admin/datastore/main/snapshots", "site-a"): [
            {"backup-type": "vm", "backup-id": "100", "backup-time": ep(100), "verification": {"state": "failed"}}],
        ("/admin/datastore/main/groups", "site-a/sub"): [],
        ("/admin/datastore/main/snapshots", "site-a/sub"): [],
    }
    handler = ns_handler(routes, seen)
    monkeypatch.setattr(pbs, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await PbsIntegration(CFG).collect()
    assert res.success
    groups = {g["name"]: g for g in res.data["datastores"][0]["groups"]}
    # Same type/id in root and in a namespace are two groups; verification stays per namespace.
    assert groups["vm/100"]["verification"] == "ok"
    assert groups["site-a/vm/100"]["verification"] == "failed"
    assert res.data["totals"]["groups"] == 3
    entities = {(a.title, a.entity) for a in PbsIntegration({}).parse_alerts(res.data)}
    assert ("Backup group overdue", "main:site-a/vm/100") in entities
    assert ("Verification failed", "main:site-a/vm/100") in entities
    assert ("Verification failed", "main:vm/100") not in entities


async def test_failing_namespace_is_unavailable_not_a_partial_list(monkeypatch):
    seen: list[httpx.Request] = []
    routes = {
        ("/status/datastore-usage", ""): USAGE[:1],
        ("/admin/datastore/main/namespace", ""): [{"ns": ""}, {"ns": "site-a"}],
        ("/admin/datastore/main/groups", ""): GROUPS_MAIN,
        ("/admin/datastore/main/snapshots", ""): SNAPS_MAIN,
        ("/admin/datastore/main/groups", "site-a"): 403,
        ("/admin/datastore/main/snapshots", "site-a"): [],
    }
    handler = ns_handler(routes, seen)
    monkeypatch.setattr(pbs, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await PbsIntegration(CFG).collect()
    assert res.success
    assert "groups (main)" in res.data["unavailable"]
    assert res.data["datastores"][0]["groups"] is None
    assert res.data["totals"]["groups"] is None


async def test_namespace_listing_failure_still_reads_the_root(monkeypatch):
    seen: list[httpx.Request] = []
    routes = {
        ("/status/datastore-usage", ""): USAGE[:1],
        ("/admin/datastore/main/namespace", ""): 500,
        ("/admin/datastore/main/groups", ""): GROUPS_MAIN,
        ("/admin/datastore/main/snapshots", ""): SNAPS_MAIN,
    }
    handler = ns_handler(routes, seen)
    monkeypatch.setattr(pbs, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await PbsIntegration(CFG).collect()
    assert "namespaces (main)" in res.data["unavailable"]
    assert len(res.data["datastores"][0]["groups"]) == 2

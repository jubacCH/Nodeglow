"""Maintenance window CRUD and its effect on the host APIs."""
from datetime import datetime, timedelta

from tests.test_routers.conftest import make_client


async def _host(client, name="srv", ip="10.0.0.60"):
    resp = await client.post("/api/v1/hosts", json={"name": name, "hostname": ip})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _now_window(host_ids=None, all_hosts=False, **kw):
    """A one-off window that is active right now."""
    now = datetime.utcnow()
    body = {
        "name": "Patch night", "kind": "once", "timezone": "UTC",
        "starts_at": (now - timedelta(minutes=5)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
        "all_hosts": all_hosts, "host_ids": host_ids or [],
    }
    body.update(kw)
    return body


async def test_crud_lifecycle(client):
    hid = await _host(client)
    resp = await client.post("/api/v1/maintenance-windows", json={
        "name": "Sunday patching", "kind": "weekly", "weekdays": [6],
        "start_time": "02:00", "duration_minutes": 120, "host_ids": [hid],
    })
    assert resp.status_code == 200, resp.text
    w = resp.json()
    assert w["timezone"] == "UTC"  # defaulted from the settings
    assert w["weekdays"] == [6] and w["host_ids"] == [hid]
    assert w["next"] is not None or w["active"]

    listed = (await client.get("/api/v1/maintenance-windows")).json()
    assert [x["id"] for x in listed] == [w["id"]]

    upd = await client.patch(f"/api/v1/maintenance-windows/{w['id']}",
                             json={"start_time": "03:30", "enabled": False})
    assert upd.status_code == 200, upd.text
    assert upd.json()["start_time"] == "03:30"
    assert upd.json()["enabled"] is False
    assert upd.json()["weekdays"] == [6]  # untouched fields survive a PATCH

    assert (await client.get(f"/api/v1/maintenance-windows/{w['id']}")).status_code == 200
    assert (await client.delete(f"/api/v1/maintenance-windows/{w['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/maintenance-windows/{w['id']}")).status_code == 404


async def test_explicit_timezone_is_kept(client):
    resp = await client.post("/api/v1/maintenance-windows", json={
        "name": "local", "kind": "weekly", "weekdays": [0], "start_time": "01:00",
        "duration_minutes": 30, "all_hosts": True, "timezone": "Europe/Zurich",
    })
    assert resp.json()["timezone"] == "Europe/Zurich"


async def test_validation_errors(client):
    hid = await _host(client)
    bad = [
        {"kind": "weekly", "weekdays": [], "start_time": "02:00", "duration_minutes": 60, "all_hosts": True},
        {"kind": "weekly", "weekdays": [1], "start_time": "2am", "duration_minutes": 60, "all_hosts": True},
        {"kind": "weekly", "weekdays": [1], "start_time": "02:00", "duration_minutes": 0, "all_hosts": True},
        {"kind": "weekly", "weekdays": [1], "start_time": "02:00", "duration_minutes": 60},  # no scope
        {"kind": "once", "starts_at": "2026-01-02T00:00", "ends_at": "2026-01-01T00:00", "all_hosts": True},
        {"kind": "once", "all_hosts": True},
        {"kind": "monthly", "all_hosts": True},
        {"kind": "weekly", "weekdays": [1], "start_time": "02:00", "duration_minutes": 60,
         "all_hosts": True, "timezone": "Nowhere/City"},
        {"kind": "weekly", "weekdays": [1], "start_time": "02:00", "duration_minutes": 60,
         "host_ids": [hid, 99999]},
        {"kind": "weekly", "weekdays": [1], "start_time": "02:00", "duration_minutes": 60,
         "all_hosts": True, "cron": "* * * * *"},
    ]
    for body in bad:
        resp = await client.post("/api/v1/maintenance-windows", json={"name": "x", **body})
        assert resp.status_code == 400, (body, resp.text)
    resp = await client.post("/api/v1/maintenance-windows", json={
        "kind": "once", "all_hosts": True,
        "starts_at": "2026-01-01T00:00", "ends_at": "2026-01-01T01:00"})
    assert resp.status_code == 400  # name missing


async def test_active_window_puts_host_into_maintenance(client):
    covered = await _host(client, "covered", "10.0.0.61")
    other = await _host(client, "other", "10.0.0.62")
    resp = await client.post("/api/v1/maintenance-windows", json=_now_window([covered]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True

    hosts = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()}
    assert hosts[covered]["status"] == "maintenance"
    assert hosts[covered]["maintenance"] is True
    assert hosts[covered]["maintenance_manual"] is False
    assert hosts[covered]["maintenance_window"]["name"] == "Patch night"
    assert hosts[other]["maintenance"] is False

    only = (await client.get("/api/v1/hosts?status=maintenance")).json()
    assert [h["id"] for h in only] == [covered]

    detail = (await client.get(f"/api/v1/hosts/{covered}")).json()
    assert detail["maintenance"] is True and detail["health_score"] == 0.5

    status = {h["id"]: h for h in (await client.get("/hosts/api/status")).json()}
    assert status[covered]["maintenance"] is True
    assert status[other]["maintenance"] is False

    topo = {n["id"]: n for n in (await client.get("/api/v1/topology")).json()["nodes"]}
    assert topo[covered]["maintenance"] is True


async def test_window_in_the_future_or_disabled_has_no_effect(client):
    hid = await _host(client, "later", "10.0.0.63")
    now = datetime.utcnow()
    await client.post("/api/v1/maintenance-windows", json=_now_window(
        [hid], starts_at=(now + timedelta(hours=1)).isoformat(),
        ends_at=(now + timedelta(hours=2)).isoformat()))
    await client.post("/api/v1/maintenance-windows", json=_now_window([hid], enabled=False))
    host = (await client.get(f"/api/v1/hosts/{hid}")).json()
    assert host["maintenance"] is False


async def test_all_hosts_scope(client):
    a = await _host(client, "a", "10.0.0.64")
    b = await _host(client, "b", "10.0.0.65")
    await client.post("/api/v1/maintenance-windows", json=_now_window(all_hosts=True))
    hosts = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()}
    assert hosts[a]["maintenance"] and hosts[b]["maintenance"]


async def test_manual_flag_unchanged_by_windows(client):
    hid = await _host(client, "manual", "10.0.0.66")
    resp = await client.post(f"/api/v1/hosts/{hid}/maintenance", json={"action": "toggle"})
    assert resp.json()["maintenance"] is True
    host = (await client.get(f"/api/v1/hosts/{hid}")).json()
    assert host["maintenance"] and host["maintenance_manual"] and host["maintenance_window"] is None


async def test_mutations_need_editor():
    class Viewer:
        id = 2
        username = "viewer"
        role = "readonly"

    async with make_client(fake_user=Viewer()) as (client, _sf):
        assert (await client.get("/api/v1/maintenance-windows")).status_code == 200
        resp = await client.post("/api/v1/maintenance-windows", json=_now_window(all_hosts=True))
        assert resp.status_code == 403
        assert (await client.delete("/api/v1/maintenance-windows/1")).status_code == 403


async def test_correlation_and_ping_job_skip_hosts_in_a_window():
    """The consumers that used to filter on PingHost.maintenance see windows too."""
    from unittest.mock import AsyncMock, patch

    from models.ping import PingHost
    from services import correlation
    from services.maintenance import without_maintenance

    async with make_client() as (client, session_factory):
        covered = await _host(client, "covered", "10.0.0.70")
        free = await _host(client, "free", "10.0.0.71")
        await client.post("/api/v1/maintenance-windows", json=_now_window([covered]))

        async with session_factory() as db:
            hosts = (await db.execute(PingHost.__table__.select())).all()
            assert len(hosts) == 2
            rows = [await db.get(PingHost, covered), await db.get(PingHost, free)]
            assert [h.id for h in await without_maintenance(db, rows)] == [free]

            with patch("services.clickhouse_client.get_offline_hosts_since",
                       new=AsyncMock(return_value=[covered, free])):
                offline = await correlation._get_offline_hosts(db)
            assert [h.id for h in offline] == [free]

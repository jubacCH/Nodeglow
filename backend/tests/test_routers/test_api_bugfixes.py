"""Regressions for two silent API failures found in the UX audit (F-07, F-08).

* ``PATCH /api/v1/hosts/bulk`` was declared after ``PATCH /api/v1/hosts/{host_id}``,
  whose path parameter matches the literal "bulk": every bulk edit got the
  single-host route's 422 and nothing changed.
* ``PATCH /api/v1/agents/{id}`` dropped ``is_probe`` from its allowlist and
  still answered ``ok``, so the probe toggle on /agents reported success
  while the agent never became a probe.
"""
from sqlalchemy import select

from tests.test_routers.conftest import make_client


class EditorUser:
    id = 2
    username = "editor"
    role = "editor"


async def _add_agent(sf, **kw):
    from models.agent import Agent

    async with sf() as db:
        agent = Agent(name=kw.pop("name", "probe-1"), hostname=kw.pop("hostname", "probe-1"),
                      token=kw.pop("token", "tok-1"), **kw)
        db.add(agent)
        await db.commit()
        return agent.id


# ── Bulk edit ────────────────────────────────────────────────────────────────


async def test_bulk_route_is_reachable_and_updates_hosts():
    async with make_client() as (client, sf):
        ids = []
        for i in range(3):
            resp = await client.post("/api/v1/hosts", json={
                "name": f"bulk-{i}", "hostname": f"10.0.9.{i + 1}", "check_type": "icmp",
            })
            ids.append(resp.json()["id"])

        resp = await client.patch("/api/v1/hosts/bulk", json={
            "ids": ids[:2], "updates": {"enabled": False, "latency_threshold_ms": 25},
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["updated"] == 2
        assert body["ids"] == sorted(ids[:2])

        from models.ping import PingHost
        async with sf() as db:
            rows = {h.id: h for h in (await db.execute(select(PingHost))).scalars().all()}
        assert rows[ids[0]].enabled is False and rows[ids[1]].enabled is False
        assert rows[ids[0]].latency_threshold_ms == 25
        assert rows[ids[2]].enabled is True  # untouched


async def test_bulk_reports_missing_ids_and_ignored_fields():
    async with make_client() as (client, _sf):
        resp = await client.post("/api/v1/hosts", json={"name": "b", "hostname": "10.0.9.9"})
        hid = resp.json()["id"]
        resp = await client.patch("/api/v1/hosts/bulk", json={
            "ids": [hid, 99999], "updates": {"maintenance": True, "name": "nope"},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["missing"] == [99999]
        assert body["ignored_fields"] == ["name"]
        detail = (await client.get(f"/api/v1/hosts/{hid}")).json()
        assert detail["maintenance_manual"] is True
        assert detail["name"] == "b"


async def test_bulk_rejects_bad_input():
    async with make_client() as (client, _sf):
        assert (await client.patch("/api/v1/hosts/bulk", json={"ids": [], "updates": {}})).status_code == 400
        assert (await client.patch("/api/v1/hosts/bulk", json={
            "ids": [1], "updates": {"check_type": "smtp"}})).status_code == 400
        assert (await client.patch("/api/v1/hosts/bulk", json={
            "ids": [1], "updates": {"probe_id": 12345}})).status_code == 400


async def test_bulk_is_audited():
    async with make_client() as (client, sf):
        hid = (await client.post("/api/v1/hosts", json={"name": "a", "hostname": "10.0.9.7"})).json()["id"]
        await client.patch("/api/v1/hosts/bulk", json={"ids": [hid], "updates": {"enabled": False}})
        from models.audit import AuditLog
        async with sf() as db:
            actions = [a.action for a in (await db.execute(select(AuditLog))).scalars().all()]
        assert "host.bulk_update" in actions


async def test_single_host_patch_still_works_after_reorder():
    async with make_client() as (client, _sf):
        hid = (await client.post("/api/v1/hosts", json={"name": "s", "hostname": "10.0.9.8"})).json()["id"]
        resp = await client.patch(f"/api/v1/hosts/{hid}", json={"name": "renamed"})
        assert resp.status_code == 200
        assert (await client.get(f"/api/v1/hosts/{hid}")).json()["name"] == "renamed"


# ── Probe toggle ─────────────────────────────────────────────────────────────


async def test_probe_toggle_is_persisted_and_returned():
    async with make_client() as (client, sf):
        agent_id = await _add_agent(sf)

        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"is_probe": True})
        assert resp.status_code == 200
        assert resp.json()["is_probe"] is True

        listed = {a["id"]: a for a in (await client.get("/api/v1/agents")).json()}
        assert listed[agent_id]["is_probe"] is True
        assert listed[agent_id]["probe"]["stale"] is True  # never reported
        assert listed[agent_id]["probe"]["host_count"] == 0

        detail = (await client.get(f"/api/v1/agents/{agent_id}")).json()
        assert detail["is_probe"] is True

        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"is_probe": False})
        assert resp.json()["is_probe"] is False
        listed = {a["id"]: a for a in (await client.get("/api/v1/agents")).json()}
        assert listed[agent_id]["is_probe"] is False
        assert listed[agent_id]["probe"] is None


async def test_probe_toggle_is_audited():
    async with make_client() as (client, sf):
        agent_id = await _add_agent(sf)
        await client.patch(f"/api/v1/agents/{agent_id}", json={"is_probe": True})
        from models.audit import AuditLog
        async with sf() as db:
            rows = (await db.execute(select(AuditLog).where(AuditLog.action == "agent.probe"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].target_id == agent_id


async def test_probe_toggle_requires_admin():
    async with make_client(fake_user=EditorUser()) as (client, sf):
        agent_id = await _add_agent(sf)
        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"is_probe": True})
        assert resp.status_code == 403
        # Editors keep their existing log-settings rights.
        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"agent_log_level": "all"})
        assert resp.status_code == 200
        from models.agent import Agent
        async with sf() as db:
            assert (await db.get(Agent, agent_id)).is_probe is False


async def test_probe_interval_validation():
    async with make_client() as (client, sf):
        agent_id = await _add_agent(sf)
        assert (await client.patch(f"/api/v1/agents/{agent_id}",
                                   json={"probe_interval_seconds": 5})).status_code == 400
        assert (await client.patch(f"/api/v1/agents/{agent_id}",
                                   json={"is_probe": "yes"})).status_code == 400
        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"probe_interval_seconds": 30})
        assert resp.json()["probe_interval_seconds"] == 30

"""Watched services through the HTTP surface: heartbeat, detail, watch list.

The heartbeat must stay backwards compatible — an agent that predates the
feature sends no ``service_states`` and must neither break nor get evaluated.
"""
import json
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.agent import Agent
from models.incident import Incident
from tests.test_routers.conftest import make_client

RAW_TOKEN = "agent-token-for-service-tests"


async def _seed_agent(sf, watched=None):
    from routers.agents import _hash_agent_token
    async with sf() as db:
        agent = Agent(name="web01", hostname="web01", token=_hash_agent_token(RAW_TOKEN),
                      platform="linux", watched_services=json.dumps(watched) if watched else None)
        db.add(agent)
        await db.commit()
        return agent.id


async def _report(client, **extra):
    body = {"hostname": "web01", "platform": "linux", "arch": "x86_64", "cpu_pct": 1.0}
    body.update(extra)
    return await client.post("/api/agent/report", json=body,
                             headers={"Authorization": f"Bearer {RAW_TOKEN}"})


async def _get_agent(sf, agent_id):
    async with sf() as db:
        return await db.get(Agent, agent_id)


async def test_old_agent_without_service_states_is_untouched():
    async with make_client() as (client, sf):
        agent_id = await _seed_agent(sf, watched=["nginx"])
        resp = await _report(client)
        assert resp.status_code == 200, resp.text
        # The list is offered; an old agent simply ignores the extra key.
        assert resp.json()["config"]["watched_services"] == ["nginx"]
        assert (await _get_agent(sf, agent_id)).service_states is None


async def test_agent_without_watch_list_gets_an_empty_list():
    async with make_client() as (client, sf):
        await _seed_agent(sf)
        resp = await _report(client)
        assert resp.status_code == 200
        assert resp.json()["config"]["watched_services"] == []


async def test_reported_states_are_stored_and_raise_an_incident():
    async with make_client() as (client, sf):
        agent_id = await _seed_agent(sf, watched=["nginx"])
        with patch("services.agent_services.send_notifications") as send:
            for _ in range(3):
                resp = await _report(client, service_states=[{"name": "nginx", "state": "stopped",
                                                              "start_type": "enabled"}])
                assert resp.status_code == 200
        sent = [n for call in send.call_args_list for n in call.args[0]]
        assert len(sent) == 1 and "nginx" in sent[0][0]

        async with sf() as db:
            incidents = (await db.execute(select(Incident).where(Incident.rule == "agent_service"))).scalars().all()
        assert len(incidents) == 1 and incidents[0].status == "open"

        detail = (await client.get(f"/api/v1/agents/{agent_id}")).json()
        assert detail["watched_services"] == ["nginx"]
        assert detail["services"][0]["state"] == "stopped"
        assert detail["services"][0]["start_type"] == "enabled"
        assert detail["services"][0]["alerted"] is True
        assert detail["services_reported_at"]


async def test_a_broken_evaluation_does_not_fail_the_heartbeat():
    async with make_client() as (client, sf):
        await _seed_agent(sf, watched=["nginx"])
        with patch("services.agent_services.apply_service_report",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            resp = await _report(client, service_states=[{"name": "nginx", "state": "stopped"}])
        assert resp.status_code == 200


async def test_set_watch_list_validates_and_round_trips():
    async with make_client() as (client, sf):
        agent_id = await _seed_agent(sf)
        resp = await client.put(f"/api/v1/agents/{agent_id}/services",
                                json={"services": [" nginx ", "sshd", "nginx"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["services"] == ["nginx", "sshd"]

        got = (await client.get(f"/api/v1/agents/{agent_id}/services")).json()
        assert got["services"] == ["nginx", "sshd"]
        assert got["reported_at"] is None

        bad = await client.put(f"/api/v1/agents/{agent_id}/services", json={"services": ["a; rm -rf /"]})
        assert bad.status_code == 400
        bad = await client.put(f"/api/v1/agents/{agent_id}/services", json={"services": "nginx"})
        assert bad.status_code == 400

        resp = await client.put(f"/api/v1/agents/{agent_id}/services", json={"services": []})
        assert resp.status_code == 200
        assert (await _get_agent(sf, agent_id)).watched_services is None

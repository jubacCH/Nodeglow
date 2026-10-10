"""Incidents record which hosts they are about (``Incident.host_ids``).

The dedup hash could never be reversed, so no screen could list an incident's
hosts. Every creation path now stores them; NULL stays "not recorded".
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.agent import Agent
from models.incident import Incident
from models.ping import PingHost
from services import agent_services as svc
from services.correlation import _find_or_create_incident, affected_host_ids_json

NOW = datetime(2026, 10, 10, 12, 0, 0)


def test_json_shape():
    assert affected_host_ids_json([7, 3, 3]) == "[3,7]"
    assert affected_host_ids_json([0]) == "[]"
    assert affected_host_ids_json([]) == "[]"
    assert affected_host_ids_json(None) is None


async def test_correlation_incident_stores_its_hosts(db):
    with patch("notifications.notify", new_callable=AsyncMock):
        inc = await _find_or_create_incident(
            db, rule="multi_host_down", title="t", severity="critical",
            host_ids=[5, 2, 9], event_type="host_down", summary="s",
        )
    assert json.loads(inc.host_ids) == [2, 5, 9]


async def test_no_specific_host_marker_is_recorded_as_empty(db):
    with patch("notifications.notify", new_callable=AsyncMock):
        inc = await _find_or_create_incident(
            db, rule="syslog_spike", title="t", severity="warning",
            host_ids=[0], event_type="syslog_error", summary="s",
        )
    assert inc.host_ids == "[]"


async def test_custom_key_hash_without_hosts_records_nothing(db):
    inc = await _find_or_create_incident(
        db, rule="x", title="t", severity="warning", host_ids=[],
        event_type="e", summary="s", key_hash="custom", send_notification=False,
    )
    assert inc.host_ids is None


async def test_existing_incident_from_before_the_column_gets_its_hosts(db):
    with patch("notifications.notify", new_callable=AsyncMock):
        inc = await _find_or_create_incident(
            db, rule="port_error", title="t", severity="warning",
            host_ids=[4], event_type="port_error", summary="s",
        )
        inc.host_ids = None  # as if created before revision 037
        await db.flush()
        again = await _find_or_create_incident(
            db, rule="port_error", title="t", severity="warning",
            host_ids=[4], event_type="port_error", summary="still",
        )
    assert again.id == inc.id
    assert again.host_ids == "[4]"


async def test_agent_service_incident_names_the_agents_ping_host(db):
    agent = Agent(name="web01", hostname="web01", token="t" * 64,
                  watched_services=json.dumps(["nginx"]))
    host = PingHost(name="web01", hostname="web01", source="agent")
    db.add_all([agent, host])
    await db.flush()
    for i in range(3):
        await svc.apply_service_report(db, agent, [{"name": "nginx", "state": "stopped"}], now=NOW)
    await db.flush()
    inc = (await db.execute(select(Incident).where(Incident.rule == "agent_service"))).scalar_one()
    assert json.loads(inc.host_ids) == [host.id]


async def test_silent_probe_problem_carries_its_hosts(db):
    from services.self_check import _probe_problems

    probe = Agent(name="probe-bern-01", hostname="probe-bern-01", token="p" * 64,
                  is_probe=True, last_seen=None)
    db.add(probe)
    await db.flush()
    hosts = [PingHost(name=f"h{i}", hostname=f"10.1.0.{i}", probe_id=probe.id) for i in range(3)]
    db.add_all(hosts)
    await db.flush()

    problems = await _probe_problems(db, NOW.timestamp())
    assert len(problems) == 1
    assert sorted(problems[0].host_ids) == sorted(h.id for h in hosts)

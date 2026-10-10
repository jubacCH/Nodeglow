"""Watched services on agents: streaks, incidents, maintenance, cleanup."""
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models.agent import Agent
from models.incident import Incident
from models.ping import PingHost
from services import agent_services as svc
from services.correlation import _auto_resolve

NOW = datetime(2026, 10, 10, 12, 0, 0)


def _report(*pairs):
    return [{"name": n, "state": s} for n, s in pairs]


async def _agent(db, watched=("nginx",), hostname="web01"):
    agent = Agent(name="web01", hostname=hostname, token="t" * 64,
                  watched_services=json.dumps(list(watched)))
    db.add(agent)
    await db.flush()
    return agent


async def _open_incidents(db):
    return (await db.execute(
        select(Incident).where(Incident.rule == "agent_service",
                               Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()


async def _feed(db, agent, report, n=1):
    notes = []
    for i in range(n):
        notes += await svc.apply_service_report(db, agent, report, now=NOW + timedelta(seconds=30 * i))
    await db.flush()
    return notes


# ── Pure merge ───────────────────────────────────────────────────────────────

def test_merge_counts_failures_and_resets_on_running():
    s1 = {"services": svc.merge_report(None, ["nginx"], _report(("nginx", "stopped")), NOW)}
    assert s1["services"][0]["fail_count"] == 1
    s2 = {"services": svc.merge_report(s1, ["nginx"], _report(("nginx", "not_found")), NOW)}
    assert s2["services"][0]["fail_count"] == 2
    s3 = svc.merge_report(s2, ["nginx"], _report(("nginx", "running")), NOW)
    assert s3[0]["fail_count"] == 0
    assert s3[0]["state"] == "running"


def test_unknown_and_missing_hold_the_streak():
    s1 = {"services": svc.merge_report(None, ["a", "b"], _report(("a", "stopped"), ("b", "stopped")), NOW)}
    s2 = svc.merge_report(s1, ["a", "b"], _report(("a", "unknown")), NOW)  # b missing
    assert [x["fail_count"] for x in s2] == [1, 1]
    assert [x["state"] for x in s2] == ["unknown", "unknown"]


def test_merge_tolerates_garbage_and_matches_case_insensitively():
    reported = [None, "x", {"name": 5}, {"name": "SPOOLER", "state": "bogus"}, {"name": "W32Time", "state": "stopped"}]
    out = svc.merge_report(None, ["Spooler", "w32time"], reported, NOW)
    assert out[0]["state"] == "unknown"
    assert out[1]["state"] == "stopped"


def test_since_only_moves_on_a_state_change():
    s1 = {"services": svc.merge_report(None, ["a"], _report(("a", "running")), NOW)}
    later = NOW + timedelta(minutes=5)
    s2 = svc.merge_report(s1, ["a"], _report(("a", "running")), later)
    assert s2[0]["since"] == NOW.isoformat()
    s3 = svc.merge_report({"services": s2}, ["a"], _report(("a", "stopped")), later)
    assert s3[0]["since"] == later.isoformat()


def test_watch_list_validation():
    assert svc.normalize_watch_list([" nginx ", "nginx", "", "MSSQL$SQLEXPRESS", "getty@tty1.service"]) == [
        "nginx", "MSSQL$SQLEXPRESS", "getty@tty1.service"]
    assert svc.normalize_watch_list(None) == []
    for bad in ("not a list", [1], ["a;b"], ["--help"], ["x" * 300], [f"s{i}" for i in range(51)]):
        with pytest.raises(ValueError):
            svc.normalize_watch_list(bad)


# ── Incidents ────────────────────────────────────────────────────────────────

async def test_incident_opens_after_n_consecutive_reports_and_only_once(db):
    agent = await _agent(db)
    notes = await _feed(db, agent, _report(("nginx", "stopped")), n=2)
    assert notes == []
    assert await _open_incidents(db) == []

    notes = await _feed(db, agent, _report(("nginx", "stopped")))
    assert len(notes) == 1 and "nginx" in notes[0][0]
    incidents = await _open_incidents(db)
    assert len(incidents) == 1
    assert incidents[0].host_ids_hash == svc.incident_hash(agent.id, "nginx")

    # Still down: no second incident, no second notification.
    notes = await _feed(db, agent, _report(("nginx", "stopped")), n=3)
    assert notes == []
    assert len(await _open_incidents(db)) == 1


async def test_running_again_resolves_and_notifies(db):
    agent = await _agent(db)
    await _feed(db, agent, _report(("nginx", "not_found")), n=3)
    assert len(await _open_incidents(db)) == 1

    notes = await _feed(db, agent, _report(("nginx", "running")))
    assert await _open_incidents(db) == []
    assert len(notes) == 1 and notes[0][0].startswith("✅ Resolved")
    state = svc.load_states(agent)["services"][0]
    assert state["alerted"] is False and state["fail_count"] == 0


async def test_threshold_comes_from_settings(db):
    from models.settings import Setting
    db.add(Setting(key=svc.FAIL_REPORTS_SETTING, value="1"))
    agent = await _agent(db)
    await _feed(db, agent, _report(("nginx", "stopped")))
    assert len(await _open_incidents(db)) == 1


async def test_unknown_never_opens_an_incident(db):
    agent = await _agent(db)
    await _feed(db, agent, _report(("nginx", "unknown")), n=10)
    assert await _open_incidents(db) == []


async def test_maintenance_suppresses_and_alerts_once_it_ends(db):
    agent = await _agent(db)
    host = PingHost(name="web01", hostname="web01", source="agent", maintenance=True)
    db.add(host)
    await db.flush()

    await _feed(db, agent, _report(("nginx", "stopped")), n=5)
    assert await _open_incidents(db) == []

    host.maintenance = False
    await db.flush()
    notes = await _feed(db, agent, _report(("nginx", "stopped")))
    assert len(notes) == 1
    assert len(await _open_incidents(db)) == 1


async def test_unwatching_a_service_resolves_its_incident(db):
    agent = await _agent(db, watched=("nginx", "sshd"))
    await _feed(db, agent, _report(("nginx", "stopped"), ("sshd", "running")), n=3)
    assert len(await _open_incidents(db)) == 1

    notes = await svc.apply_watch_list_change(db, agent, ["sshd"])
    await db.flush()
    assert await _open_incidents(db) == []
    assert len(notes) == 1
    assert [s["name"] for s in svc.load_states(agent)["services"]] == ["sshd"]
    assert json.loads(agent.watched_services) == ["sshd"]

    await svc.apply_watch_list_change(db, agent, [])
    assert agent.watched_services is None


async def test_correlation_auto_resolve_leaves_service_incidents_alone(db):
    agent = await _agent(db)
    await _feed(db, agent, _report(("nginx", "stopped")), n=3)
    with patch("notifications.notify", new_callable=AsyncMock):
        await _auto_resolve(db, offline_hosts=[])
    await db.flush()
    assert len(await _open_incidents(db)) == 1


async def test_service_view_joins_list_and_states(db):
    agent = await _agent(db, watched=("nginx", "sshd"))
    view = svc.service_view(agent)
    assert view["services_reported_at"] is None
    assert [s["state"] for s in view["services"]] == [None, None]

    await _feed(db, agent, [{"name": "nginx", "state": "running", "start_type": "enabled"}])
    view = svc.service_view(agent)
    assert view["watched_services"] == ["nginx", "sshd"]
    assert view["services"][0]["state"] == "running"
    assert view["services"][0]["start_type"] == "enabled"
    assert view["services"][1]["state"] == "unknown"
    assert view["services_reported_at"] == NOW.isoformat()

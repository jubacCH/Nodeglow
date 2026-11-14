"""Incidents for agents that stopped reporting."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.agent import Agent
from models.incident import Incident
from models.ping import PingHost
from services import agent_silence as silence
from services import condition_incidents as cond

NOW = datetime(2026, 11, 14, 12, 0, 0)


def _agent(name, minutes_ago=None, **fields) -> Agent:
    last_seen = None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago)
    return Agent(**{"name": name, "hostname": name, "token": f"tok-{name}", "enabled": True,
                    "is_probe": False, "last_seen": last_seen, **fields})


async def _sync(db, now=NOW):
    notes = await cond.sync_rule(db, silence.RULE, await silence.silent_conditions(db, now),
                                 event_type="agent_silent", resolved_summary="reports again")
    await db.commit()
    return notes


async def _open(db):
    return (await db.execute(
        select(Incident).where(Incident.rule == silence.RULE, Incident.status == "open")
    )).scalars().all()


async def test_a_silent_agent_is_an_incident_and_a_reporting_one_resolves_it(db):
    """Six agents crash-looped for seven months on a real installation and
    nothing was raised: the hosts still answered a ping."""
    web = _agent("web", minutes_ago=11)
    db.add_all([web, _agent("db", minutes_ago=5), _agent("new"), _agent("off", minutes_ago=600, enabled=False)])
    await db.flush()

    notes = await _sync(db)
    assert [n.title for n in notes] == ["🔴 Incident: Agent silent: web"]
    (incident,) = await _open(db)
    assert incident.severity == "warning" and incident.host_ids_hash == f"ag:{web.id}"
    assert "last reported 11 minutes ago (2026-11-14 11:49 UTC)" in notes[0].summary

    assert await _sync(db) == [], "one notification, not one per run"

    web.last_seen = NOW + timedelta(minutes=1)
    notes = await _sync(db, NOW + timedelta(minutes=2))
    assert [n.title for n in notes] == ["✅ Resolved: Agent silent: web"]
    assert await _open(db) == []


def test_the_age_reads_like_a_person_would_say_it():
    def said(**delta):
        agent = _agent("a")
        agent.last_seen = NOW - timedelta(**delta)
        return silence.silent_condition(agent, NOW).summary

    assert "last reported 90 minutes ago" in said(minutes=90)
    assert "last reported 5 hours ago" in said(hours=5)
    assert "last reported 218 days ago" in said(days=218)


def test_a_silent_probe_says_what_is_no_longer_checked():
    cond_ = silence.silent_condition(_agent("site-b", minutes_ago=30, is_probe=True), NOW)
    assert cond_.title == "Probe silent: site-b" and "hosts it checks are not being checked" in cond_.summary


async def test_the_incident_names_the_host_and_maintenance_keeps_it_quiet(db):
    host = PingHost(name="web", hostname="web.example.com", check_type="icmp", enabled=True, source="proxmox")
    planned = PingHost(name="db", hostname="db.example.com", check_type="icmp", enabled=True,
                       source="proxmox", maintenance=True)
    # Not this agent's host: an address has no short name to match on.
    other = PingHost(name="10.0.0.7", hostname="10.0.0.7", check_type="icmp", enabled=True, source="manual")
    db.add_all([host, planned, other, _agent("web", minutes_ago=30), _agent("DB", minutes_ago=30),
                _agent("10", minutes_ago=30)])
    await db.flush()

    wanted = await silence.silent_conditions(db, NOW)
    # The agent calls itself "web", the host is listed as web.example.com.
    assert {c.title: c.host_ids for c in wanted.values()} == {
        "Agent silent: web": [host.id], "Agent silent: 10": []}


def test_nothing_is_judged_right_after_a_start():
    """While the server was down no agent could report."""
    assert silence.started_recently(now_ts=1000.0, process_start=900.0)
    assert not silence.started_recently(now_ts=1000.0 + 600, process_start=1000.0)


def test_many_notifications_of_one_run_are_one_message():
    """A site that drops off the network is one notification."""
    opened = [cond.Note(f"🔴 Incident: Agent silent: a{i}", "s", "warning", i) for i in range(4)]
    resolved = [cond.Note(f"✅ Resolved: Agent silent: b{i}", "s", "info", 10 + i) for i in range(2)]
    out = silence.bundle(opened + resolved)
    assert [n.title for n in out] == ["🔴 Incident: 4 agents silent",
                                      "✅ Resolved: Agent silent: b0", "✅ Resolved: Agent silent: b1"]
    assert out[0].summary == "a0, a1, a2, a3." and out[0].link_path == "/incidents"
    assert silence.bundle(opened[:2]) == opened[:2]

    back = silence.bundle([cond.Note(f"✅ Resolved: Agent silent: a{i}", "s", "info", i) for i in range(3)])
    assert [n.title for n in back] == ["✅ Resolved: 3 agents reporting again"]


async def test_a_bundled_note_links_to_the_incident_list():
    with patch("notifications.notify", new=AsyncMock()) as notify:
        await cond.send([cond.Note("t", "s", "warning", 7), cond.Note("t", "s", "warning", 7, link_path="/incidents")])
    assert [c.kwargs["link_path"] for c in notify.call_args_list] == ["/incidents/7", "/incidents"]


async def test_auto_resolve_leaves_silent_agents_to_their_job(db):
    from services import correlation as corr

    db.add(_agent("web", minutes_ago=30))
    await db.flush()
    await _sync(db)
    with patch("notifications.notify", new=AsyncMock()):
        await corr._auto_resolve(db, offline_hosts=[])
        await db.commit()
    assert len(await _open(db)) == 1

"""Evidence on incidents (concept F, phase 3, PR 1): writers, labels, API shape."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.incident import Incident, IncidentRelation
from models.ping import PingHost
from services import incident_relations as relations


def _host(i, **fields):
    hostname = fields.pop("hostname", f"10.0.{i}.1")
    return SimpleNamespace(id=i, name=fields.pop("name", f"h{i}"), hostname=hostname, **fields)


def _reset_streaks():
    from services import correlation as corr

    corr._rule_hit_counts.clear()
    corr._current_cycle_hits.clear()


async def _relations(db, incident_id):
    return (await db.execute(
        select(IncidentRelation).where(IncidentRelation.incident_id == incident_id)
        .order_by(IncidentRelation.kind, IncidentRelation.id)
    )).scalars().all()


async def _open(db, rule):
    return (await db.execute(
        select(Incident).where(Incident.rule == rule, Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()


async def test_record_refreshes_instead_of_duplicating(db):
    inc = Incident(rule="r", title="t", severity="warning")
    db.add(inc)
    await db.flush()
    t0 = datetime.utcnow() - timedelta(minutes=5)
    await relations.record(db, inc, kind="confirmed", source="topology", subject_kind="host", subject_id=7,
                           summary="All 3 hosts share the parent sw.", now=t0)
    await relations.record(db, inc, kind="confirmed", source="topology", subject_kind="host", subject_id=7,
                           summary="All 4 hosts share the parent sw.", detail={"hosts": 4})
    await relations.record(db, inc, kind="rule", source="rule", summary="Rule r: x")
    await relations.record(db, inc, kind="rule", source="rule", summary="Rule r: y")
    await db.commit()

    rows = await _relations(db, inc.id)
    assert [(r.kind, r.summary) for r in rows] == [
        ("confirmed", "All 4 hosts share the parent sw."), ("rule", "Rule r: y")]
    confirmed = rows[0]
    assert confirmed.first_seen_at == t0 and confirmed.last_seen_at > t0
    assert json.loads(confirmed.detail) == {"hosts": 4}


async def test_an_unknown_kind_is_refused(db):
    inc = Incident(rule="r", title="t", severity="warning")
    db.add(inc)
    await db.flush()
    try:
        await relations.record(db, inc, kind="probably", source="rule", summary="x")
    except ValueError:
        return
    raise AssertionError("an unknown label must not be stored")


def test_shared_parent_needs_the_same_direct_parent():
    topo = {1: 10, 2: 10, 3: 10, 4: 11, 10: 99, 11: 99}
    assert relations.shared_parent(topo, [1, 2, 3]) == 10
    assert relations.shared_parent(topo, [1, 2, 4]) is None, "only the direct parent counts"
    assert relations.shared_parent(topo, [1]) is None, "one host shares nothing"
    assert relations.shared_parent({5: None, 6: None}, [5, 6]) is None
    assert relations.shared_parent(None, [1, 2]) is None


def test_cause_path_runs_from_the_root_and_survives_loops():
    assert relations.cause_path({3: 2, 2: 1, 1: None}, 3) == [1, 2, 3]
    assert relations.cause_path({3: 2, 2: 3}, 3) == [2, 3]


async def test_multi_host_down_confirms_the_shared_parent(db):
    from services import correlation as corr

    _reset_streaks()
    sw = PingHost(name="SW-ZH-CORE-02", hostname="10.0.0.254")
    db.add(sw)
    await db.flush()
    # Ids far from the switch's, so the simulated hosts never collide with it.
    hosts = [_host(i, hostname=f"10.0.0.{i - 100}") for i in (101, 102, 103)]
    topo = {101: sw.id, 102: sw.id, 103: sw.id, sw.id: None}
    with patch.object(corr, "_get_topology", new=AsyncMock(return_value=topo)), \
         patch("notifications.notify", new=AsyncMock()):
        await corr._rule_multi_host_down(db, min_cycles=1, offline_hosts=hosts)
        await db.commit()

    (inc,) = await _open(db, "multi_host_down")
    assert (inc.cause_kind, inc.cause_id) == ("host", sw.id)
    rows = {r.kind: r for r in await _relations(db, inc.id)}
    assert rows["confirmed"].summary == "All 3 hosts share the parent SW-ZH-CORE-02 in the topology."
    assert (rows["confirmed"].source, rows["confirmed"].subject_kind, rows["confirmed"].subject_id) == (
        "topology", "host", sw.id)
    assert rows["rule"].summary.startswith("Rule multi_host_down: 3 hosts offline in 10.0.0.0/24")


async def test_without_a_shared_parent_nothing_is_confirmed(db):
    from services import correlation as corr

    _reset_streaks()
    hosts = [_host(i, hostname=f"10.0.0.{i}") for i in (1, 2, 3)]
    with patch.object(corr, "_get_topology", new=AsyncMock(return_value={1: 50, 2: 51, 3: 50})), \
         patch("notifications.notify", new=AsyncMock()):
        await corr._rule_multi_host_down(db, min_cycles=1, offline_hosts=hosts)
        await db.commit()

    (inc,) = await _open(db, "multi_host_down")
    assert inc.cause_kind is None and inc.cause_id is None
    assert [r.kind for r in await _relations(db, inc.id)] == ["rule"]


async def test_upstream_failure_confirms_the_failed_parent(db):
    from services import correlation as corr

    _reset_streaks()
    parent = _host(10, name="SW-BE-01", hostname="10.9.0.1")
    behind = [_host(i, hostname=f"10.9.{i}.5") for i in (1, 2)]
    topo = {1: 10, 2: 10, 10: None}
    with patch("services.clickhouse_client.count_syslog_by_host", new=AsyncMock(return_value={})), \
         patch.object(corr, "_get_topology", new=AsyncMock(return_value=topo)), \
         patch("notifications.notify", new=AsyncMock()):
        await corr._rule_host_down(db, min_cycles=1, offline_hosts=[parent, *behind])
        await db.commit()

    (inc,) = await _open(db, "upstream_failure")
    assert (inc.cause_kind, inc.cause_id) == ("host", 10)
    confirmed = [r for r in await _relations(db, inc.id) if r.kind == "confirmed"]
    assert [r.summary for r in confirmed] == [
        "2 of the 2 hosts are behind SW-BE-01 in the topology, and SW-BE-01 is offline."]


async def test_a_failing_evidence_writer_keeps_the_incident(db, caplog):
    from services import correlation as corr

    with patch.object(corr.relations, "record", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("notifications.notify", new=AsyncMock()):
        inc = await corr._find_or_create_incident(
            db, rule="r", title="t", severity="warning", host_ids=[1], event_type="created", summary="s")
        await db.commit()

    assert inc.id is not None and inc.status == "open"
    assert any("Could not record the evidence" in r.getMessage() for r in caplog.records)


async def test_describe_names_subjects_builds_the_path_and_marks_stale(db):
    fw = PingHost(name="fw-zh-01", hostname="10.0.0.1")
    sw = PingHost(name="SW-ZH-CORE-02", hostname="10.0.0.2")
    db.add_all([fw, sw])
    await db.flush()
    inc = Incident(rule="multi_host_down", title="t", severity="critical", cause_kind="host", cause_id=sw.id)
    db.add(inc)
    await db.flush()
    now = datetime.utcnow()
    await relations.record(db, inc, kind="rule", source="rule", summary="Rule multi_host_down: x", now=now)
    await relations.record(db, inc, kind="confirmed", source="topology", subject_kind="host",
                           subject_id=sw.id, summary="All 3 hosts share the parent SW-ZH-CORE-02.", now=now)
    await relations.record(db, inc, kind="confirmed", source="probe", subject_kind="host", subject_id=fw.id,
                           summary="Not refreshed for ten minutes.", now=now - timedelta(minutes=10))
    await db.commit()

    states = {sw.id: SimpleNamespace(state="degraded"), fw.id: SimpleNamespace(state="up")}
    out = (await relations.describe(db, [inc], topology={sw.id: fw.id, fw.id: None},
                                    states=states, now=now))[inc.id]
    assert [r["kind"] for r in out["relations"]] == ["confirmed", "confirmed", "rule"]
    assert out["relations"][0]["subject"] == {"kind": "host", "id": sw.id, "name": "SW-ZH-CORE-02"}
    assert [r["stale"] for r in out["relations"]] == [False, True, False]
    assert out["cause"] == {
        "kind": "host", "id": sw.id, "name": "SW-ZH-CORE-02", "state": "degraded",
        "path": [{"id": fw.id, "name": "fw-zh-01", "state": "up"},
                 {"id": sw.id, "name": "SW-ZH-CORE-02", "state": "degraded"}],
    }

    capped = (await relations.describe(db, [inc], topology=None, now=now, limit=2))[inc.id]
    assert len(capped["relations"]) == 2
    assert capped["cause"]["path"] == [{"id": sw.id, "name": "SW-ZH-CORE-02", "state": None}]


async def test_relations_of_a_resolved_incident_are_never_stale(db):
    inc = Incident(rule="r", title="t", severity="warning", status="resolved")
    db.add(inc)
    await db.flush()
    await relations.record(db, inc, kind="rule", source="rule", summary="Rule r: x",
                           now=datetime.utcnow() - timedelta(hours=3))
    await db.commit()
    out = (await relations.describe(db, [inc], topology=None))[inc.id]
    assert out["relations"][0]["stale"] is False

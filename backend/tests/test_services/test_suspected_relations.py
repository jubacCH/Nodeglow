"""Suspected relations (concept F, phase 3, PR 2): window, limit, wording, honesty."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.incident import Incident, IncidentRelation
from models.ping import PingHost
from services import incident_relations as relations
from services import suspected_relations as suspected

NOW = datetime(2026, 10, 10, 14, 32)


def _inc(id_, minutes_ago, hosts, title=None, rule="host_down", status="open"):
    return Incident(id=id_, rule=rule, title=title or f"incident {id_}", severity="warning", status=status,
                    host_ids=json.dumps(hosts) if hosts is not None else None,
                    created_at=NOW - timedelta(minutes=minutes_ago))


async def _suspected(db, incident_id):
    return (await db.execute(
        select(IncidentRelation).where(IncidentRelation.incident_id == incident_id,
                                       IncidentRelation.kind == "suspected")
        .order_by(IncidentRelation.id)
    )).scalars().all()


def _no_baselines():
    return patch("services.log_intelligence.load_effective_baselines", new=AsyncMock(return_value={}))


async def test_another_incident_in_the_window_without_a_link_is_suspected(db):
    db.add_all([_inc(1, 25, [10, 11]), _inc(2, 28, [50], title="Filterlog burst on fw-zh-01")])
    await db.commit()
    with _no_baselines():
        n = await suspected.run(db, {10: 5, 11: 5, 50: None}, now=NOW)
        await db.commit()

    assert n == 2, "both are young: each is a hint for the other"
    assert [r.subject_id for r in await _suspected(db, 2)] == [1]
    (rel,) = await _suspected(db, 1)
    assert (rel.source, rel.subject_kind, rel.subject_id) == ("incident", "incident", 2)
    assert rel.summary == ("Incident #2 (Filterlog burst on fw-zh-01) opened 3 min before this one, "
                           "on 1 host with no topology link to these hosts. Overlap in time only.")
    assert json.loads(rel.detail)["offset_minutes"] == -3.0


async def test_a_structural_link_is_never_suspected(db):
    # Incident 2 is on the parent switch of incident 1's hosts, incident 3 on a host behind them.
    db.add_all([_inc(1, 20, [10, 11]), _inc(2, 21, [5]), _inc(3, 19, [99])])
    await db.commit()
    with _no_baselines():
        await suspected.run(db, {10: 5, 11: 5, 5: None, 99: 10}, now=NOW)
        await db.commit()
    assert await _suspected(db, 1) == []


async def test_only_the_window_and_only_young_incidents(db):
    db.add_all([
        _inc(1, 25, [10]),           # young target
        _inc(2, 25 + 11, [60]),      # 11 min before: outside the window
        _inc(3, 45, [11]),           # old target: not looked at
        _inc(4, 44, [61]),           # would be in the old target's window
        _inc(5, 20, None),           # no hosts recorded: never a target itself
    ])
    await db.commit()
    with _no_baselines():
        await suspected.run(db, {}, now=NOW)
        await db.commit()
    assert [r.subject_id for r in await _suspected(db, 1)] == [5], "only #5 (5 min after) is in #1's window"
    assert await _suspected(db, 3) == []
    assert await _suspected(db, 5) == []


async def test_at_most_three_are_stored_closest_first_also_across_runs(db):
    db.add(_inc(1, 20, [10]))
    for i, offset in zip(range(2, 7), (4, -1, 6, 2, -8)):
        db.add(_inc(i, 20 - offset, [100 + i]))
    await db.commit()
    with _no_baselines():
        await suspected.run(db, {}, now=NOW)
        await db.commit()
        assert [r.subject_id for r in await _suspected(db, 1)] == [3, 5, 2], "1, 2 and 4 min away"

        db.add(_inc(7, 20, [107]))   # a new, even closer one in a later run
        await db.commit()
        await suspected.run(db, {}, now=NOW + timedelta(minutes=1))
        await db.commit()
    assert [r.subject_id for r in await _suspected(db, 1)] == [3, 5, 2], "the stored cap holds"


async def test_a_loud_unrelated_host_is_suspected_with_its_numbers(db):
    fw = PingHost(name="fw-zh-01", hostname="10.0.0.1")
    sw = PingHost(name="SW-ZH-CORE-02", hostname="10.0.0.2")
    db.add_all([fw, sw])
    await db.flush()
    db.add(_inc(1, 10, [sw.id + 100]))
    await db.commit()
    baselines = {
        f"host:{fw.id}": SimpleNamespace(avg_rate=600.0),     # 10/min usual
        f"host:{sw.id}": SimpleNamespace(avg_rate=60.0),      # quiet, but structurally linked
        "10.9.9.9": SimpleNamespace(avg_rate=10.0),           # no host: never a subject
    }
    # Window: 20 minutes (onset 10 min ago, minus 10). fw: 840 messages = 2520/h = 4.2x.
    counts = {f"host:{fw.id}": 840, f"host:{sw.id}": 400, "10.9.9.9": 500}
    with patch("services.log_intelligence.load_effective_baselines", new=AsyncMock(return_value=baselines)), \
         patch("services.clickhouse_client.count_syslog_received_by_key", new=AsyncMock(return_value=counts)):
        await suspected.run(db, {sw.id + 100: sw.id, sw.id: fw.id, fw.id: None}, now=NOW)
        await db.commit()

    rels = await _suspected(db, 1)
    assert [r.subject_id for r in rels] == [], "fw is an ancestor of the incident's host: structural"

    # Same numbers, but fw is not in the incident's branch.
    with patch("services.log_intelligence.load_effective_baselines", new=AsyncMock(return_value=baselines)), \
         patch("services.clickhouse_client.count_syslog_received_by_key", new=AsyncMock(return_value=counts)):
        await suspected.run(db, {sw.id + 100: sw.id, sw.id: None, fw.id: None}, now=NOW)
        await db.commit()
    (rel,) = await _suspected(db, 1)
    assert (rel.source, rel.subject_kind, rel.subject_id) == ("baseline", "host", fw.id)
    assert rel.summary == ("fw-zh-01 logged 2520 messages/h between 14:12 and 14:32 UTC, 4.2× its usual "
                           "600/h for this hour. No topology link to these hosts.")


async def test_quiet_hosts_and_small_numbers_are_not_suspected(db):
    fw = PingHost(name="fw", hostname="10.0.0.1")
    db.add(fw)
    await db.flush()
    db.add(_inc(1, 10, [999]))
    await db.commit()
    baselines = {f"host:{fw.id}": SimpleNamespace(avg_rate=3.0)}
    for count in (19, 30):   # 19 is below the minimum; 30 in 20 min = 90/h is 30x, but checked below
        with patch("services.log_intelligence.load_effective_baselines", new=AsyncMock(return_value=baselines)), \
             patch("services.clickhouse_client.count_syslog_received_by_key",
                   new=AsyncMock(return_value={f"host:{fw.id}": count})):
            await suspected.run(db, {}, now=NOW)
            await db.commit()
        if count == 19:
            assert await _suspected(db, 1) == []
    assert len(await _suspected(db, 1)) == 1


async def test_suspected_relations_never_go_stale(db):
    db.add_all([_inc(1, 25, [10]), _inc(2, 27, [60])])
    await db.commit()
    with _no_baselines():
        await suspected.run(db, {}, now=NOW - timedelta(hours=1))
        await db.commit()
    inc = await db.get(Incident, 1)
    out = (await relations.describe(db, [inc], topology=None))[1]
    assert [r["stale"] for r in out["relations"]] == [False]


async def test_the_correlation_run_includes_the_pass_and_never_notifies(db):
    from services import correlation as corr

    notify = AsyncMock()
    run = AsyncMock(return_value=0)
    with patch.object(corr, "_get_topology", new=AsyncMock(return_value={1: None})), \
         patch("services.suspected_relations.run", new=run), \
         patch("notifications.notify", new=notify):
        await corr._suspected_relations(db)
    run.assert_awaited_once()
    assert run.await_args.args[1] == {1: None}
    assert notify.await_count == 0

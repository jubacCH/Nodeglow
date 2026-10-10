"""The alerts widget shows the newest meaningful event of each recent incident.

It used to load every event of the ten incidents (20k rows on production) and
keep the first per incident; now it asks for exactly one per incident.
"""
from datetime import datetime, timedelta

from models.incident import Incident, IncidentEvent
from routers.dashboard import latest_incident_summaries
from tests.test_routers.conftest import make_client

T0 = datetime(2026, 10, 10, 8, 0)


def _incident(i: int) -> Incident:
    return Incident(rule=f"r{i}", title=f"Incident {i}", severity="warning",
                    status="open", created_at=T0 + timedelta(minutes=i))


def _event(inc_id: int, minute: int, kind: str, summary: str) -> IncidentEvent:
    return IncidentEvent(incident_id=inc_id, timestamp=T0 + timedelta(minutes=minute),
                         event_type=kind, summary=summary)


async def test_newest_event_wins_and_ack_resolve_are_skipped(db):
    a, b, c = _incident(1), _incident(2), _incident(3)
    db.add_all([a, b, c])
    await db.flush()
    db.add_all([
        _event(a.id, 1, "created", "a created"),
        _event(a.id, 5, "host_down", "a down"),
        _event(a.id, 9, "acknowledged", "a acked"),
        _event(a.id, 10, "resolved", "a resolved"),
        _event(b.id, 2, "created", "b created"),
        # c has only events that do not count
        _event(c.id, 3, "resolved", "c resolved"),
    ])
    await db.commit()

    got = await latest_incident_summaries(db, [a.id, b.id, c.id])

    assert got == {a.id: "a down", b.id: "b created"}


async def test_only_the_requested_incidents(db):
    a, b = _incident(1), _incident(2)
    db.add_all([a, b])
    await db.flush()
    db.add_all([_event(a.id, 1, "created", "a"), _event(b.id, 1, "created", "b")])
    await db.commit()

    assert await latest_incident_summaries(db, [b.id]) == {b.id: "b"}
    assert await latest_incident_summaries(db, []) == {}


async def test_dashboard_recent_incidents_carry_the_summary():
    async with make_client() as (client, session_factory):
        async with session_factory() as db:
            inc = _incident(1)
            db.add(inc)
            await db.flush()
            db.add_all([
                _event(inc.id, 1, "created", "first"),
                _event(inc.id, 2, "host_down", "latest"),
                _event(inc.id, 3, "acknowledged", "acked"),
            ])
            await db.commit()

        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    recent = resp.json()["recent_incidents"]
    assert [(r["title"], r["summary"]) for r in recent] == [("Incident 1", "latest")]

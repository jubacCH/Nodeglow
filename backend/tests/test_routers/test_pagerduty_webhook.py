"""The way back from PagerDuty: acknowledge and resolve reach the incident."""
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import notification_channels as nc
from models.incident import Incident, IncidentEvent
from services.tenancy import tenant_scope
from tests.test_routers.conftest import make_client

PUBLIC_URL = "https://ng.example"
_JSON = {"accept": "application/json"}


async def _setup(client, sf, status="open") -> int:
    from database import set_setting

    async with sf() as db:
        with tenant_scope(1):
            # Written directly: the settings endpoint is rate limited, and the
            # whole suite shares one client address.
            await set_setting(db, "public_url", PUBLIC_URL)
            inc = Incident(rule="host_down", title="Host down: router", severity="critical",
                           status=status, host_ids_hash="h")
            db.add(inc)
            await db.commit()
            return inc.id


def _event(event_type, incident_id, *, public_url=PUBLIC_URL, key=None):
    # The key exactly as Nodeglow sent it with the trigger.
    key = key or nc.dedup_key(public_url, f"/incidents/{incident_id}", "🔴 Incident: Host down: router")
    return {"event": {
        "id": "01ABC", "event_type": event_type, "resource_type": "incident",
        "occurred_at": "2026-11-13T09:00:00Z",
        "agent": {"id": "PUSER1", "type": "user_reference", "summary": "Ana Oncall"},
        "data": {"id": "Q1", "type": "incident", "status": "acknowledged",
                 "incident_key": key, "title": "Host down: router"},
    }}


async def _state(sf, incident_id):
    async with sf() as db:
        with tenant_scope(1):
            inc = await db.get(Incident, incident_id)
            events = (await db.execute(
                select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.id))).scalars().all()
            return inc, [(e.event_type, e.summary) for e in events]


async def test_acknowledge_in_pagerduty_acknowledges_the_incident():
    async with make_client() as (client, sf):
        incident_id = await _setup(client, sf)
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            r = await client.post("/api/v1/events/pagerduty",
                                  json=_event("incident.acknowledged", incident_id))
        assert r.status_code == 200
        assert r.json() == {"ok": True, "action": "acknowledged", "incident_id": incident_id}
        inc, events = await _state(sf, incident_id)
        assert inc.status == "acknowledged" and inc.acknowledged_by == "Ana Oncall (PagerDuty)"
        assert events == [("acknowledged", "Acknowledged by Ana Oncall (PagerDuty)")]
        notify.assert_not_awaited()

        # PagerDuty delivers at least once: a repeat changes nothing.
        r = await client.post("/api/v1/events/pagerduty",
                              json=_event("incident.acknowledged", incident_id))
        assert r.json()["action"] == "unchanged"
        assert len((await _state(sf, incident_id))[1]) == 1


async def test_resolve_in_pagerduty_resolves_and_tells_the_other_channels():
    async with make_client() as (client, sf):
        incident_id = await _setup(client, sf, status="acknowledged")
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            r = await client.post("/api/v1/events/pagerduty",
                                  json=_event("incident.resolved", incident_id))
            again = await client.post("/api/v1/events/pagerduty",
                                      json=_event("incident.resolved", incident_id))
        assert r.json()["action"] == "resolved" and again.json()["action"] == "unchanged"
        inc, events = await _state(sf, incident_id)
        assert inc.status == "resolved" and inc.resolved_at is not None
        assert events == [("resolved", "Resolved by Ana Oncall (PagerDuty)")]
        notify.assert_awaited_once()
        assert notify.await_args.args[0] == "✅ Resolved: Host down: router"


async def test_an_acknowledge_does_not_reopen_a_resolved_incident():
    async with make_client() as (client, sf):
        incident_id = await _setup(client, sf, status="resolved")
        r = await client.post("/api/v1/events/pagerduty",
                              json=_event("incident.acknowledged", incident_id))
        assert r.json()["action"] == "unchanged"
        assert (await _state(sf, incident_id))[0].status == "resolved"


async def test_an_alert_of_another_installation_is_not_applied():
    """Two Nodeglow installations page into one PagerDuty service; both have
    an incident with this id."""
    async with make_client() as (client, sf):
        incident_id = await _setup(client, sf)
        r = await client.post("/api/v1/events/pagerduty", json=_event(
            "incident.resolved", incident_id, public_url="https://other.example"))
        assert r.status_code == 200 and r.json()["action"] == "ignored"
        assert (await _state(sf, incident_id))[0].status == "open"


@pytest.mark.parametrize("body", [
    {}, {"event": "nope"}, {"event": {"event_type": "incident.acknowledged"}},
    {"event": {"event_type": "incident.acknowledged", "data": {"incident_key": "someone-elses-key"}}},
    {"event": {"event_type": "incident.acknowledged",
               "data": {"incident_key": "nodeglow-00000000-incident-999999999999999999999"}}},
    {"event": {"event_type": "pagey.ping", "data": {}}},
    {"event": {"event_type": "incident.triggered", "data": {"incident_key": "x"}}},
])
async def test_bodies_that_do_not_concern_an_incident_are_answered_200(client, body):
    """PagerDuty retries every non-2xx answer; a retry cannot fix these."""
    r = await client.post("/api/v1/events/pagerduty", json=body)
    assert r.status_code == 200 and r.json()["action"] == "ignored"


async def test_an_unknown_incident_id_is_ignored():
    async with make_client() as (client, sf):
        await _setup(client, sf)
        r = await client.post("/api/v1/events/pagerduty", json=_event("incident.resolved", 424242))
        assert r.json() == {"ok": True, "action": "ignored", "reason": "incident not found"}


async def test_needs_an_api_key(auth_client):
    client, _sf = auth_client
    r = await client.post("/api/v1/events/pagerduty", json=_event("incident.resolved", 1))
    assert r.status_code == 401

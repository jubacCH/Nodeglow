"""Inbound events: /api/v1/events and the Alertmanager receiver."""
import secrets
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models.api_key import ApiKey
from models.incident import Incident, IncidentEvent
from services import external_events as ext
from services.tenancy import tenant_scope
from tests.test_routers.conftest import make_client

EVENT = {"source": "backup-script", "key": "nightly-db", "severity": "critical",
         "title": "Nightly DB backup failed", "summary": "pg_dump exited with 1"}


async def _api_key(sf, role: str = "editor", tenant_id: int = 1) -> str:
    from routers.api_v1 import _hash_key

    raw = f"ng_{secrets.token_hex(12)}"
    async with sf() as s:
        with tenant_scope(tenant_id):
            s.add(ApiKey(name=f"key-{role}", key_hash=_hash_key(raw), prefix=raw[:8],
                         role=role, enabled=True, hash_algo="hmac"))
            await s.commit()
    return raw


async def _incidents(sf):
    async with sf() as s:
        with tenant_scope(1):
            return (await s.execute(select(Incident).order_by(Incident.id))).scalars().all()


async def _events(sf, incident_id):
    async with sf() as s:
        with tenant_scope(1):
            return (await s.execute(
                select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.id))).scalars().all()


@pytest.fixture
def notify():
    with patch("notifications.notify", new_callable=AsyncMock) as m:
        yield m


# ── Generic events ───────────────────────────────────────────────────────────

async def test_firing_opens_one_incident_and_notifies_once(client, notify):
    r = await client.post("/api/v1/events", json=EVENT)
    assert r.status_code == 200, r.text
    first = r.json()["results"][0]
    assert first["action"] == "created" and first["incident_id"]

    # The sender repeats the alert while it fires: same incident, no new page.
    r = await client.post("/api/v1/events", json=EVENT)
    assert r.json()["results"][0] == {"key": "nightly-db", "action": "updated",
                                      "incident_id": first["incident_id"]}
    assert notify.await_count == 1
    args, kwargs = notify.await_args
    assert args[0] == "🔴 Incident: Nightly DB backup failed"
    assert kwargs == {"severity": "critical", "link_path": f"/incidents/{first['incident_id']}"}


async def test_incident_is_stored_under_the_external_rule():
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock):
            await client.post("/api/v1/events", json=EVENT)
            await client.post("/api/v1/events", json=EVENT)                       # unchanged repeat
            await client.post("/api/v1/events", json={**EVENT, "summary": "still failing"})
        (incident,) = await _incidents(sf)
        assert incident.rule == ext.RULE and incident.status == "open"
        assert incident.severity == "critical" and incident.host_ids == "[]"
        assert incident.host_ids_hash == ext.event_hash("backup-script", "nightly-db")
        # created + one changed summary; the unchanged repeat left no trace
        events = await _events(sf, incident.id)
        assert [(e.event_type, e.summary) for e in events] == [
            ("created", "pg_dump exited with 1"), ("external", "still failing")]


async def test_resolved_closes_the_incident_and_notifies():
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            created = (await client.post("/api/v1/events", json=EVENT)).json()["results"][0]
            r = await client.post("/api/v1/events", json={
                "source": "backup-script", "key": "nightly-db", "status": "resolved"})
        assert r.json()["results"][0] == {"key": "nightly-db", "action": "resolved",
                                          "incident_id": created["incident_id"]}
        (incident,) = await _incidents(sf)
        assert incident.status == "resolved" and incident.resolved_at is not None
        assert notify.await_args.args[0] == "✅ Resolved: Nightly DB backup failed"
        assert notify.await_args.kwargs["link_path"] == f"/incidents/{incident.id}"


async def test_resolved_without_an_open_incident_is_ignored(client, notify):
    r = await client.post("/api/v1/events", json={
        "source": "backup-script", "key": "never-fired", "status": "resolved"})
    assert r.status_code == 200
    assert r.json()["results"][0]["action"] == "ignored"
    notify.assert_not_awaited()


async def test_a_new_firing_after_resolve_opens_a_new_incident():
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock):
            await client.post("/api/v1/events", json=EVENT)
            await client.post("/api/v1/events", json={**EVENT, "status": "resolved"})
            r = await client.post("/api/v1/events", json=EVENT)
        assert r.json()["results"][0]["action"] == "created"
        assert [i.status for i in await _incidents(sf)] == ["resolved", "open"]


async def test_same_key_from_another_source_is_another_incident(client, notify):
    a = (await client.post("/api/v1/events", json=EVENT)).json()["results"][0]
    b = (await client.post("/api/v1/events", json={**EVENT, "source": "other"})).json()["results"][0]
    assert b["action"] == "created" and a["incident_id"] != b["incident_id"]


async def test_severity_escalates_but_never_drops():
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock):
            await client.post("/api/v1/events", json={**EVENT, "severity": "warning"})
            await client.post("/api/v1/events", json={**EVENT, "severity": "critical"})
            await client.post("/api/v1/events", json={**EVENT, "severity": "info"})
        (incident,) = await _incidents(sf)
        assert incident.severity == "critical"


async def test_a_batch_with_firing_and_resolve_for_the_same_key(client, notify):
    r = await client.post("/api/v1/events", json=[EVENT, {**EVENT, "status": "resolved"}])
    assert [x["action"] for x in r.json()["results"]] == ["created", "resolved"]


@pytest.mark.parametrize("bad", [
    {**EVENT, "source": ""},
    {**EVENT, "source": "has space"},
    {**EVENT, "key": ""},
    {**EVENT, "status": "exploded"},
    {**EVENT, "severity": "apocalyptic"},
    {**EVENT, "title": ""},
    {**EVENT, "title": {"nested": 1}},
    "just a string",
    [],
])
async def test_invalid_events_are_rejected_and_nothing_is_stored(bad):
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            r = await client.post("/api/v1/events", json=bad)
        assert r.status_code == 400, r.text
        assert await _incidents(sf) == []
        notify.assert_not_awaited()


async def test_one_invalid_event_rejects_the_whole_batch():
    async with make_client() as (client, sf):
        r = await client.post("/api/v1/events", json=[EVENT, {**EVENT, "severity": "nope"}])
        assert r.status_code == 400
        assert await _incidents(sf) == []


async def test_invalid_json_is_a_400(client):
    r = await client.post("/api/v1/events", content=b"{not json",
                          headers={"content-type": "application/json"})
    assert r.status_code == 400


async def test_a_failing_notification_does_not_lose_the_incident():
    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock, side_effect=RuntimeError("smtp")):
            r = await client.post("/api/v1/events", json=EVENT)
        assert r.status_code == 200
        assert len(await _incidents(sf)) == 1


# ── Auth ─────────────────────────────────────────────────────────────────────

async def test_needs_a_key(auth_client):
    client, _sf = auth_client
    assert (await client.post("/api/v1/events", json=EVENT)).status_code == 401


async def test_readonly_key_is_refused_editor_key_is_accepted(auth_client, notify):
    client, sf = auth_client
    readonly = await _api_key(sf, "readonly")
    editor = await _api_key(sf, "editor")
    r = await client.post("/api/v1/events", json=EVENT, headers={"X-API-Key": readonly})
    assert r.status_code == 403
    r = await client.post("/api/v1/events", json=EVENT, headers={"X-API-Key": editor})
    assert r.status_code == 200 and r.json()["results"][0]["action"] == "created"


async def test_bearer_api_key_works_without_csrf_token(auth_client, notify):
    """Alertmanager can only send an Authorization header."""
    client, sf = auth_client
    client.headers.pop("x-csrf-token", None)
    key = await _api_key(sf, "editor")
    r = await client.post("/api/v1/events/alertmanager", json=_AM,
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("auth", ["Bearer not-an-api-key", "Bearer ng_wrongwrongwrong", "Basic abc"])
async def test_other_bearer_tokens_are_not_api_keys(auth_client, auth):
    client, _sf = auth_client
    r = await client.post("/api/v1/events", json=EVENT, headers={"Authorization": auth})
    assert r.status_code == 401


# ── Auto-resolve must leave these incidents alone ────────────────────────────

async def test_correlation_auto_resolve_does_not_close_external_incidents():
    from services.correlation import _auto_resolve

    async with make_client() as (client, sf):
        with patch("notifications.notify", new_callable=AsyncMock):
            await client.post("/api/v1/events", json=EVENT)
        async with sf() as s:
            with tenant_scope(1):
                resolved = await _auto_resolve(s, offline_hosts=[])
                await s.commit()
        assert resolved == []
        (incident,) = await _incidents(sf)
        assert incident.status == "open"


# ── Alertmanager ─────────────────────────────────────────────────────────────

_AM = {
    "version": "4", "status": "firing", "receiver": "nodeglow",
    "alerts": [
        {"status": "firing", "fingerprint": "aaa111",
         "labels": {"alertname": "HighCPU", "instance": "web01:9100", "severity": "critical"},
         "annotations": {"summary": "CPU above 95% for 10m"}},
        {"status": "firing", "fingerprint": "bbb222",
         "labels": {"alertname": "DiskFilling", "severity": "weird"},
         "annotations": {"description": "/var fills in 4h"}},
    ],
}


def test_alertmanager_mapping():
    high, disk = ext.parse_alertmanager(_AM)
    assert (high.source, high.key, high.status, high.severity) == (
        "alertmanager", "aaa111", "firing", "critical")
    assert high.title == "HighCPU on web01:9100"
    assert high.summary.startswith("CPU above 95% for 10m\n") and "severity=critical" in high.summary
    # an unknown severity label is a warning, a missing instance is left out
    assert (disk.title, disk.severity) == ("DiskFilling", "warning")
    assert disk.summary.startswith("/var fills in 4h")


def test_alertmanager_without_fingerprint_keys_by_labels():
    alert = {"status": "firing", "labels": {"alertname": "X", "job": "a"}}
    (one,) = ext.parse_alertmanager({"alerts": [alert]})
    (same,) = ext.parse_alertmanager({"alerts": [{**alert, "annotations": {"summary": "changed"}}]})
    (other,) = ext.parse_alertmanager({"alerts": [{"status": "firing",
                                                   "labels": {"alertname": "X", "job": "b"}}]})
    assert one.key == same.key != other.key


@pytest.mark.parametrize("bad", [
    {}, {"alerts": "nope"}, {"alerts": ["x"]}, {"alerts": [{"status": "firing"}]},
    {"alerts": [{"fingerprint": "f", "labels": {}}] * (ext.MAX_ALERTS + 1)},
])
def test_alertmanager_rejects_what_it_cannot_read(bad):
    with pytest.raises(ext.EventError):
        ext.parse_alertmanager(bad)


async def test_alertmanager_firing_then_resolved(client, notify):
    r = await client.post("/api/v1/events/alertmanager", json=_AM)
    assert [x["action"] for x in r.json()["results"]] == ["created", "created"]

    resolved = {**_AM, "status": "resolved",
                "alerts": [{**_AM["alerts"][0], "status": "resolved"}]}
    r = await client.post("/api/v1/events/alertmanager", json=resolved)
    assert [x["action"] for x in r.json()["results"]] == ["resolved"]
    assert notify.await_count == 3

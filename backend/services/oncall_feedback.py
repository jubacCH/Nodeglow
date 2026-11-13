"""The way back from an on-call service: an acknowledge or resolve done in
PagerDuty reaches the Nodeglow incident it belongs to.

PagerDuty (webhook subscription, version 3) posts an ``event`` whose
``data.incident_key`` is the deduplication key Nodeglow sent with the
trigger (``notification_channels.dedup_key``): ``nodeglow-<scope>-incident-<id>``.
The scope ties the key to this installation's public URL, so an event for an
incident of another Nodeglow that pages into the same PagerDuty service is
not applied here.

Only incidents are handled. Alerts that were keyed by title (rule
notifications without an incident) have nothing to acknowledge.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from models.incident import Incident, IncidentEvent

_KEY_RE = re.compile(r"^nodeglow-([0-9a-f]{8})-incident-(\d{1,18})$")
ACKNOWLEDGED = "incident.acknowledged"
RESOLVED = "incident.resolved"


def scope_of(public_url: str) -> str:
    """Same scope as ``notification_channels.dedup_key``."""
    return hashlib.sha256((public_url or "").strip().lower().encode()).hexdigest()[:8]


def _who(event: dict) -> str:
    agent = event.get("agent") if isinstance(event.get("agent"), dict) else {}
    name = str(agent.get("summary") or "").strip()[:80]
    return f"{name} (PagerDuty)" if name else "PagerDuty"


async def apply_pagerduty_event(db, body, public_url: str) -> tuple[dict, Incident | None]:
    """Apply one PagerDuty webhook body to the current tenant's incidents.
    Does not commit.

    Returns ``(result, resolved_incident)``; ``result["action"]`` is one of
    ``acknowledged``, ``resolved``, ``unchanged`` (already in that state) or
    ``ignored`` with a ``reason``. Nothing here raises on a malformed body:
    PagerDuty retries every non-2xx answer, and a retry cannot fix a body
    this code does not understand.
    """
    def ignored(reason: str) -> tuple[dict, None]:
        return {"action": "ignored", "reason": reason}, None

    event = body.get("event") if isinstance(body, dict) else None
    if not isinstance(event, dict):
        return ignored("no event object")
    event_type = str(event.get("event_type") or "")
    if event_type not in (ACKNOWLEDGED, RESOLVED):
        return ignored(f"event type {event_type or '?'} is not handled")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    match = _KEY_RE.match(str(data.get("incident_key") or ""))
    if not match:
        return ignored("not an alert for a Nodeglow incident")
    if match.group(1) != scope_of(public_url):
        return ignored("alert of another Nodeglow installation")

    incident = await db.get(Incident, int(match.group(2)))
    if incident is None:
        return ignored("incident not found")
    result = {"action": "unchanged", "incident_id": incident.id}
    who = _who(event)

    if event_type == ACKNOWLEDGED:
        if incident.status != "open":
            return result, None
        incident.status = "acknowledged"
        incident.acknowledged_by = who
        incident.updated_at = datetime.utcnow()
        db.add(IncidentEvent(incident_id=incident.id, event_type="acknowledged",
                             summary=f"Acknowledged by {who}"))
        result["action"] = "acknowledged"
        return result, None

    if incident.status == "resolved":
        return result, None
    incident.status = "resolved"
    incident.resolved_at = datetime.utcnow()
    incident.updated_at = datetime.utcnow()
    db.add(IncidentEvent(incident_id=incident.id, event_type="resolved",
                         summary=f"Resolved by {who}"))
    result["action"] = "resolved"
    return result, incident

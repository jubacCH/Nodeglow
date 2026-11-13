"""Incidents reported by other systems (inbound events).

Anything that can send an HTTP request — Prometheus Alertmanager, Uptime
Kuma, a backup script — can open and resolve Nodeglow incidents through
``POST /api/v1/events`` (routers/events.py). An event names its ``source``
and a ``key`` that is stable for the thing it is about; the pair identifies
the incident: a repeated "firing" updates the open incident instead of
opening a second one, and "resolved" closes it.

These incidents are resolved only by the sender (or by hand). Nodeglow cannot
check the condition itself, so the correlation engine's auto-resolve leaves
the rule alone — a sender that dies without sending "resolved" leaves its
incident open, which is the safe direction for a monitoring product.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from models.incident import Incident, IncidentEvent

RULE = "external_event"
STATUSES = ("firing", "resolved")
SEVERITIES = ("critical", "warning", "info")
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

MAX_SOURCE = 64
MAX_KEY = 256
MAX_TITLE = 200
MAX_SUMMARY = 4000
SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EventError(ValueError):
    """An event that cannot be accepted; the message is safe to return."""


@dataclass(frozen=True)
class ExternalEvent:
    source: str
    key: str
    status: str
    severity: str
    title: str
    summary: str


@dataclass(frozen=True)
class Notice:
    """A notification to send after the commit."""
    title: str
    summary: str
    severity: str
    incident_id: int


def _text(value, field: str, limit: int, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise EventError(f"{field} must be a string")
    text = str(value).strip()
    if required and not text:
        raise EventError(f"{field} is required")
    return text[:limit]


def parse_event(body) -> ExternalEvent:
    """Validate one event of the generic format. Raises :class:`EventError`."""
    if not isinstance(body, dict):
        raise EventError("an event must be a JSON object")
    source = _text(body.get("source"), "source", MAX_SOURCE)
    if not SOURCE_RE.match(source):
        raise EventError("source may only contain letters, digits, '.', '_' and '-' (max 64)")
    key = _text(body.get("key"), "key", MAX_KEY)
    status = _text(body.get("status") or "firing", "status", 16).lower()
    if status not in STATUSES:
        raise EventError("status must be 'firing' or 'resolved'")
    severity = _text(body.get("severity") or "warning", "severity", 16).lower()
    if severity not in SEVERITIES:
        raise EventError("severity must be 'critical', 'warning' or 'info'")
    title = _text(body.get("title"), "title", MAX_TITLE, required=status == "firing")
    summary = _text(body.get("summary"), "summary", MAX_SUMMARY, required=False)
    return ExternalEvent(source=source, key=key, status=status, severity=severity,
                         title=title or key, summary=summary or title or key)


def event_hash(source: str, key: str) -> str:
    """Dedup hash of an external incident (``Incident.host_ids_hash``)."""
    return hashlib.sha256(f"{source}\x00{key}".encode()).hexdigest()


async def _open_incident(db, key_hash: str) -> Incident | None:
    return (await db.execute(
        select(Incident).where(
            Incident.rule == RULE,
            Incident.host_ids_hash == key_hash,
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalar_one_or_none()


async def _last_summary(db, incident_id: int) -> str | None:
    return (await db.execute(
        select(IncidentEvent.summary)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.timestamp.desc(), IncidentEvent.id.desc())
        .limit(1)
    )).scalar_one_or_none()


async def apply_event(db, event: ExternalEvent) -> tuple[dict, Notice | None]:
    """Apply one event to the incidents of the current tenant. Does not
    commit; the caller commits and then sends the returned notice.

    Returns ``({"key", "action", "incident_id"}, notice)`` with action one of
    ``created``, ``updated``, ``resolved`` or ``ignored`` (a "resolved" for
    which nothing is open).
    """
    key_hash = event_hash(event.source, event.key)
    incident = await _open_incident(db, key_hash)
    result = {"key": event.key, "action": "ignored", "incident_id": incident.id if incident else None}

    if event.status == "resolved":
        if incident is None:
            return result, None
        incident.status = "resolved"
        incident.resolved_at = datetime.utcnow()
        incident.updated_at = datetime.utcnow()
        reason = f"Resolved by {event.source}"
        if event.summary and event.summary != event.key:
            reason = f"{reason}: {event.summary}"
        db.add(IncidentEvent(incident_id=incident.id, event_type="resolved", summary=reason))
        result["action"] = "resolved"
        return result, Notice(f"✅ Resolved: {incident.title}", reason, "info", incident.id)

    if incident is not None:
        # A sender repeats a firing alert for as long as it fires. Only what
        # changed is recorded, so the timeline does not fill with repeats.
        incident.updated_at = datetime.utcnow()
        escalated = _SEVERITY_RANK[event.severity] > _SEVERITY_RANK.get(incident.severity, 0)
        if escalated:
            incident.severity = event.severity
        if escalated or await _last_summary(db, incident.id) != event.summary:
            db.add(IncidentEvent(incident_id=incident.id, event_type="external",
                                 summary=event.summary))
        result["action"] = "updated"
        return result, None

    from services.correlation import _find_or_create_incident

    incident = await _find_or_create_incident(
        db, RULE, event.title, event.severity, [0], "external", event.summary,
        key_hash=key_hash, send_notification=False, affected_host_ids=[],
    )
    result.update(action="created", incident_id=incident.id)
    return result, Notice(f"🔴 Incident: {event.title}", event.summary, event.severity, incident.id)


async def send_notices(notices: list[Notice]) -> list[str]:
    """Send the notices; returns the errors (never raises: the incidents are
    committed, a failed notification must not turn the request into a 500)."""
    from notifications import notify

    errors: list[str] = []
    for n in notices:
        try:
            await notify(n.title, n.summary, severity=n.severity,
                         link_path=f"/incidents/{n.incident_id}")
        except Exception as exc:
            errors.append(f"incident {n.incident_id}: {type(exc).__name__}")
    return errors


# ── Prometheus Alertmanager ──────────────────────────────────────────────────

_AM_SEVERITY = {
    "critical": "critical", "page": "critical", "error": "critical", "high": "critical",
    "warning": "warning", "warn": "warning", "medium": "warning",
    "info": "info", "informational": "info", "none": "info", "low": "info",
}
MAX_ALERTS = 200


def parse_alertmanager(body, source: str = "alertmanager") -> list[ExternalEvent]:
    """Events for an Alertmanager webhook payload (version 4)."""
    if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
        raise EventError("not an Alertmanager webhook payload (no 'alerts' list)")
    alerts = body["alerts"]
    if len(alerts) > MAX_ALERTS:
        raise EventError(f"too many alerts in one request (max {MAX_ALERTS}); "
                         "lower group size in the Alertmanager receiver")
    events: list[ExternalEvent] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            raise EventError("every alert must be a JSON object")
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        label_text = ", ".join(f"{k}={labels[k]}" for k in sorted(labels))
        key = str(alert.get("fingerprint") or "").strip()
        if not key:
            if not labels:
                raise EventError("an alert needs a fingerprint or labels")
            key = hashlib.sha256(label_text.encode()).hexdigest()[:32]
        name = str(labels.get("alertname") or "Alert")
        instance = str(labels.get("instance") or "").strip()
        title = f"{name} on {instance}" if instance else name
        text = str(annotations.get("summary") or annotations.get("description") or title)
        if label_text:
            text = f"{text}\n{label_text}"
        # An unknown severity label is a warning: never quieter than the
        # sender may have meant, never a page for a label we cannot read.
        severity = _AM_SEVERITY.get(str(labels.get("severity") or "").strip().lower(), "warning")
        status = str(alert.get("status") or body.get("status") or "firing").strip().lower()
        events.append(parse_event({
            "source": source, "key": key, "status": status, "severity": severity,
            "title": title, "summary": text,
        }))
    return events

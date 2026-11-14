"""Incidents for conditions a job re-evaluates as a whole on every run.

Certificate expiry and backup compliance have no correlation cycle: a job
looks at every certificate or every backup job and knows, each time, the
complete set of things that are wrong. ``sync_rule`` turns that set into
incidents: one per key, opened when the key appears, updated when its
severity or wording changes, resolved when the key is gone.

Notifications are returned, not sent: the caller commits first and sends
afterwards (``send``), so a notification never names an incident that was
rolled back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from models.incident import Incident, IncidentEvent

log = logging.getLogger("nodeglow.condition_incidents")

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class Condition:
    """One thing that is wrong right now."""

    title: str
    severity: str
    summary: str
    host_ids: list[int] = field(default_factory=list)
    # Keys of narrower incidents this one stands for. An open incident under
    # one of them is closed without a "resolved" notification: its problem is
    # not over, it is reported here from now on.
    absorbs: frozenset[str] = frozenset()


@dataclass
class Note:
    title: str
    summary: str
    severity: str
    incident_id: int
    # Where the notification links to; the incident itself unless given.
    link_path: str | None = None


async def sync_rule(db, rule: str, wanted: dict[str, Condition], *,
                    event_type: str, resolved_summary: str) -> list[Note]:
    """Make the open incidents of ``rule`` match ``wanted`` (key -> condition).

    Returns the notifications to send after the caller's commit: one for each
    incident opened, escalated to a higher severity, or resolved. An incident
    that a wanted condition absorbs is closed without one.
    """
    from services.correlation import _find_or_create_incident

    open_now = (await db.execute(
        select(Incident).where(Incident.rule == rule,
                               Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()
    by_key: dict[str, Incident] = {}
    for incident in open_now:
        by_key.setdefault(incident.host_ids_hash or "", incident)

    notes: list[Note] = []
    for key, cond in wanted.items():
        existing = by_key.get(key)
        incident = await _find_or_create_incident(
            db, rule=rule, title=cond.title, severity=cond.severity, host_ids=[],
            event_type=event_type, summary=cond.summary, key_hash=key,
            send_notification=False, affected_host_ids=cond.host_ids,
        )
        if existing is None:
            await db.flush()
            notes.append(Note(f"🔴 Incident: {cond.title}", cond.summary, cond.severity, incident.id))
            continue
        escalated = _SEVERITY_RANK.get(cond.severity, 0) > _SEVERITY_RANK.get(incident.severity, 0)
        incident.title = cond.title
        incident.severity = cond.severity
        if escalated:
            notes.append(Note(f"🔴 Now {cond.severity}: {cond.title}", cond.summary,
                              cond.severity, incident.id))

    absorbed_by = {key: cond for cond in wanted.values() for key in cond.absorbs}
    for key, incident in by_key.items():
        if key in wanted:
            continue
        incident.status = "resolved"
        incident.resolved_at = datetime.utcnow()
        if key in absorbed_by:
            db.add(IncidentEvent(incident_id=incident.id, event_type="resolved",
                                 summary=f"Continued as: {absorbed_by[key].title}"))
            continue
        db.add(IncidentEvent(incident_id=incident.id, event_type="resolved",
                             summary=resolved_summary))
        notes.append(Note(f"✅ Resolved: {incident.title}", resolved_summary, "info", incident.id))
    return notes


async def send(notes: list[Note]) -> None:
    for note in notes:
        try:
            from notifications import notify
            await notify(note.title, note.summary, severity=note.severity,
                         link_path=note.link_path or f"/incidents/{note.incident_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to send incident notification: %s", exc)

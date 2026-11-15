"""Evidence on incidents: write and read ``IncidentRelation`` rows.

Concept F, phase 3 (docs/specs/2026-11-15-concept-f-phase-3-design.md).
The correlation rules call :func:`record` when they create or refresh an
incident. Labels are honest by construction:

* ``rule`` — written for every incident, from the rule that fired.
* ``confirmed`` — only from a structural link the rule already used: the
  topology parent its hosts share, the failed upstream device, the
  integration whose host is down.
* ``suspected`` — not written here (phase 3, PR 2).

Nothing here notifies, changes severity or touches other incidents.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select

from models.incident import Incident, IncidentRelation

log = logging.getLogger(__name__)

KINDS = ("confirmed", "rule", "suspected")
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}
# A relation not refreshed for this long is reported as stale (two correlation
# runs of 60 s plus slack).
STALE_AFTER_SECONDS = 180
MAX_SUMMARY = 500


async def record(
    db, incident: Incident, *, kind: str, source: str, summary: str,
    subject_kind: str | None = None, subject_id: int | None = None,
    detail: dict | None = None, now: datetime | None = None,
) -> IncidentRelation:
    """Insert or refresh one relation of ``incident`` (same kind, source, subject).

    A refresh updates the sentence and the numbers and moves ``last_seen_at``;
    ``first_seen_at`` stays.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown relation kind: {kind}")
    now = now or datetime.utcnow()
    text = (summary or "").strip()[:MAX_SUMMARY]
    payload = json.dumps(detail, separators=(",", ":"), sort_keys=True) if detail else None
    q = select(IncidentRelation).where(
        IncidentRelation.incident_id == incident.id,
        IncidentRelation.kind == kind,
        IncidentRelation.source == source,
        IncidentRelation.subject_kind.is_(None) if subject_kind is None else IncidentRelation.subject_kind == subject_kind,
        IncidentRelation.subject_id.is_(None) if subject_id is None else IncidentRelation.subject_id == subject_id,
    )
    row = (await db.execute(q)).scalars().first()
    if row is None:
        row = IncidentRelation(
            incident_id=incident.id, kind=kind, source=source,
            subject_kind=subject_kind, subject_id=subject_id,
            summary=text, detail=payload, first_seen_at=now, last_seen_at=now,
        )
        db.add(row)
    else:
        row.summary = text
        row.detail = payload
        row.last_seen_at = now
    return row


def set_cause(incident: Incident, kind: str | None, object_id: int | None) -> None:
    """Store the object the incident's hosts share (or clear it)."""
    if kind is None or object_id is None:
        incident.cause_kind, incident.cause_id = None, None
    else:
        incident.cause_kind, incident.cause_id = kind, int(object_id)


def shared_parent(topology: dict[int, int | None] | None, host_ids: list[int]) -> int | None:
    """The topology parent all of ``host_ids`` have in common, else None.

    Only the direct parent: two hosts behind different access switches of the
    same core share no parent here, and nothing is claimed for them.
    """
    if not topology or len(host_ids) < 2:
        return None
    parents = {topology.get(h) for h in host_ids}
    if len(parents) != 1:
        return None
    (parent,) = parents
    return parent


async def for_incidents(db, incident_ids: list[int]) -> dict[int, list[IncidentRelation]]:
    """Relations per incident, ordered confirmed, rule, suspected, then newest first."""
    if not incident_ids:
        return {}
    rows = (await db.execute(
        select(IncidentRelation).where(IncidentRelation.incident_id.in_(incident_ids))
    )).scalars().all()
    out: dict[int, list[IncidentRelation]] = {}
    for r in sorted(rows, key=lambda r: (KIND_ORDER.get(r.kind, 9), -r.last_seen_at.timestamp())):
        out.setdefault(r.incident_id, []).append(r)
    return out


def serialize(r: IncidentRelation, *, now: datetime, names: dict[tuple[str, int], str] | None = None,
              open_incident: bool = True) -> dict:
    """API shape of one relation. ``stale`` only applies while the incident is open."""
    subject = None
    if r.subject_kind and r.subject_id is not None:
        subject = {"kind": r.subject_kind, "id": r.subject_id,
                   "name": (names or {}).get((r.subject_kind, r.subject_id))}
    age = (now - r.last_seen_at).total_seconds()
    return {
        "kind": r.kind,
        "source": r.source,
        "summary": r.summary,
        "subject": subject,
        "first_seen_at": _iso(r.first_seen_at),
        "last_seen_at": _iso(r.last_seen_at),
        "stale": bool(open_incident and age > STALE_AFTER_SECONDS),
    }


def cause_path(topology: dict[int, int | None] | None, host_id: int, max_depth: int = 8) -> list[int]:
    """Host ids from the topology root down to ``host_id`` (inclusive); loop-safe."""
    chain: list[int] = []
    seen: set[int] = set()
    cur: int | None = host_id
    while cur is not None and cur not in seen and len(chain) < max_depth:
        seen.add(cur)
        chain.append(cur)
        cur = (topology or {}).get(cur)
    return list(reversed(chain))


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


async def describe(
    db, incidents: list[Incident], *, topology: dict[int, int | None] | None,
    states: dict | None = None, now: datetime | None = None, limit: int | None = None,
) -> dict[int, dict]:
    """``{"relations": [...], "cause": {...} | None}`` per incident id, ready for the API.

    ``cause.path`` runs from the topology root down to the cause (hosts only);
    ``state`` comes from ``states`` (host id -> HostState) when given, else null.
    ``limit`` caps the relations per incident (the dashboard sends four).
    """
    from models.agent import Agent
    from models.integration import IntegrationConfig
    from models.ping import PingHost

    now = now or datetime.utcnow()
    by_inc = await for_incidents(db, [i.id for i in incidents])
    paths: dict[int, list[int]] = {}
    want: dict[str, set[int]] = {"host": set(), "probe": set(), "integration": set()}
    for inc in incidents:
        for r in by_inc.get(inc.id, []):
            if r.subject_kind in want and r.subject_id is not None:
                want[r.subject_kind].add(r.subject_id)
        if inc.cause_kind in want and inc.cause_id is not None:
            want[inc.cause_kind].add(inc.cause_id)
            if inc.cause_kind == "host":
                paths[inc.id] = cause_path(topology, inc.cause_id)
                want["host"].update(paths[inc.id])

    names: dict[tuple[str, int], str] = {}
    for kind, model in (("host", PingHost), ("probe", Agent), ("integration", IntegrationConfig)):
        ids = want[kind]
        if not ids:
            continue
        name_col = model.name
        for oid, name in (await db.execute(select(model.id, name_col).where(model.id.in_(ids)))).all():
            names[(kind, oid)] = name

    def state_of(hid: int) -> str | None:
        st = (states or {}).get(hid)
        return getattr(st, "state", None)

    out: dict[int, dict] = {}
    for inc in incidents:
        is_open = inc.status != "resolved"
        rels = [serialize(r, now=now, names=names, open_incident=is_open) for r in by_inc.get(inc.id, [])]
        cause = None
        if inc.cause_kind and inc.cause_id is not None:
            cause = {
                "kind": inc.cause_kind,
                "id": inc.cause_id,
                "name": names.get((inc.cause_kind, inc.cause_id)),
                "state": state_of(inc.cause_id) if inc.cause_kind == "host" else None,
                "path": [{"id": h, "name": names.get(("host", h)), "state": state_of(h)}
                         for h in paths.get(inc.id, [])],
            }
        out[inc.id] = {"relations": rels[:limit] if limit else rels, "cause": cause}
    return out

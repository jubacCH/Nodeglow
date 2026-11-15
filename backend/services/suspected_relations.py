"""Suspected relations of incidents (concept F, phase 3, PR 2).

docs/specs/2026-11-15-concept-f-phase-3-design.md, owner decisions 1 and 2:
a "suspected" relation is a hint for a person, shown in the UI only. It never
notifies, never changes severity, never opens, merges or resolves anything.

Only incidents opened in the last ``RECENT`` with recorded hosts are looked
at. A signal counts when it falls into ``WINDOW`` around the incident's onset
and is *not* structurally related to the incident's hosts (not one of them,
not an ancestor or descendant in the topology) — structural links are
"confirmed", not suspected. Two kinds of signal:

* another incident opened in the window (source ``incident``), with the
  offset in minutes;
* a host whose syslog volume is at least ``FACTOR`` times its learned
  baseline for this hour, with at least ``MIN_MESSAGES`` messages (source
  ``baseline``), with the measured rate, the baseline and the window.

No number, no relation. At most ``MAX_PER_INCIDENT`` per incident, strongest
first: baseline deviations by factor, then incidents by closeness in time.
The window is fixed around the onset, so these relations are never "stale".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from models.incident import Incident
from models.ping import PingHost
from services import incident_relations as relations
from services.host_state import parse_host_ids

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=10)
RECENT = timedelta(minutes=30)
MAX_PER_INCIDENT = 3
FACTOR = 3.0
MIN_MESSAGES = 20
OPEN = ("open", "acknowledged")


@dataclass
class _Candidate:
    rank: tuple
    source: str
    subject_kind: str
    subject_id: int
    summary: str
    detail: dict


def _related_hosts(topology: dict[int, int | None], host_ids: list[int]) -> set[int]:
    """The hosts, their ancestors and their descendants: structurally linked."""
    children: dict[int, list[int]] = {}
    for child, parent in topology.items():
        if parent is not None:
            children.setdefault(parent, []).append(child)
    out: set[int] = set()
    for h in host_ids:
        out.add(h)
        seen: set[int] = set()
        cur = topology.get(h)
        while cur is not None and cur not in seen:
            seen.add(cur)
            out.add(cur)
            cur = topology.get(cur)
        stack = list(children.get(h, []))
        while stack:
            c = stack.pop()
            if c not in out:
                out.add(c)
                stack.extend(children.get(c, []))
    return out


def _minutes(delta: timedelta) -> int:
    return int(round(abs(delta.total_seconds()) / 60))


def _clock(dt: datetime) -> str:
    return dt.strftime("%H:%M")


async def run(db, topology: dict[int, int | None] | None, now: datetime | None = None) -> int:
    """Write the suspected relations of recent incidents. Returns how many were written."""
    from services import clickhouse_client as ch
    from services.log_intelligence import load_effective_baselines

    now = now or datetime.utcnow()
    topology = topology or {}
    targets = [
        i for i in (await db.execute(
            select(Incident).where(Incident.status.in_(OPEN), Incident.created_at >= now - RECENT)
        )).scalars().all()
        if parse_host_ids(i.host_ids)
    ]
    if not targets:
        return 0

    earliest = min(t.created_at for t in targets) - WINDOW
    others = (await db.execute(
        select(Incident).where(Incident.created_at >= earliest)
    )).scalars().all()

    # Syslog volume per host since the earliest window start, one query.
    baselines = {k: b for k, b in (await load_effective_baselines(db, now)).items()
                 if k.startswith("host:") and (b.avg_rate or 0) > 0}
    counts: dict[str, int] = {}
    if baselines:
        try:
            counts = await ch.count_syslog_received_by_key(earliest, list(baselines))
        except Exception:
            log.warning("Suspected relations: syslog volume unavailable", exc_info=True)
            counts = {}
    minutes = max(1.0, (now - earliest).total_seconds() / 60)
    loud: list[tuple[int, int, float, float]] = []   # host id, messages, rate/h, baseline/h
    for key, bl in baselines.items():
        part = key.split(":", 1)[1]
        n = counts.get(key, 0)
        if not part.isdigit() or n < MIN_MESSAGES:
            continue
        rate = n * 60.0 / minutes
        if rate >= FACTOR * bl.avg_rate:
            loud.append((int(part), n, rate, float(bl.avg_rate)))
    names: dict[int, str] = {}
    if loud:
        names = dict((await db.execute(
            select(PingHost.id, PingHost.name).where(PingHost.id.in_([h for h, *_ in loud]))
        )).all())

    stored = await relations.for_incidents(db, [t.id for t in targets])
    written = 0
    for inc in targets:
        hosts = parse_host_ids(inc.host_ids) or []
        related = _related_hosts(topology, hosts)
        onset = inc.created_at
        cands: list[_Candidate] = []

        for host_id, n, rate, base in loud:
            if host_id in related or host_id not in names:
                continue
            factor = rate / base
            cands.append(_Candidate(
                rank=(0, -factor), source="baseline", subject_kind="host", subject_id=host_id,
                summary=(f"{names[host_id]} logged {rate:.0f} messages/h between {_clock(earliest)} and "
                         f"{_clock(now)} UTC, {factor:.1f}× its usual {base:.0f}/h for this hour. "
                         f"No topology link to these hosts."),
                detail={"messages": n, "rate_per_hour": round(rate, 1), "baseline_per_hour": round(base, 1),
                        "factor": round(factor, 1), "since": earliest.isoformat(), "until": now.isoformat()},
            ))

        for o in others:
            if o.id == inc.id or abs(o.created_at - onset) > WINDOW:
                continue
            o_hosts = parse_host_ids(o.host_ids) or []
            if related.intersection(o_hosts):
                continue  # shares a host or a topology branch: not a mere overlap in time
            offset = o.created_at - onset
            m = _minutes(offset)
            when = "at the same minute as" if m == 0 else f"{m} min {'before' if offset.total_seconds() < 0 else 'after'}"
            where = (f"on {len(o_hosts)} host{'s' if len(o_hosts) != 1 else ''} with no topology link to these hosts"
                     if o_hosts else "without recorded hosts")
            cands.append(_Candidate(
                rank=(1, m), source="incident", subject_kind="incident", subject_id=o.id,
                summary=f"Incident #{o.id} ({o.title}) opened {when} this one, {where}. Overlap in time only.",
                detail={"incident_id": o.id, "offset_minutes": round(offset.total_seconds() / 60, 1),
                        "rule": o.rule},
            ))

        # The cap holds for what is stored, not per run: known ones are
        # refreshed, new ones only fill free places.
        have = {(r.source, r.subject_kind, r.subject_id) for r in stored.get(inc.id, []) if r.kind == "suspected"}
        free = MAX_PER_INCIDENT - len(have)
        for c in sorted(cands, key=lambda c: c.rank):
            if (c.source, c.subject_kind, c.subject_id) not in have:
                if free <= 0:
                    continue
                free -= 1
            await relations.record(db, inc, kind="suspected", source=c.source, subject_kind=c.subject_kind,
                                   subject_id=c.subject_id, summary=c.summary, detail=c.detail, now=now)
            written += 1
    if written:
        await db.flush()
    return written

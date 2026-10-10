"""What changed since a point in time — derived from tables that already exist.

Nodeglow has no event store. Every item here is reconstructed from a table
that records the fact anyway; anything that is not recorded is left out
rather than guessed. What is derivable, by type:

``incident_opened``        incidents.created_at
``incident_resolved``      incidents.resolved_at
``incident_acknowledged``  incident_events (event_type "acknowledged")
``host_added``             ping_hosts.created_at (``discovered`` when the
                           source is an integration/scanner, not manual)
``port_discovered``        discovered_ports.first_seen
``agent_enrolled``         agents.created_at
``agent_updated``          audit_logs "agent.version_change" — written by the
                           heartbeat since this feature; older updates are lost
``maintenance_started``    maintenance windows: occurrences computed from the
                           schedule; manual toggles from audit "maintenance.toggle"
``probe_silent``           probes that are silent NOW: last_seen + staleness
                           window. A probe that went silent and recovered in
                           between leaves only its self-check incident
                           ("Probe not reporting: …", an incident_opened item).
``config_change``          other audit_logs rows (admins only; logins excluded)

Not derivable (not recorded anywhere): per-host up/down flips outside
incidents (only in ClickHouse ping_checks, too costly for a feed), integration
error/recovery transitions (snapshots keep only their retention window and no
transition marker), and who viewed what.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from services import maintenance as maint_svc
from services.host_state import iso, probe_state_of
from services import probes as probe_svc

TYPES = (
    "incident_opened", "incident_resolved", "incident_acknowledged",
    "host_added", "port_discovered", "agent_enrolled", "agent_updated",
    "maintenance_started", "probe_silent", "config_change",
)
ADMIN_ONLY_TYPES = frozenset({"config_change"})

MAX_WINDOW_DAYS = 31
MAX_OFFSET = 5000

# Audit actions that are covered by a dedicated type, or are noise in a feed.
_AUDIT_EXCLUDED = ("auth.login", "auth.logout", "agent.version_change", "maintenance.toggle")


def _item(type_, at, title, obj_kind, obj_id, obj_name, **detail):
    return {
        "type": type_,
        "at": iso(at),
        "title": title,
        "object": {"kind": obj_kind, "id": obj_id, "name": obj_name},
        "detail": {k: v for k, v in detail.items() if v is not None} or None,
        "_sort": at,
    }


async def _count(db, model_col, *conds) -> int:
    return (await db.execute(select(func.count(model_col)).where(*conds))).scalar() or 0


async def collect(
    db,
    since: datetime,
    until: datetime | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    types: set[str] | None = None,
    is_admin: bool = False,
) -> dict:
    """Changes in ``[since, until)`` newest first, plus a count per type.

    Each source is read at most ``offset + limit`` rows deep, so merging the
    sources and slicing yields the exact page. Counts are full counts.
    """
    from models.agent import Agent
    from models.audit import AuditLog
    from models.discovered_port import DiscoveredPort
    from models.incident import Incident, IncidentEvent
    from models.ping import PingHost

    until = until or datetime.utcnow()
    since = max(since, until - timedelta(days=MAX_WINDOW_DAYS))
    offset = max(0, min(int(offset), MAX_OFFSET))
    depth = offset + limit
    wanted = set(types or TYPES)
    if not is_admin:
        wanted -= ADMIN_ONLY_TYPES

    items: list[dict] = []
    counts: dict[str, int] = {}

    if "incident_opened" in wanted:
        conds = (Incident.created_at >= since, Incident.created_at < until)
        counts["incident_opened"] = await _count(db, Incident.id, *conds)
        for inc in (await db.execute(
            select(Incident).where(*conds).order_by(Incident.created_at.desc()).limit(depth)
        )).scalars():
            items.append(_item("incident_opened", inc.created_at, f"#{inc.id} {inc.title}",
                               "incident", inc.id, inc.title, severity=inc.severity, rule=inc.rule,
                               status=inc.status))

    if "incident_resolved" in wanted:
        conds = (Incident.resolved_at.isnot(None), Incident.resolved_at >= since, Incident.resolved_at < until)
        counts["incident_resolved"] = await _count(db, Incident.id, *conds)
        for inc in (await db.execute(
            select(Incident).where(*conds).order_by(Incident.resolved_at.desc()).limit(depth)
        )).scalars():
            items.append(_item("incident_resolved", inc.resolved_at, f"#{inc.id} {inc.title}",
                               "incident", inc.id, inc.title, severity=inc.severity,
                               opened_at=iso(inc.created_at),
                               duration_seconds=int((inc.resolved_at - inc.created_at).total_seconds())))

    if "incident_acknowledged" in wanted:
        conds = (IncidentEvent.event_type == "acknowledged",
                 IncidentEvent.timestamp >= since, IncidentEvent.timestamp < until)
        counts["incident_acknowledged"] = await _count(db, IncidentEvent.id, *conds)
        rows = (await db.execute(
            select(IncidentEvent, Incident.title)
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .where(*conds).order_by(IncidentEvent.timestamp.desc()).limit(depth)
        )).all()
        for ev, title in rows:
            items.append(_item("incident_acknowledged", ev.timestamp, f"#{ev.incident_id} {title}",
                               "incident", ev.incident_id, title, summary=ev.summary))

    if "host_added" in wanted:
        conds = (PingHost.created_at >= since, PingHost.created_at < until)
        counts["host_added"] = await _count(db, PingHost.id, *conds)
        for h in (await db.execute(
            select(PingHost).where(*conds).order_by(PingHost.created_at.desc()).limit(depth)
        )).scalars():
            source = h.source or "manual"
            discovered = source != "manual"
            title = f"New host discovered: {h.name}" if discovered else f"Host added: {h.name}"
            items.append(_item("host_added", h.created_at, title, "host", h.id, h.name,
                               source=source, discovered=discovered, source_detail=h.source_detail))

    if "port_discovered" in wanted:
        conds = (DiscoveredPort.first_seen >= since, DiscoveredPort.first_seen < until)
        counts["port_discovered"] = await _count(db, DiscoveredPort.id, *conds)
        rows = (await db.execute(
            select(DiscoveredPort, PingHost.name)
            .join(PingHost, PingHost.id == DiscoveredPort.host_id)
            .where(*conds).order_by(DiscoveredPort.first_seen.desc()).limit(depth)
        )).all()
        for dp, host_name in rows:
            items.append(_item("port_discovered", dp.first_seen,
                               f"{host_name}: port {dp.port}/{dp.protocol} open", "host", dp.host_id,
                               host_name, port=dp.port, service=dp.service, status=dp.status))

    if "agent_enrolled" in wanted:
        conds = (Agent.created_at >= since, Agent.created_at < until)
        counts["agent_enrolled"] = await _count(db, Agent.id, *conds)
        for a in (await db.execute(
            select(Agent).where(*conds).order_by(Agent.created_at.desc()).limit(depth)
        )).scalars():
            items.append(_item("agent_enrolled", a.created_at, f"Agent enrolled: {a.name}",
                               "agent", a.id, a.name, platform=a.platform))

    audit_types = {"agent_updated", "maintenance_started", "config_change"} & wanted
    if audit_types:
        base = (AuditLog.timestamp >= since, AuditLog.timestamp < until)
        if "agent_updated" in wanted:
            conds = base + (AuditLog.action == "agent.version_change",)
            counts["agent_updated"] = await _count(db, AuditLog.id, *conds)
            for row in (await db.execute(
                select(AuditLog).where(*conds).order_by(AuditLog.timestamp.desc()).limit(depth)
            )).scalars():
                d = _json(row.details)
                items.append(_item("agent_updated", row.timestamp,
                                   f"Agent {row.target_name} updated to {d.get('to', '?')}",
                                   "agent", row.target_id, row.target_name,
                                   **{"from": d.get("from"), "to": d.get("to")}))
        if "config_change" in wanted:
            conds = base + (AuditLog.action.notin_(_AUDIT_EXCLUDED),)
            counts["config_change"] = await _count(db, AuditLog.id, *conds)
            for row in (await db.execute(
                select(AuditLog).where(*conds).order_by(AuditLog.timestamp.desc()).limit(depth)
            )).scalars():
                items.append(_item("config_change", row.timestamp,
                                   f"{row.action}: {row.target_name or row.target_type or ''}".strip(),
                                   row.target_type or "setting", row.target_id, row.target_name,
                                   action=row.action, by=row.username))

    if "maintenance_started" in wanted:
        found: list[dict] = []
        for w in await maint_svc.load_windows(db):
            for start, end in maint_svc.occurrences_between(w, since, until):
                scope = "all hosts" if w.all_hosts else f"{len(w.host_ids)} host(s)"
                found.append(_item("maintenance_started", start, f"Maintenance started: {w.name}",
                                   "maintenance_window", w.id, w.name, ends_at=iso(end), scope=scope,
                                   host_ids=sorted(w.host_ids) if not w.all_hosts else None))
        conds = (AuditLog.timestamp >= since, AuditLog.timestamp < until,
                 AuditLog.action == "maintenance.toggle")
        manual = (await db.execute(
            select(AuditLog).where(*conds).order_by(AuditLog.timestamp.desc()).limit(depth)
        )).scalars().all()
        manual_total = await _count(db, AuditLog.id, *conds)
        for row in manual:
            found.append(_item("maintenance_started", row.timestamp,
                               f"Maintenance toggled: {row.target_name}", "host", row.target_id,
                               row.target_name, manual=True, by=row.username))
        counts["maintenance_started"] = len(found) - len(manual) + manual_total
        items.extend(found)

    if "probe_silent" in wanted:
        until_ts = (until - datetime(1970, 1, 1)).total_seconds()
        found = []
        for a in (await db.execute(select(Agent).where(Agent.is_probe == True))).scalars():  # noqa: E712
            st = probe_state_of(a)
            if not probe_svc.is_stale(st, until_ts):
                continue
            if a.last_seen is not None:
                silent_at = a.last_seen + timedelta(seconds=probe_svc.staleness_window(a.probe_interval_seconds))
                title = f"Probe went silent: {st.name}"
            else:
                silent_at = a.created_at
                title = f"Probe never reported: {st.name}"
            if silent_at and since <= silent_at < until:
                found.append(_item("probe_silent", silent_at, title, "probe", a.id, st.name,
                                   last_report=iso(a.last_seen)))
        counts["probe_silent"] = len(found)
        items.extend(found)

    items.sort(key=lambda i: i["_sort"] or datetime.min, reverse=True)
    page = items[offset:offset + limit]
    for i in page:
        i.pop("_sort", None)
    total = sum(counts.values())
    return {
        "since": iso(since),
        "until": iso(until),
        "total": total,
        "counts": {t: counts.get(t, 0) for t in TYPES if t in wanted},
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "items": page,
    }


def _json(raw) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}

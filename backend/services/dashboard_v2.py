"""Read model for the E3 dashboard (GET /api/v2/dashboard) and the badges
(GET /api/v2/summary).

Every number here comes from a shared definition:

* host states: :mod:`services.host_state` (the same rule as every host list);
* "open incidents": status open or acknowledged (:mod:`services.incident_view`);
* integration status: the latest snapshot per configuration, standby = ok.

The dashboard loads the base data once (enabled hosts, their newest check,
maintenance windows, open incidents, topology) and every section reads from
that; each section is isolated, so one failing source yields ``null`` plus an
entry in ``errors`` instead of a failed page (IA §6.5 "Teilausfall").

Rules of honesty: no data is never "up", estimates say how they were made,
and fields that Nodeglow cannot derive are ``null`` with a reason — never
invented. See docs/design/05-dashboard-api.md.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import func, select

from services import host_state as hs
from services import incident_view as iv
from services import maintenance as maint_svc
from services import probes as probe_svc

log = logging.getLogger("nodeglow.dashboard_v2")

EPOCH = datetime(1970, 1, 1)
DEFAULT_AVAILABILITY_TARGET = 99.9
AVAILABILITY_DAYS = 30
INCIDENT_TREND_DAYS = 14
UPCOMING_HOURS = 24
UPCOMING_DAYS = 30
SPEEDTEST_HISTORY = 24
LATENCY_HOURS = 2
LATENCY_MAX_HOSTS = 200
MAX_CHILDREN_LISTED = 40
MAX_PARENTS_LISTED = 12


def _epoch(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds()


# ── Short TTL cache for the heavy ClickHouse aggregates ─────────────────────
# The 30-day availability scan reads a month of ping_checks and the 24 h syslog
# histogram a day of syslog_messages; both move slowly compared to how often a
# dashboard polls. Keyed by the database bind (held, so its id is not reused).

AVAILABILITY_TTL = 300.0
SYSLOG_TTL = 60.0
_ttl_cache: dict = {}


async def _cached(db, name: str, ttl: float, fn):
    try:
        bind = db.get_bind()
    except Exception:  # noqa: BLE001
        bind = None
    key = (id(bind), name)
    hit = _ttl_cache.get(key)
    now = time.monotonic()
    if bind is not None and hit and hit[0] is bind and now - hit[1] < ttl:
        return hit[2]
    value = await fn()
    if bind is not None:
        if len(_ttl_cache) > 32:
            _ttl_cache.clear()
        _ttl_cache[key] = (bind, now, value)
    return value


def clear_caches() -> None:
    _ttl_cache.clear()
    _wan_cache.clear()


# ── Base data shared by every section ────────────────────────────────────────


@dataclass
class Base:
    now: datetime
    hosts: list                      # enabled hosts
    all_hosts: list                  # including disabled
    latest: dict
    windows: list
    states: dict                     # host_id -> HostState (enabled hosts)
    open_incidents: list
    incidents_by_host: dict
    topology: dict | None = None
    provenance: dict = field(default_factory=dict)
    gateways: set = field(default_factory=set)
    probes: list = field(default_factory=list)   # Agent rows with is_probe


async def load_base(db, now: datetime | None = None, *, with_topology: bool = True) -> Base:
    from models.agent import Agent
    from models.incident import Incident
    from models.ping import PingHost
    from services import clickhouse_client as ch
    from services.topology import cached_topology

    now = now or datetime.utcnow()
    all_hosts = (await db.execute(select(PingHost))).scalars().all()
    hosts = [h for h in all_hosts if h.enabled]
    latest = await ch.get_latest_ping_per_host([h.id for h in hosts]) if hosts else {}
    windows = await maint_svc.load_windows(db)
    open_incidents = (await db.execute(
        select(Incident).where(Incident.status.in_(iv.OPEN_STATUSES))
    )).scalars().all()
    incidents_by_host: dict[int, list[dict]] = {}
    for inc in open_incidents:
        for hid in hs.parse_host_ids(inc.host_ids) or []:
            incidents_by_host.setdefault(hid, []).append(
                {"id": inc.id, "rule": inc.rule, "title": inc.title, "severity": inc.severity})
    topology, provenance, gateways = (None, {}, set())
    if with_topology:
        try:
            topology, provenance, gateways = await cached_topology(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("topology unavailable for dashboard: %s", exc)
    states = await hs.host_states(db, hosts, now, latest=latest, windows=windows,
                                  topology=topology, incidents_by_host=incidents_by_host)
    probes = (await db.execute(select(Agent).where(Agent.is_probe == True))).scalars().all()  # noqa: E712
    return Base(now=now, hosts=hosts, all_hosts=all_hosts, latest=latest, windows=windows,
                states=states, open_incidents=open_incidents, incidents_by_host=incidents_by_host,
                topology=topology, provenance=provenance, gateways=gateways, probes=list(probes))


# ── Summary (badges) — one definition, also embedded in the dashboard ───────


async def integration_status(db) -> dict:
    """Enabled integrations by latest-snapshot status: ok / error / no_data.

    Reads only (type, id, ok, error) of the newest snapshot per config — the
    data_json payload (often hundreds of kB) is never loaded for a count.
    """
    from models.integration import IntegrationConfig, Snapshot

    configs = (await db.execute(
        select(IntegrationConfig.id, IntegrationConfig.type, IntegrationConfig.name)
        .where(IntegrationConfig.enabled == True)  # noqa: E712
    )).all()
    newest = (
        select(Snapshot.entity_type, Snapshot.entity_id, func.max(Snapshot.id).label("max_id"))
        .group_by(Snapshot.entity_type, Snapshot.entity_id)
        .subquery()
    )
    rows = (await db.execute(
        select(Snapshot.entity_type, Snapshot.entity_id, Snapshot.ok, Snapshot.error, Snapshot.timestamp)
        .join(newest, Snapshot.id == newest.c.max_id)
    )).all()
    latest = {(t, i): (ok, err, ts) for t, i, ok, err, ts in rows}
    ok = error = no_data = 0
    failing = []
    for cid, ctype, name in configs:
        snap = latest.get((ctype, cid))
        if snap is None:
            no_data += 1
        elif snap[0]:
            ok += 1  # standby members report ok with a marker: healthy
        else:
            error += 1
            failing.append({"id": cid, "type": ctype, "name": name, "error": snap[1],
                            "since_check": hs.iso(snap[2])})
    return {"total": len(configs), "ok": ok, "error": error, "no_data": no_data, "failing": failing}


def probe_rows(base: Base) -> list[dict]:
    now_ts = _epoch(base.now)
    counts: dict[int, int] = {}
    for h in base.hosts:
        if h.probe_id:
            counts[h.probe_id] = counts.get(h.probe_id, 0) + 1
    out = []
    for a in base.probes:
        st = hs.probe_state_of(a)
        out.append({
            "id": a.id, "name": st.name,
            "stale": probe_svc.is_stale(st, now_ts),
            "last_report": hs.iso(a.last_seen),
            "staleness_window_seconds": int(probe_svc.staleness_window(a.probe_interval_seconds)),
            "host_count": counts.get(a.id, 0),
        })
    return out


def incident_counts(incidents) -> dict:
    c = {"open": 0, "acknowledged": 0, "unacknowledged": 0, "critical": 0, "warning": 0, "info": 0}
    for inc in incidents:
        c["open"] += 1
        if inc.status == "acknowledged":
            c["acknowledged"] += 1
        else:
            c["unacknowledged"] += 1
        sev = inc.severity if inc.severity in ("critical", "warning", "info") else "info"
        c[sev] += 1
    return c


async def summary_from(db, base: Base, integrations: dict | None = None) -> dict:
    probes = probe_rows(base)
    integrations = integrations or await integration_status(db)
    counts = hs.counts(base.states.values())
    return {
        "generated_at": hs.iso(base.now),
        "incidents": incident_counts(base.open_incidents),
        "hosts": {
            "total": len(base.hosts),
            "by_state": counts,
            "attention": counts["down"] + counts["warning"] + counts["degraded"] + counts["unknown"],
        },
        "probes": {
            "total": len(probes),
            "stale": sum(1 for p in probes if p["stale"] and p["host_count"]),
            "stale_unused": sum(1 for p in probes if p["stale"] and not p["host_count"]),
        },
        "integrations": {k: integrations[k] for k in ("total", "ok", "error", "no_data")},
        "definitions": {
            "incidents.open": "status open or acknowledged",
            "hosts.by_state": "services.host_state — enabled hosts only",
            "probes.stale": "stale probes that have hosts assigned",
            "integrations.error": "enabled integrations whose newest snapshot failed",
        },
    }


async def build_summary(db, now: datetime | None = None) -> dict:
    base = await load_base(db, now, with_topology=False)
    return await summary_from(db, base)


# ── Sections ─────────────────────────────────────────────────────────────────


def section_health(base: Base, agents: dict, integrations: dict, probes: list[dict]) -> dict:
    states = list(base.states.values())
    counts = hs.counts(states)
    return {
        "counts": counts,
        "reasons": hs.reasons_by_state(states),
        "worst": hs.worst(s.state for s in states if s.state != hs.STATE_DISABLED),
        "totals": {
            "hosts": len(base.hosts),
            "disabled": len(base.all_hosts) - len(base.hosts),
            "with_current_data": sum(1 for s in states if s.state in hs.OBSERVED_STATES),
            "agents": agents["total"],
            "agents_reporting": agents["reporting"],
            "integrations": integrations["total"],
            "integrations_error": integrations["error"],
            "probes": len(probes),
            "probes_stale": sum(1 for p in probes if p["stale"] and p["host_count"]),
        },
    }


async def agent_counts(db, now: datetime) -> dict:
    from models.agent import Agent

    rows = (await db.execute(select(Agent.last_seen).where(Agent.enabled == True))).all()  # noqa: E712
    reporting = sum(1 for (seen,) in rows if seen and (now - seen).total_seconds() < 120)
    return {"total": len(rows), "reporting": reporting}


_wan_cache: dict = {}


async def section_internet(db, base: Base) -> dict | None:
    """Latest speedtest + the last 24 results, WAN status from UniFi if known."""
    from models.integration import IntegrationConfig, Snapshot

    configs = (await db.execute(
        select(IntegrationConfig.id, IntegrationConfig.type, IntegrationConfig.name)
        .where(IntegrationConfig.enabled == True,  # noqa: E712
               IntegrationConfig.type.in_(["speedtest", "unifi"]))
        .order_by(IntegrationConfig.id)
    )).all()
    speed_cfg = next((c for c in configs if c.type == "speedtest"), None)
    unifi_cfgs = [c for c in configs if c.type == "unifi"]

    latest = None
    history: list[dict] = []
    source = None
    if speed_cfg:
        rows = (await db.execute(
            select(Snapshot.timestamp, Snapshot.data_json)
            .where(Snapshot.entity_type == "speedtest", Snapshot.entity_id == speed_cfg.id,
                   Snapshot.ok == True)  # noqa: E712
            .order_by(Snapshot.timestamp.desc()).limit(SPEEDTEST_HISTORY)
        )).all()
        for ts, raw in reversed(rows):
            d = _json(raw)
            history.append({
                "at": hs.iso(ts),
                "download_mbps": d.get("download_mbps"),
                "upload_mbps": d.get("upload_mbps"),
                "latency_ms": d.get("ping_ms"),
            })
        if history:
            source = "speedtest"
            last = history[-1]
            latest = {**last, "server": _json(rows[0][1]).get("server_name") or None}

    wan = None
    gateway = None
    for ucfg in unifi_cfgs:
        extract = await _unifi_extract(db, ucfg.id)
        if extract is None:
            continue
        if extract["wan"] and wan is None:
            wan = {**extract["wan"], "source": "unifi", "integration": ucfg.name}
        if latest is None and extract["speedtest"]:
            source = "unifi"
            history = extract["speedtest"][-SPEEDTEST_HISTORY:]
            latest = {**history[-1], "server": "UniFi gateway"}
    if base.gateways:
        gw_id = sorted(base.gateways)[0]
        h = next((x for x in base.all_hosts if x.id == gw_id), None)
        if h is not None:
            st = base.states.get(h.id)
            gateway = {"id": h.id, "name": h.name, "state": st.state if st else None}

    if latest is None and wan is None:
        return None
    return {"source": source, "latest": latest, "history": history, "wan": wan, "gateway": gateway}


async def _unifi_extract(db, config_id: int) -> dict | None:
    """WAN + speedtest from the newest UniFi snapshot, parsed once per snapshot id."""
    from models.integration import Snapshot

    row = (await db.execute(
        select(Snapshot.id, Snapshot.ok, Snapshot.timestamp)
        .where(Snapshot.entity_type == "unifi", Snapshot.entity_id == config_id)
        .order_by(Snapshot.id.desc()).limit(1)
    )).first()
    if row is None or not row[1]:
        return None
    snap_key = (row[0], row[2])  # id + timestamp: a reused id never serves stale data
    hit = _wan_cache.get(config_id)
    if hit and hit[0] == snap_key:
        return hit[1]
    raw = (await db.execute(select(Snapshot.data_json).where(Snapshot.id == row[0]))).scalar()
    d = _json(raw)
    w = d.get("wan") or {}
    wan = None
    if w:
        status = str(w.get("status") or "unknown").lower()
        wan = {
            "status": "up" if status == "ok" else ("down" if status in ("error", "down") else status),
            "raw_status": w.get("status"),
            "latency_ms": w.get("latency"),
        }
    speed = []
    for e in reversed(d.get("speedtest") or []):  # stored newest first
        speed.append({"at": _unifi_ts(e.get("timestamp")), "download_mbps": e.get("download_mbps"),
                      "upload_mbps": e.get("upload_mbps"), "latency_ms": e.get("latency_ms")})
    extract = {"wan": wan, "speedtest": speed}
    _wan_cache[config_id] = (snap_key, extract)
    return extract


def _unifi_ts(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M").isoformat() + "Z"
    except ValueError:
        return str(value)


async def section_incidents(db, base: Base, tz_name: str) -> dict:
    from models.incident import Incident

    summaries = await _latest_summaries(db, [i.id for i in base.open_incidents])
    items = []
    for inc in sorted(base.open_incidents,
                      key=lambda i: (iv.SEVERITY_RANK.get(i.severity, 9), i.status == "acknowledged",
                                     -_epoch(i.created_at))):
        ids = hs.parse_host_ids(inc.host_ids)
        items.append({
            "id": inc.id, "title": inc.title, "severity": inc.severity, "rule": inc.rule,
            "status": inc.status, "acknowledged": inc.status == "acknowledged",
            "acknowledged_by": inc.acknowledged_by,
            "opened_at": hs.iso(inc.created_at),
            "age_seconds": int((base.now - inc.created_at).total_seconds()),
            "host_count": len(ids) if ids is not None else None,
            "host_ids": ids[:iv.MAX_HOSTS_PER_ITEM] if ids is not None else None,
            "summary": summaries.get(inc.id),
        })

    day_start_utc = _local_day_start_utc(base.now, tz_name)
    trend_since = day_start_utc - timedelta(days=INCIDENT_TREND_DAYS - 1)
    rows = (await db.execute(
        select(Incident.created_at, Incident.severity).where(Incident.created_at >= trend_since)
    )).all()
    per_day = []
    for d in range(INCIDENT_TREND_DAYS):
        start = trend_since + timedelta(days=d)
        per_day.append({"date": _local_date(start, tz_name), "count": 0,
                        "critical": 0, "warning": 0, "info": 0, "_s": start, "_e": start + timedelta(days=1)})
    for created, sev in rows:
        for b in per_day:
            if b["_s"] <= created < b["_e"]:
                b["count"] += 1
                b[sev if sev in ("critical", "warning", "info") else "info"] += 1
                break
    for b in per_day:
        b.pop("_s")
        b.pop("_e")

    resolved = (await db.execute(
        select(Incident).where(Incident.resolved_at.isnot(None), Incident.resolved_at >= day_start_utc)
        .order_by(Incident.resolved_at.desc()).limit(20)
    )).scalars().all()
    return {
        "counts": incident_counts(base.open_incidents),
        "items": items,
        "per_day": per_day,
        "resolved_today": [{
            "id": i.id, "title": i.title, "severity": i.severity,
            "opened_at": hs.iso(i.created_at), "resolved_at": hs.iso(i.resolved_at),
            "duration_seconds": int((i.resolved_at - i.created_at).total_seconds()),
        } for i in resolved],
        "day_starts_at": hs.iso(day_start_utc),
    }


async def _latest_summaries(db, ids: list[int]) -> dict[int, str]:
    from routers.dashboard import latest_incident_summaries

    return await latest_incident_summaries(db, ids) if ids else {}


def section_topology(base: Base) -> dict | None:
    if base.topology is None:
        return None
    topo = base.topology
    enabled = {h.id: h for h in base.hosts}
    children: dict[int, list[int]] = {}
    for child, parent in topo.items():
        if parent is not None and child in enabled and parent in enabled:
            children.setdefault(parent, []).append(child)

    def node(hid):
        h = enabled[hid]
        st = base.states.get(hid)
        return {"id": hid, "name": h.name, "state": st.state if st else None,
                "state_reason": st.reason if st else None}

    def subtree(hid, seen=None):
        seen = seen or set()
        out = []
        for c in children.get(hid, []):
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            out.extend(subtree(c, seen))
        return out

    parents = []
    for pid, kids in children.items():
        all_desc = subtree(pid)
        desc_states = [base.states[c].state for c in all_desc if c in base.states]
        own = base.states.get(pid)
        worst = hs.worst(desc_states + ([own.state] if own else []))
        counts = {s: n for s, n in hs.counts(base.states[c] for c in all_desc if c in base.states).items() if n}
        affected = worst in (hs.STATE_DOWN, hs.STATE_WARNING, hs.STATE_DEGRADED, hs.STATE_UNKNOWN)
        prov: dict[str, int] = {}
        for c in kids:
            p = base.provenance.get(c, "unknown")
            prov[p] = prov.get(p, 0) + 1
        entry = {
            **node(pid),
            "parent_id": topo.get(pid),
            "child_count": len(kids),
            "descendant_count": len(all_desc),
            "descendant_states": counts,
            "worst_state": worst,
            "affected": affected,
            "link_provenance": prov,
            "is_gateway": pid in base.gateways,
        }
        if affected:
            listed = sorted(kids, key=lambda c: hs.STATES.index(base.states[c].state)
                            if c in base.states else 99)[:MAX_CHILDREN_LISTED]
            entry["children"] = [{**node(c), "provenance": base.provenance.get(c),
                                  "child_count": len(children.get(c, []))} for c in listed]
        parents.append(entry)

    rank = {s: i for i, s in enumerate(hs.STATES)}
    roots = [p for p in parents if p["parent_id"] is None or p["parent_id"] not in enabled]
    roots.sort(key=lambda p: (not p["is_gateway"], -p["descendant_count"]))
    top = sorted(parents, key=lambda p: (rank.get(p["worst_state"], 99), -p["descendant_count"]))
    linked = {c for kids in children.values() for c in kids} | set(children)
    by_prov: dict[str, int] = {}
    for c, p in topo.items():
        if p is not None and c in enabled:
            by_prov[base.provenance.get(c, "unknown")] = by_prov.get(base.provenance.get(c, "unknown"), 0) + 1
    return {
        "roots": [{"id": r["id"], "name": r["name"], "state": r["state"], "is_gateway": r["is_gateway"],
                   "descendant_count": r["descendant_count"], "worst_state": r["worst_state"]}
                  for r in roots[:MAX_PARENTS_LISTED]],
        "parents": top[:MAX_PARENTS_LISTED],
        "parents_total": len(parents),
        "unlinked_hosts": len([h for h in enabled if h not in linked]),
        "links_by_provenance": by_prov,
        "internet_root": None,  # filled by the caller from the internet section
    }


def section_groups(base: Base, probes: list[dict]) -> list[dict]:
    """Honest grouping: "Direct" (core-checked) and one group per probe.

    Nodeglow has no site model; these are groups by who checks the hosts,
    labelled with ``kind`` so the UI cannot present them as sites.
    """
    direct = [h for h in base.hosts if not h.probe_id]
    groups = [{
        "kind": "direct", "id": None, "name": "Direct",
        "description": "Checked by this Nodeglow instance",
        "host_count": len(direct),
        "by_state": {k: v for k, v in hs.counts(base.states[h.id] for h in direct).items() if v},
        "fresh": None,
    }]
    by_probe: dict[int, list] = {}
    for h in base.hosts:
        if h.probe_id:
            by_probe.setdefault(h.probe_id, []).append(h)
    known = set()
    for p in probes:
        known.add(p["id"])
        members = by_probe.get(p["id"], [])
        groups.append({
            "kind": "probe", "id": p["id"], "name": p["name"],
            "description": "Checked by remote probe",
            "host_count": len(members),
            "by_state": {k: v for k, v in hs.counts(base.states[h.id] for h in members).items() if v},
            "fresh": not p["stale"],
            "last_report": p["last_report"],
            "staleness_window_seconds": p["staleness_window_seconds"],
        })
    orphaned = [h for pid, hs_ in by_probe.items() if pid not in known for h in hs_]
    if orphaned:
        groups.append({
            "kind": "probe", "id": None, "name": "Probe not found",
            "description": "Assigned to an agent that is not (or no longer) a probe",
            "host_count": len(orphaned),
            "by_state": {k: v for k, v in hs.counts(base.states[h.id] for h in orphaned).items() if v},
            "fresh": False,
        })
    return groups


async def section_upcoming(db, base: Base) -> dict:
    items = []
    horizon = base.now + timedelta(hours=UPCOMING_HOURS)
    host_names = {h.id: h.name for h in base.all_hosts}

    # Maintenance: active now, or starting within 24 h.
    for w in base.windows:
        active = maint_svc.occurrence_at(w, base.now)
        nxt = maint_svc.next_occurrence(w, base.now)
        scope_ids = sorted(w.host_ids) if not w.all_hosts else None
        scope = {"all_hosts": w.all_hosts, "host_ids": scope_ids,
                 "host_names": [host_names[i] for i in (scope_ids or [])[:5] if i in host_names]}
        if active:
            items.append({"kind": "maintenance", "phase": "active", "due_at": hs.iso(active[1]),
                          "starts_at": hs.iso(active[0]), "ends_at": hs.iso(active[1]),
                          "title": w.name, "object": {"kind": "maintenance_window", "id": w.id, "name": w.name},
                          "scope": scope})
        if nxt and nxt[0] <= horizon:
            items.append({"kind": "maintenance", "phase": "scheduled", "due_at": hs.iso(nxt[0]),
                          "starts_at": hs.iso(nxt[0]), "ends_at": hs.iso(nxt[1]),
                          "title": w.name, "object": {"kind": "maintenance_window", "id": w.id, "name": w.name},
                          "scope": scope})
    for h in base.all_hosts:
        if maint_svc.manual_maintenance(h, base.now) and h.maintenance_until:
            items.append({"kind": "maintenance", "phase": "active", "due_at": hs.iso(h.maintenance_until),
                          "starts_at": None, "ends_at": hs.iso(h.maintenance_until),
                          "title": f"{h.name} (manual)", "object": {"kind": "host", "id": h.id, "name": h.name},
                          "scope": {"all_hosts": False, "host_ids": [h.id], "host_names": [h.name]}})

    # Certificates expiring within 30 days.
    for h in base.hosts:
        days = h.ssl_expiry_days
        if days is not None and "https" in (h.check_type or "") and days <= UPCOMING_DAYS:
            items.append({"kind": "certificate", "due_at": hs.iso(base.now + timedelta(days=days)),
                          "days": days, "title": "Certificate expires", "estimated_due": True,
                          "object": {"kind": "host", "id": h.id, "name": h.name}, "source": "host check"})
    try:
        from routers.ssl_monitor import _get_integration_certs

        for c in await _get_integration_certs(db):
            days = c.get("days")
            if days is not None and days <= UPCOMING_DAYS:
                items.append({"kind": "certificate", "due_at": hs.iso(base.now + timedelta(days=days)),
                              "days": days, "title": "Certificate expires", "estimated_due": True,
                              "object": {"kind": "certificate", "id": None, "name": c.get("name")},
                              "source": c.get("source_label") or c.get("source")})
    except Exception as exc:  # noqa: BLE001
        log.warning("integration certificates unavailable: %s", exc)

    # Disk-full predictions within 30 days (linear trend, confidence >= 0.3).
    from services import predictions as pred_svc

    integ = await pred_svc.integration_predictions(db)
    agent = pred_svc.cached_predictions("agent", pred_svc.AGENT_CACHE_TTL)
    for key, p in list((integ or {}).items()) + list((agent or {}).items()):
        days = p.get("days_until_full")
        if days is None or days > UPCOMING_DAYS or (p.get("confidence") or 0) < 0.3:
            continue
        name = p.get("hostname") or p.get("agent_name") or p.get("config_name")
        items.append({"kind": "disk", "due_at": hs.iso(base.now + timedelta(days=days)), "days": days,
                      "title": f"{name} · {p.get('pool_name')} {p.get('current_pct')} %",
                      "estimated_due": True, "method": "linear trend over 14 days",
                      "object": {"kind": "agent" if key.startswith("agent-") else "integration",
                                 "id": p.get("agent_id") or p.get("config_id"), "name": name},
                      "current_pct": p.get("current_pct"), "trend_pct_per_day": p.get("trend_pct_per_day"),
                      "confidence": p.get("confidence"), "source": p.get("source")})

    items.sort(key=lambda i: i["due_at"] or "")
    return {"items": items, "agent_disk_predictions_ready": agent is not None}


async def section_latency(base: Base) -> dict | None:
    """Per-minute median/max latency of the hosts of the most severe open
    incident that recorded hosts, last 2 h (ClickHouse ping_checks)."""
    from services import clickhouse_client as ch

    candidates = [i for i in base.open_incidents if hs.parse_host_ids(i.host_ids)]
    if not candidates:
        return None
    inc = sorted(candidates, key=lambda i: (iv.SEVERITY_RANK.get(i.severity, 9), -_epoch(i.created_at)))[0]
    host_ids = hs.parse_host_ids(inc.host_ids)[:LATENCY_MAX_HOSTS]
    since = base.now - timedelta(hours=LATENCY_HOURS)
    rows = await ch.query(
        """
        SELECT toStartOfMinute(timestamp) AS minute,
               quantileIf(0.5)(latency_ms, success = 1) AS median_ms,
               maxIf(latency_ms, success = 1) AS max_ms,
               countIf(success = 1) AS ok,
               countIf(success = 0) AS failed
        FROM ping_checks
        WHERE host_id IN ({hids:Array(UInt32)}) AND timestamp >= {since:DateTime64(3)}
        GROUP BY minute ORDER BY minute
        """,
        {"hids": host_ids, "since": since},
    )
    points = []
    for r in rows:
        ok = int(r.get("ok") or 0)
        points.append({
            "t": hs.iso(r["minute"]) if isinstance(r.get("minute"), datetime) else str(r.get("minute")),
            "median_ms": _num(r.get("median_ms")) if ok else None,
            "max_ms": _num(r.get("max_ms")) if ok else None,
            "ok": ok, "failed": int(r.get("failed") or 0),
        })
    onset = inc.created_at
    before = [p["median_ms"] for p, r in zip(points, rows)
              if p["median_ms"] is not None and isinstance(r.get("minute"), datetime) and r["minute"] < onset]
    after = [p["median_ms"] for p, r in zip(points, rows)
             if p["median_ms"] is not None and isinstance(r.get("minute"), datetime) and r["minute"] >= onset]
    names = {h.id: h.name for h in base.all_hosts}
    return {
        "incident_id": inc.id, "title": inc.title, "severity": inc.severity,
        "host_ids": host_ids, "host_names": [names.get(i) for i in host_ids[:10]],
        "host_count": len(host_ids),
        "onset_at": hs.iso(onset),
        "window_hours": LATENCY_HOURS,
        "median_before_ms": round(median(before), 2) if before else None,
        "median_since_ms": round(median(after), 2) if after else None,
        "points": points,
    }


async def section_syslog(db, base: Base) -> dict:
    from services import clickhouse_client as ch

    since = base.now - timedelta(hours=24)
    where, params = ch.received_since_clause(since)

    async def _buckets():
        return await ch.query(
            f"""
            SELECT toStartOfFifteenMinutes(received_at) AS bucket,
                   count() AS cnt,
                   countIf(severity <= 3) AS errors
            FROM syslog_messages
            WHERE {where}
            GROUP BY bucket ORDER BY bucket
            """,
            params,
        )

    rows = await _cached(db, "syslog_buckets", SYSLOG_TTL, _buckets)
    buckets = [{"t": hs.iso(r["bucket"]) if isinstance(r.get("bucket"), datetime) else str(r.get("bucket")),
                "count": int(r.get("cnt") or 0), "errors": int(r.get("errors") or 0)} for r in rows]
    total = sum(b["count"] for b in buckets)
    errors = sum(b["errors"] for b in buckets)
    recent_where, recent_params = ch.received_since_clause(base.now - timedelta(minutes=15), "recent")
    current = int(await ch.query_scalar(
        f"SELECT count() FROM syslog_messages WHERE {recent_where}", recent_params) or 0)

    usual = None
    try:
        from services.log_intelligence import load_effective_baselines

        baselines = {k: b for k, b in (await load_effective_baselines(db, base.now)).items()
                     if not k.startswith("host:")}
        if baselines:
            avg = sum(b.avg_rate or 0.0 for b in baselines.values())
            std = math.sqrt(sum((b.std_rate or 0.0) ** 2 for b in baselines.values()))
            usual = {"low_per_min": round(max(0.0, avg - std) / 60.0, 2),
                     "high_per_min": round((avg + std) / 60.0, 2),
                     "mean_per_min": round(avg / 60.0, 2),
                     "sources": len(baselines),
                     "method": "learned hourly baselines (mean ± 1σ) for this hour and weekday"}
    except Exception as exc:  # noqa: BLE001
        log.warning("syslog baselines unavailable: %s", exc)
    return {
        "bucket_minutes": 15,
        "buckets": buckets,
        "current_per_min": round(current / 15.0, 2),
        "usual": usual,
        "total_24h": total,
        "errors_24h": errors,
        "receiving": total > 0,
    }


async def section_availability(db, base: Base) -> dict:
    """30-day availability over the checks that actually ran.

    Maintenance is excluded because hosts in maintenance are not checked (no
    rows); no-data periods are excluded the same way — they produce no rows,
    so they never count as up. Disabled hosts are left out. The downtime
    figure is fleet-equivalent: (1 - availability) x the 30-day window.
    """
    from models.settings import get_setting
    from services import clickhouse_client as ch

    raw_target = await get_setting(db, "availability_target", None)
    try:
        target = float(raw_target) if raw_target not in (None, "") else DEFAULT_AVAILABILITY_TARGET
    except (TypeError, ValueError):
        target = DEFAULT_AVAILABILITY_TARGET
    host_ids = [h.id for h in base.hosts]
    window_minutes = AVAILABILITY_DAYS * 24 * 60
    budget = round((1 - target / 100.0) * window_minutes, 1)
    out = {"window_days": AVAILABILITY_DAYS, "target_pct": target, "budget_minutes": budget,
           "pct": None, "downtime_minutes": None, "checks": 0, "failed_checks": 0,
           "hosts_with_data": 0, "hosts_total": len(host_ids), "status": "no_data",
           "method": "successful checks / all checks, 30 days; maintenance and no-data excluded"}
    if not host_ids:
        return out
    async def _scan():
        return await ch.query(
            """
            SELECT count() AS total, countIf(success = 1) AS ok, uniqExact(host_id) AS hosts
            FROM ping_checks
            WHERE host_id IN ({hids:Array(UInt32)})
              AND timestamp >= now() - toIntervalDay({d:UInt32})
            """,
            {"hids": host_ids, "d": AVAILABILITY_DAYS},
        )

    rows = await _cached(db, f"availability:{hash(tuple(host_ids))}", AVAILABILITY_TTL, _scan)
    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    ok = int(r.get("ok") or 0)
    if not total:
        return out
    pct = ok / total * 100.0
    downtime = round((1 - ok / total) * window_minutes, 1)
    out.update({
        "pct": round(pct, 3), "downtime_minutes": downtime, "checks": total,
        "failed_checks": total - ok, "hosts_with_data": int(r.get("hosts") or 0),
        "status": "above_target" if pct >= target else "below_target",
        "budget_used_pct": round(downtime / budget * 100, 1) if budget else None,
    })
    return out


async def section_since_last_visit(db, base: Base, since: datetime | None, fallback: bool,
                                   is_admin: bool) -> dict:
    from services import changes as changes_svc

    since = since or base.now - timedelta(hours=24)
    feed = await changes_svc.collect(db, since, base.now, limit=20, is_admin=is_admin)
    return {"since": hs.iso(since), "fallback": fallback, "counts": feed["counts"],
            "total": feed["total"], "items": feed["items"]}


# ── Assembly ────────────────────────────────────────────────────────────────


async def build_dashboard(db, *, user_id: int | None, is_admin: bool,
                          since_override: datetime | None = None) -> dict:
    from models.settings import get_setting
    from services import user_prefs

    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    errors: list[dict] = []

    def mark(name, start):
        timings[name] = round((time.perf_counter() - start) * 1000, 1)

    s = time.perf_counter()
    base = await load_base(db)
    mark("base", s)
    tz_name = await get_setting(db, "timezone", "UTC") or "UTC"
    previous_seen = await user_prefs.dashboard_seen_at(db, user_id)

    out: dict = {"generated_at": hs.iso(base.now), "previous_seen_at": hs.iso(previous_seen),
                 "timezone": tz_name}

    async def run(name, coro_fn):
        start = time.perf_counter()
        try:
            out[name] = await coro_fn()
        except Exception as exc:  # noqa: BLE001 — one failing card must not fail the page
            log.exception("dashboard section %s failed", name)
            # No rollback: it would expire the base rows every later section
            # reads. ClickHouse failures (the common case) leave the Postgres
            # transaction untouched anyway.
            out[name] = None
            errors.append({"section": name, "error": type(exc).__name__})
        mark(name, start)

    integrations: dict = {}
    agents: dict = {}

    async def _integrations():
        integrations.update(await integration_status(db))
        return None

    async def _agents():
        agents.update(await agent_counts(db, base.now))
        return None

    await run("_integrations", _integrations)
    await run("_agents", _agents)
    out.pop("_integrations", None)
    out.pop("_agents", None)
    integrations = integrations or {"total": 0, "ok": 0, "error": 0, "no_data": 0, "failing": []}
    agents = agents or {"total": 0, "reporting": 0}
    probes = probe_rows(base)

    async def _summary():
        return await summary_from(db, base, integrations)

    async def _health():
        return section_health(base, agents, integrations, probes)

    async def _internet():
        return await section_internet(db, base)

    async def _incidents():
        return await section_incidents(db, base, tz_name)

    async def _topology():
        return section_topology(base)

    async def _groups():
        return section_groups(base, probes)

    async def _upcoming():
        return await section_upcoming(db, base)

    async def _latency():
        return await section_latency(base)

    async def _syslog():
        return await section_syslog(db, base)

    async def _availability():
        return await section_availability(db, base)

    async def _since():
        since = since_override or previous_seen
        return await section_since_last_visit(db, base, since, fallback=since is None,
                                              is_admin=is_admin)

    for name, fn in (("summary", _summary), ("health", _health), ("internet", _internet),
                     ("incidents", _incidents), ("topology", _topology), ("groups", _groups),
                     ("upcoming", _upcoming), ("latency", _latency), ("syslog", _syslog),
                     ("availability", _availability), ("since_last_visit", _since)):
        await run(name, fn)

    if out.get("topology") is not None and out.get("internet"):
        wan = out["internet"].get("wan")
        out["topology"]["internet_root"] = {
            "state": (wan or {}).get("status"),
            "latency_ms": (wan or {}).get("latency_ms"),
            "gateway": out["internet"].get("gateway"),
            "source": (wan or {}).get("source"),
        }
    if out.get("health") is not None:
        out["health"]["failing_integrations"] = integrations.get("failing", [])[:10]

    out["errors"] = errors
    out["timings_ms"] = {**timings, "total": round((time.perf_counter() - t0) * 1000, 1)}
    return out


# ── Helpers ──────────────────────────────────────────────────────────────────


def _json(raw) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 2)


def _zone(tz_name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _local_day_start_utc(now_utc: datetime, tz_name: str) -> datetime:
    from datetime import timezone

    zone = _zone(tz_name)
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(zone)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)


def _local_date(utc_dt: datetime, tz_name: str) -> str:
    from datetime import timezone

    # +1 h guards against the bucket start sitting exactly on a DST edge.
    return (utc_dt + timedelta(hours=1)).replace(tzinfo=timezone.utc).astimezone(_zone(tz_name)).date().isoformat()

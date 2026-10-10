"""Watched services on agents — evaluation of what the agent reports.

Data flow
---------
1. An editor sets ``agents.watched_services`` (JSON list of service / systemd
   unit names) via ``PUT /api/v1/agents/{id}/services``.
2. The heartbeat response hands the list to the agent in
   ``config.watched_services``.
3. The agent checks each entry once per heartbeat and sends
   ``service_states: [{"name", "state", "start_type"?}]`` with its next report.
   ``state`` is one of ``running | stopped | not_found | unknown``.
4. :func:`apply_service_report` merges that into ``agents.service_states``
   (latest state, consecutive-failure streak, whether an incident is open) and
   opens / resolves ``agent_service`` incidents.

An agent that predates the feature never sends ``service_states``; the report
handler then leaves everything untouched, so nothing is evaluated and nothing
is raised for it.

Rules
-----
* ``stopped`` and ``not_found`` extend a failure streak, ``running`` ends it.
  ``unknown`` (the agent could not tell) and a watched name missing from the
  report (the agent has not picked up a new list yet) hold the streak as it is —
  neither a failure nor a recovery is claimed without evidence.
* An incident opens once the streak reaches ``agent_service_fail_reports``
  consecutive reports (setting, default 3), at most once per streak, and is
  resolved by the first ``running`` report or by removing the service from the
  watch list.
* While the agent's host (the ``source == "agent"`` ping host with the agent's
  hostname) is in maintenance, no incident is opened. The streak keeps counting,
  so a service still down when maintenance ends alerts on the next report.
* The streak lives in the database, not in memory: a backend restart neither
  re-alerts nor forgets an ongoing outage.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime

from sqlalchemy import func, select

from models.incident import Incident, IncidentEvent

log = logging.getLogger("nodeglow.agent_services")

RULE = "agent_service"
STATES = ("running", "stopped", "not_found", "unknown")
FAILING_STATES = ("stopped", "not_found")
MAX_WATCHED_SERVICES = 50
MAX_NAME_LEN = 256
DEFAULT_FAIL_REPORTS = 3
FAIL_REPORTS_SETTING = "agent_service_fail_reports"

# Windows service names and systemd unit names. Mirrors the agent's own check;
# the agent passes the name as a single argv entry (no shell), the leading-dash
# ban keeps it from being read as an option.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:$ \\-]+$")

# Keeps fire-and-forget notification tasks alive until they finish.
_pending_notifications: set[asyncio.Task] = set()


# ── Watch list ───────────────────────────────────────────────────────────────

def is_valid_service_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_NAME_LEN
        and not name.startswith("-")
        and bool(_NAME_RE.match(name))
    )


def normalize_watch_list(raw) -> list[str]:
    """Validate a watch list from the API. Raises ``ValueError`` with a reason."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("services must be a list of service names")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("service names must be strings")
        name = item.strip()
        if not name:
            continue
        if not is_valid_service_name(name):
            raise ValueError(f"invalid service name: {name!r}")
        if name not in out:
            out.append(name)
    if len(out) > MAX_WATCHED_SERVICES:
        raise ValueError(f"at most {MAX_WATCHED_SERVICES} services can be watched")
    return out


def watched_list(agent) -> list[str]:
    """The agent's stored watch list; tolerant of legacy / malformed values."""
    if not agent.watched_services:
        return []
    try:
        data = json.loads(agent.watched_services)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, str) and s]


def load_states(agent) -> dict | None:
    """The stored evaluation state, or None if the agent never reported any."""
    if not agent.service_states:
        return None
    try:
        data = json.loads(agent.service_states)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def incident_hash(agent_id: int, name: str) -> str:
    """Dedup key of the incident for one service on one agent."""
    return hashlib.sha256(f"agent:{agent_id}:service:{name.lower()}".encode()).hexdigest()[:16]


# ── Pure merge ───────────────────────────────────────────────────────────────

def _index_reported(reported) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(reported, list):
        return out
    for r in reported:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.setdefault(name, r)
    return out


def merge_report(previous: dict | None, watched: list[str], reported, now: datetime) -> list[dict]:
    """Fold one report into the per-service state. Pure — no I/O.

    Returns one entry per watched name, in watch-list order::

        {"name", "state", "start_type", "since", "fail_count", "alerted"}

    ``alerted`` is carried over unchanged; the caller decides on incidents.
    """
    prev_by_name = {
        s["name"]: s for s in (previous or {}).get("services", [])
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    by_name = _index_reported(reported)
    by_lower = {k.lower(): v for k, v in by_name.items()}
    now_iso = now.isoformat()

    merged = []
    for name in watched:
        rep = by_name.get(name) or by_lower.get(name.lower())
        prev = prev_by_name.get(name, {})
        state = rep.get("state") if rep else None
        if state not in STATES:
            state = "unknown"
        prev_count = prev.get("fail_count") if isinstance(prev.get("fail_count"), int) else 0

        if state == "running":
            fail_count = 0
        elif state in FAILING_STATES:
            fail_count = prev_count + 1
        else:
            fail_count = prev_count

        start_type = rep.get("start_type") if rep else None
        if not isinstance(start_type, str):
            start_type = prev.get("start_type") if not rep else None

        merged.append({
            "name": name,
            "state": state,
            "start_type": start_type,
            "since": prev.get("since") if prev.get("state") == state and prev.get("since") else now_iso,
            "fail_count": fail_count,
            "alerted": bool(prev.get("alerted")),
        })
    return merged


# ── Incidents ────────────────────────────────────────────────────────────────

async def _fail_threshold(db) -> int:
    from models.settings import get_setting
    try:
        value = int(await get_setting(db, FAIL_REPORTS_SETTING, str(DEFAULT_FAIL_REPORTS)))
    except (TypeError, ValueError):
        value = DEFAULT_FAIL_REPORTS
    return max(1, min(value, 1000))


async def _host_in_maintenance(db, agent) -> bool:
    """Whether the ping host that represents this agent is in maintenance."""
    from models.ping import PingHost

    if not agent.hostname:
        return False
    hn = agent.hostname.lower()
    rows = (await db.execute(
        select(PingHost.maintenance).where(
            PingHost.source == "agent",
            (func.lower(PingHost.hostname) == hn) | (func.lower(PingHost.name) == hn),
        )
    )).scalars().all()
    return any(bool(m) for m in rows)


async def _agent_ping_host_ids(db, agent) -> list[int]:
    """Ids of the ping hosts that represent this agent (matched like
    :func:`_host_in_maintenance`), recorded as the incident's affected hosts."""
    from models.ping import PingHost

    if not agent.hostname:
        return []
    hn = agent.hostname.lower()
    return [hid for (hid,) in (await db.execute(
        select(PingHost.id).where(
            PingHost.source == "agent",
            (func.lower(PingHost.hostname) == hn) | (func.lower(PingHost.name) == hn),
        )
    )).all()]


async def _resolve(db, agent_id: int, name: str, summary: str) -> Incident | None:
    open_incidents = (await db.execute(
        select(Incident).where(
            Incident.rule == RULE,
            Incident.host_ids_hash == incident_hash(agent_id, name),
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalars().all()
    now = datetime.utcnow()
    for incident in open_incidents:
        incident.status = "resolved"
        incident.resolved_at = now
        incident.updated_at = now
        db.add(IncidentEvent(incident_id=incident.id, event_type="resolved", summary=summary))
    return open_incidents[0] if open_incidents else None


def _describe(state: str) -> str:
    return "not installed" if state == "not_found" else "not running"


async def apply_service_report(db, agent, reported, now: datetime | None = None) -> list[tuple[str, str, str]]:
    """Merge a heartbeat's ``service_states`` into the agent and raise / resolve
    incidents. Changes are added to ``db`` but not committed.

    Returns the notifications to send *after* the caller commits, as
    ``(title, message, severity)`` tuples — see :func:`send_notifications`.
    """
    from services.correlation import _find_or_create_incident

    now = now or datetime.utcnow()
    watched = watched_list(agent)
    previous = load_states(agent)
    merged = merge_report(previous, watched, reported, now)
    notifications: list[tuple[str, str, str]] = []

    threshold = await _fail_threshold(db) if merged else DEFAULT_FAIL_REPORTS
    in_maintenance = None  # looked up lazily, only when an incident would open

    for svc in merged:
        name, state = svc["name"], svc["state"]
        if svc["fail_count"] >= threshold and not svc["alerted"]:
            if in_maintenance is None:
                in_maintenance = await _host_in_maintenance(db, agent)
            if in_maintenance:
                continue
            title = f"{agent.name}: service {name} {_describe(state)}"
            summary = (
                f"Service '{name}' on agent {agent.name} ({agent.hostname or '?'}) is "
                f"{state.replace('_', ' ')} for {svc['fail_count']} consecutive reports"
            )
            await _find_or_create_incident(
                db,
                rule=RULE,
                title=title,
                severity="warning",
                host_ids=[],
                event_type="service_down",
                summary=summary,
                key_hash=incident_hash(agent.id, name),
                send_notification=False,
                affected_host_ids=await _agent_ping_host_ids(db, agent),
            )
            svc["alerted"] = True
            notifications.append((f"🔴 Incident: {title}", summary, "warning"))
        elif state == "running" and svc["alerted"]:
            summary = f"Service '{name}' on agent {agent.name} is running again"
            incident = await _resolve(db, agent.id, name, summary)
            svc["alerted"] = False
            if incident is not None:
                notifications.append((f"✅ Resolved: {incident.title}", summary, "info"))

    # Services dropped from the watch list take their open incident with them.
    watched_names = {s["name"] for s in merged}
    for old in (previous or {}).get("services", []):
        if isinstance(old, dict) and old.get("alerted") and old.get("name") not in watched_names:
            await _resolve_unwatched(db, agent, old["name"], notifications)

    agent.service_states = json.dumps({"reported_at": now.isoformat(), "services": merged})
    return notifications


async def _resolve_unwatched(db, agent, name: str, notifications: list) -> None:
    summary = f"Service '{name}' on agent {agent.name} is no longer watched"
    incident = await _resolve(db, agent.id, name, summary)
    if incident is not None:
        notifications.append((f"✅ Resolved: {incident.title}", summary, "info"))


async def apply_watch_list_change(db, agent, new_list: list[str]) -> list[tuple[str, str, str]]:
    """Store a new watch list, resolving incidents of services no longer on it
    and dropping their state. Not committed."""
    previous = load_states(agent)
    keep = set(new_list)
    notifications: list[tuple[str, str, str]] = []
    if previous is not None:
        remaining = []
        for svc in previous.get("services", []):
            if not isinstance(svc, dict):
                continue
            if svc.get("name") in keep:
                remaining.append(svc)
            elif svc.get("alerted"):
                await _resolve_unwatched(db, agent, svc["name"], notifications)
        previous["services"] = remaining
        agent.service_states = json.dumps(previous)
    agent.watched_services = json.dumps(new_list) if new_list else None
    return notifications


def send_notifications(notifications: list[tuple[str, str, str]]) -> None:
    """Hand notifications to the channel mechanism without blocking the caller
    (the agent heartbeat must not wait on Telegram or SMTP)."""
    if not notifications:
        return

    async def _send():
        from notifications import notify
        for title, message, severity in notifications:
            try:
                await notify(title, message, severity=severity)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to send service notification: %s", exc)

    task = asyncio.create_task(_send())
    _pending_notifications.add(task)
    task.add_done_callback(_pending_notifications.discard)


# ── API view ─────────────────────────────────────────────────────────────────

def service_view(agent) -> dict:
    """What the agent detail API exposes: the watch list joined with the last
    reported state of each entry."""
    watched = watched_list(agent)
    states = load_states(agent)
    by_name = {
        s["name"]: s for s in (states or {}).get("services", [])
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    services = []
    for name in watched:
        s = by_name.get(name)
        services.append({
            "name": name,
            "state": s.get("state", "unknown") if s else None,
            "start_type": s.get("start_type") if s else None,
            "since": s.get("since") if s else None,
            "fail_count": s.get("fail_count", 0) if s else 0,
            "alerted": bool(s.get("alerted")) if s else False,
        })
    return {
        "watched_services": watched,
        "services_reported_at": (states or {}).get("reported_at"),
        "services": services,
    }

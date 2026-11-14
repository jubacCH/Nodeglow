"""Incidents for agents that stopped reporting.

An agent reports every 30 seconds. One that falls silent takes its host's
metrics, logs and service checks with it, and nothing else notices: the host
still answers a ping. On a real installation six agents crash-looped for
seven months after a failed self-update and no alert was ever raised.

One incident per silent agent (rule ``agent_silent``), opened and resolved by
the job that evaluates them (``services.condition_incidents``).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from models.agent import Agent
from services.condition_incidents import Condition, Note

RULE = "agent_silent"

# Twenty missed reports at the default interval. Long enough for a host
# reboot or an agent update, short enough to still be news.
SILENT_AFTER = timedelta(minutes=10)

# From this many notifications of one kind in one run on, they are sent as
# one. A network outage silences every agent of a site at the same moment.
BUNDLE_FROM = 3


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours"
    return f"{hours // 24} days"


def silent_condition(agent: Agent, now: datetime, host_ids: list[int] | None = None) -> Condition | None:
    """The condition for an agent that has been quiet for too long, else None.

    An agent that never reported is not silent: it was added and not yet
    installed, and has no report to miss.
    """
    if not agent.enabled or agent.last_seen is None:
        return None
    quiet = now - agent.last_seen
    if quiet <= SILENT_AFTER:
        return None
    kind = "Probe" if agent.is_probe else "Agent"
    what = ("The hosts it checks are not being checked, and its own metrics and logs are missing."
            if agent.is_probe else
            "Metrics, logs and service checks of this host are missing since then.")
    return Condition(
        f"{kind} silent: {agent.name}", "warning",
        f"{kind} {agent.name} last reported {_ago(quiet)} ago "
        f"({agent.last_seen:%Y-%m-%d %H:%M} UTC). {what} "
        "Check that the agent service is running on the host and can reach this server.",
        host_ids=host_ids or [],
    )


def _labels(value: str | None) -> set[str]:
    """The names a machine goes by: as given, and without its domain.

    An agent calls itself ``web``; the host it runs on is listed as
    ``web.example.com``. An address has no short form.
    """
    value = (value or "").strip().lower()
    if not value:
        return set()
    first = value.split(".", 1)[0]
    return {value} if first.isdigit() or ":" in value else {value, first}


async def _hosts_by_agent(db, agents: list[Agent]) -> dict[int, list]:
    """The ping hosts each agent stands for, matched by name or hostname."""
    from models.ping import PingHost

    wanted: dict[str, list[int]] = {}
    for agent in agents:
        for label in _labels(agent.hostname) | _labels(agent.name):
            wanted.setdefault(label, []).append(agent.id)
    if not wanted:
        return {}
    found: dict[int, list] = {}
    for host in (await db.execute(select(PingHost))).scalars().all():
        for label in _labels(host.name) | _labels(host.hostname):
            for agent_id in wanted.get(label, []):
                if host not in found.setdefault(agent_id, []):
                    found[agent_id].append(host)
    return found


async def silent_conditions(db, now: datetime) -> dict[str, Condition]:
    """Dedup key -> condition for every agent that is silent right now.

    An agent whose host is in maintenance is left out: someone took the
    machine down on purpose.
    """
    from services.maintenance import maintenance_ids

    cutoff = now - SILENT_AFTER
    agents = (await db.execute(
        select(Agent).where(Agent.enabled == True, Agent.last_seen.is_not(None),  # noqa: E712
                            Agent.last_seen < cutoff).order_by(Agent.id)
    )).scalars().all()
    if not agents:
        return {}
    hosts = await _hosts_by_agent(db, agents)
    in_maintenance = await maintenance_ids(db, [h for hs in hosts.values() for h in hs], now)

    wanted: dict[str, Condition] = {}
    for agent in agents:
        own = hosts.get(agent.id, [])
        if own and all(h.id in in_maintenance for h in own):
            continue
        cond = silent_condition(agent, now, [h.id for h in own])
        if cond is not None:
            wanted[f"ag:{agent.id}"] = cond
    return wanted


def started_recently(now_ts: float, process_start: float) -> bool:
    """Has this server been up for less than the silence limit?

    While it was down no agent could report. Judged right after a start,
    every agent would look silent for as long as the outage lasted.
    """
    return now_ts - process_start < SILENT_AFTER.total_seconds()


def bundle(notes: list[Note]) -> list[Note]:
    """Many notifications of one kind from one run become one.

    The incidents stay one per agent. Only the message is combined, so a
    site that drops off the network is one notification, not one per agent.
    """
    out: list[Note] = []
    for prefix, word, severity in (("🔴 Incident: ", "silent", "warning"),
                                   ("✅ Resolved: ", "reporting again", "info")):
        group = [n for n in notes if n.title.startswith(prefix)]
        if len(group) < BUNDLE_FROM:
            out.extend(group)
            continue
        names = sorted(n.title[len(prefix):].split(": ", 1)[-1] for n in group)
        shown = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
        out.append(Note(f"{prefix}{len(group)} agents {word}", f"{shown}.", severity,
                        group[0].incident_id, link_path="/incidents"))
    out.extend(n for n in notes if not n.title.startswith(("🔴 Incident: ", "✅ Resolved: ")))
    return out

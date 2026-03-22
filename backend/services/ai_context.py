"""Gather infrastructure context for AI features (copilot + postmortem).

Token budget: keep infra context under ~800 tokens to minimize API costs.
Only include actionable data — counts and anomalies, not full lists.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ping import PingHost, PingResult
from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig

log = logging.getLogger(__name__)


async def gather_infrastructure_context(db: AsyncSession) -> str:
    """Build a compact text summary of current infrastructure state.

    Optimised for minimal token usage: only counts and anomalies,
    individual items listed only when something is wrong.
    """
    sections = []

    # ── Host status (counts only, list only offline) ──
    try:
        hosts = (await db.execute(
            select(PingHost).where(PingHost.enabled == True)
        )).scalars().all()

        online = sum(1 for h in hosts if h.status == "online")
        offline = [h for h in hosts if h.status == "offline"]
        line = f"Hosts: {len(hosts)} total, {online} online, {len(offline)} offline"
        if offline:
            line += "\nOffline: " + ", ".join(
                h.name or h.hostname for h in offline[:10]
            )
        sections.append(line)
    except Exception as e:
        log.debug("Context: host status failed: %s", e)

    # ── Active incidents (max 10, one line each) ──
    try:
        incidents = (await db.execute(
            select(Incident)
            .where(Incident.status.in_(["open", "acknowledged"]))
            .order_by(Incident.created_at.desc())
            .limit(10)
        )).scalars().all()

        if incidents:
            lines = [
                f"- [{inc.severity}] {inc.title} ({inc.rule}, {inc.status})"
                for inc in incidents
            ]
            sections.append(f"Incidents ({len(incidents)} active):\n" + "\n".join(lines))
        else:
            sections.append("Incidents: none active")
    except Exception as e:
        log.debug("Context: incidents failed: %s", e)

    # ── Syslog stats (last hour, aggregated numbers only) ──
    try:
        from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar
        since = datetime.utcnow() - timedelta(hours=1)

        total = await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)}",
            {"t": since},
        )
        errors = await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)} AND severity <= 3",
            {"t": since},
        )

        syslog_line = f"Syslog (1h): {total or 0} msgs, {errors or 0} errors"

        # Only fetch top patterns if there are errors (saves a query when quiet)
        if errors and int(errors) > 0:
            top_templates = await ch_query(
                "SELECT any(message) as example, count() as cnt "
                "FROM syslog_messages "
                "WHERE timestamp >= {t:DateTime64(3)} AND severity <= 3 "
                "GROUP BY template_hash ORDER BY cnt DESC LIMIT 3",
                {"t": since},
            )
            if top_templates:
                syslog_line += "\nTop errors: " + " | ".join(
                    f"{r['cnt']}x {r['example'][:80]}" for r in top_templates
                )

        sections.append(syslog_line)
    except Exception as e:
        log.debug("Context: syslog failed: %s", e)

    # ── Integration status (count + only unhealthy ones) ──
    try:
        integrations = (await db.execute(
            select(IntegrationConfig).where(IntegrationConfig.enabled == True)
        )).scalars().all()

        if integrations:
            unhealthy = [ic for ic in integrations if ic.status and ic.status != "ok"]
            line = f"Integrations: {len(integrations)} active"
            if unhealthy:
                line += ", problems: " + ", ".join(
                    f"{ic.integration_type}:{ic.name or ic.host}({ic.status})"
                    for ic in unhealthy[:5]
                )
            else:
                line += ", all healthy"
            sections.append(line)
    except Exception as e:
        log.debug("Context: integrations failed: %s", e)

    return "\n\n".join(sections) if sections else "No infrastructure data available."


async def gather_incident_context(db: AsyncSession, incident_id: int) -> str:
    """Build compact context for an incident postmortem.

    Optimised: aggregated syslog patterns instead of raw messages,
    event details truncated, keeps total under ~600 tokens.
    """
    incident = await db.get(Incident, incident_id)
    if not incident:
        return "Incident not found."

    # Events timeline (compact, no detail blobs)
    events_q = await db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.timestamp.asc())
    )
    events = events_q.scalars().all()

    event_lines = "\n".join(
        f"- {e.timestamp.strftime('%H:%M:%S')} {e.event_type}: {e.summary[:100]}"
        for e in events
    )

    # Aggregated syslog patterns (not raw messages — much fewer tokens)
    syslog_section = ""
    try:
        from services.clickhouse_client import query as ch_query
        start = incident.created_at - timedelta(minutes=5)
        end = (incident.resolved_at or datetime.utcnow()) + timedelta(minutes=5)

        patterns = await ch_query(
            "SELECT any(message) as example, count() as cnt, "
            "min(severity) as worst_sev, groupUniqArray(hostname) as hosts "
            "FROM syslog_messages "
            "WHERE timestamp >= {t0:DateTime64(3)} AND timestamp <= {t1:DateTime64(3)} "
            "AND severity <= 4 "
            "GROUP BY template_hash ORDER BY cnt DESC LIMIT 5",
            {"t0": start, "t1": end},
        )
        if patterns:
            lines = []
            for r in patterns:
                hosts = r.get("hosts", [])
                host_str = ",".join(str(h) for h in hosts[:3])
                lines.append(f"- {r['cnt']}x sev{r['worst_sev']} [{host_str}]: {r['example'][:80]}")
            syslog_section = "\nSyslog patterns:\n" + "\n".join(lines)
    except Exception as e:
        log.debug("Incident context: syslog failed: %s", e)

    duration = ""
    if incident.resolved_at and incident.created_at:
        delta = incident.resolved_at - incident.created_at
        minutes = int(delta.total_seconds() / 60)
        duration = f"{minutes // 60}h{minutes % 60}m" if minutes >= 60 else f"{minutes}m"

    return (
        f"Incident #{incident.id}: {incident.title}\n"
        f"Rule: {incident.rule} | Severity: {incident.severity} | "
        f"Duration: {duration or 'ongoing'}\n"
        f"Created: {incident.created_at.strftime('%Y-%m-%d %H:%M')} | "
        f"Resolved: {incident.resolved_at.strftime('%Y-%m-%d %H:%M') if incident.resolved_at else 'no'}\n\n"
        f"Events:\n{event_lines or 'None'}"
        f"{syslog_section}"
    )

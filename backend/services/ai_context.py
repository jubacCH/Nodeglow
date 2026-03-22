"""Gather infrastructure context for AI features (copilot + postmortem)."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ping import PingHost, PingResult
from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig

log = logging.getLogger(__name__)


async def gather_infrastructure_context(db: AsyncSession) -> str:
    """Build a structured text summary of current infrastructure state."""
    sections = []

    # ── Host status ──
    try:
        hosts = (await db.execute(
            select(PingHost).where(PingHost.enabled == True)
        )).scalars().all()

        online = [h for h in hosts if h.status == "online"]
        offline = [h for h in hosts if h.status == "offline"]
        sections.append(
            f"## Hosts ({len(hosts)} total, {len(online)} online, {len(offline)} offline)\n"
            + (("\nOffline hosts:\n" + "\n".join(
                f"- {h.name or h.hostname} (ID {h.id})" for h in offline[:20]
            )) if offline else "\nAll hosts are online.")
        )
    except Exception as e:
        log.debug("Context: host status failed: %s", e)

    # ── Active incidents ──
    try:
        incidents = (await db.execute(
            select(Incident)
            .where(Incident.status.in_(["open", "acknowledged"]))
            .order_by(Incident.created_at.desc())
            .limit(20)
        )).scalars().all()

        if incidents:
            lines = []
            for inc in incidents:
                lines.append(
                    f"- [{inc.severity}] {inc.title} (rule: {inc.rule}, "
                    f"status: {inc.status}, since: {inc.created_at.isoformat()})"
                )
            sections.append(f"## Active Incidents ({len(incidents)})\n" + "\n".join(lines))
        else:
            sections.append("## Active Incidents\nNo active incidents.")
    except Exception as e:
        log.debug("Context: incidents failed: %s", e)

    # ── Syslog stats (last hour) ──
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

        top_templates = await ch_query(
            "SELECT template_hash, any(message) as example, count() as cnt "
            "FROM syslog_messages "
            "WHERE timestamp >= {t:DateTime64(3)} AND severity <= 3 "
            "GROUP BY template_hash ORDER BY cnt DESC LIMIT 5",
            {"t": since},
        )
        template_lines = "\n".join(
            f"- {r['cnt']}x: {r['example'][:120]}" for r in top_templates
        ) if top_templates else "None"

        top_hosts = await ch_query(
            "SELECT source_ip, hostname, count() as cnt "
            "FROM syslog_messages "
            "WHERE timestamp >= {t:DateTime64(3)} AND severity <= 3 "
            "GROUP BY source_ip, hostname ORDER BY cnt DESC LIMIT 5",
            {"t": since},
        )
        host_lines = "\n".join(
            f"- {r.get('hostname') or r['source_ip']}: {r['cnt']} errors" for r in top_hosts
        ) if top_hosts else "None"

        sections.append(
            f"## Syslog (last hour)\n"
            f"Total messages: {total or 0}, Errors (sev 0-3): {errors or 0}\n\n"
            f"Top error patterns:\n{template_lines}\n\n"
            f"Top error sources:\n{host_lines}"
        )
    except Exception as e:
        log.debug("Context: syslog failed: %s", e)

    # ── Integration status ──
    try:
        integrations = (await db.execute(
            select(IntegrationConfig).where(IntegrationConfig.enabled == True)
        )).scalars().all()

        if integrations:
            lines = [
                f"- {ic.integration_type}: {ic.name or ic.host} "
                f"(last check: {ic.last_check.isoformat() if ic.last_check else 'never'}, "
                f"status: {ic.status or 'unknown'})"
                for ic in integrations[:15]
            ]
            sections.append(f"## Integrations ({len(integrations)} active)\n" + "\n".join(lines))
    except Exception as e:
        log.debug("Context: integrations failed: %s", e)

    return "\n\n".join(sections) if sections else "No infrastructure data available."


async def gather_incident_context(db: AsyncSession, incident_id: int) -> str:
    """Build context specific to an incident for postmortem generation."""
    incident = await db.get(Incident, incident_id)
    if not incident:
        return "Incident not found."

    # Events timeline
    events_q = await db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.timestamp.asc())
    )
    events = events_q.scalars().all()

    event_lines = "\n".join(
        f"- [{e.timestamp.isoformat()}] {e.event_type}: {e.summary}"
        + (f"\n  Detail: {e.detail[:200]}" if e.detail else "")
        for e in events
    )

    # Related syslog messages
    syslog_section = ""
    try:
        from services.clickhouse_client import query as ch_query
        start = incident.created_at - timedelta(minutes=5)
        end = (incident.resolved_at or datetime.utcnow()) + timedelta(minutes=5)

        rows = await ch_query(
            "SELECT timestamp, hostname, severity, app_name, message "
            "FROM syslog_messages "
            "WHERE timestamp >= {t0:DateTime64(3)} AND timestamp <= {t1:DateTime64(3)} "
            "AND severity <= 4 "
            "ORDER BY timestamp ASC LIMIT 50",
            {"t0": start, "t1": end},
        )
        if rows:
            log_lines = "\n".join(
                f"- [{r['timestamp']}] [{r['severity']}] {r.get('hostname', '')}: "
                f"{r.get('app_name', '')} - {r['message'][:150]}"
                for r in rows
            )
            syslog_section = f"\n\n## Related Syslog Messages\n{log_lines}"
    except Exception as e:
        log.debug("Incident context: syslog failed: %s", e)

    duration = ""
    if incident.resolved_at and incident.created_at:
        delta = incident.resolved_at - incident.created_at
        minutes = int(delta.total_seconds() / 60)
        if minutes >= 60:
            duration = f"{minutes // 60}h {minutes % 60}m"
        else:
            duration = f"{minutes}m"

    return (
        f"## Incident #{incident.id}\n"
        f"Title: {incident.title}\n"
        f"Rule: {incident.rule}\n"
        f"Severity: {incident.severity}\n"
        f"Status: {incident.status}\n"
        f"Created: {incident.created_at.isoformat()}\n"
        f"Resolved: {incident.resolved_at.isoformat() if incident.resolved_at else 'not yet'}\n"
        f"Duration: {duration or 'ongoing'}\n\n"
        f"## Event Timeline\n{event_lines or 'No events recorded.'}"
        f"{syslog_section}"
    )

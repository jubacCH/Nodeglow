"""
Weekly digest service — aggregate stats from the last 7 days.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost
from models.incident import Incident
from models.integration import IntegrationConfig, Snapshot
from models.log_template import LogTemplate

logger = logging.getLogger(__name__)


async def build_weekly_digest(db: AsyncSession) -> dict:
    """Aggregate weekly stats into a single dict for rendering."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    digest: dict = {
        "period_start": week_ago,
        "period_end": now,
    }

    # ── Incidents ──────────────────────────────────────────────────────────
    incidents_result = await db.execute(
        select(Incident).where(Incident.created_at >= week_ago)
        .order_by(Incident.created_at.desc())
    )
    all_incidents = incidents_result.scalars().all()

    resolved = [i for i in all_incidents if i.status == "resolved"]
    mttr_seconds = []
    for i in resolved:
        if i.resolved_at and i.created_at:
            mttr_seconds.append((i.resolved_at - i.created_at).total_seconds())

    digest["incidents"] = {
        "total": len(all_incidents),
        "by_severity": {},
        "by_status": {},
        "mttr_min": round(sum(mttr_seconds) / len(mttr_seconds) / 60, 1) if mttr_seconds else None,
        "top": all_incidents[:10],
    }
    for i in all_incidents:
        digest["incidents"]["by_severity"][i.severity] = digest["incidents"]["by_severity"].get(i.severity, 0) + 1
        digest["incidents"]["by_status"][i.status] = digest["incidents"]["by_status"].get(i.status, 0) + 1

    # ── Host availability (ClickHouse aggregation, single query) ─────────────
    hosts_result = await db.execute(
        select(PingHost).where(PingHost.enabled == True)
    )
    all_hosts = hosts_result.scalars().all()
    host_by_id = {h.id: h for h in all_hosts}

    from services.clickhouse_client import get_ping_uptime
    uptime_stats = await get_ping_uptime([h.id for h in all_hosts], hours=24 * 7)
    uptime_by_host = {
        hid: (stats["ok"], stats["total"])
        for hid, stats in uptime_stats.items()
    }

    host_uptimes = []
    for host in all_hosts:
        ok, total = uptime_by_host.get(host.id, (0, 0))
        uptime_pct = round(ok / total * 100, 2) if total > 0 else 100.0
        host_uptimes.append({
            "id": host.id,
            "name": host.name,
            "hostname": host.hostname,
            "uptime_pct": uptime_pct,
            "total_pings": total,
            "failures": total - ok,
        })

    host_uptimes.sort(key=lambda x: x["uptime_pct"])
    digest["hosts"] = {
        "total": len(all_hosts),
        "worst": host_uptimes[:10],
        "avg_uptime": round(sum(h["uptime_pct"] for h in host_uptimes) / len(host_uptimes), 2) if host_uptimes else 100.0,
    }

    # ── Syslog stats ──────────────────────────────────────────────────────
    syslog_stats = {"total": 0, "errors": 0, "top_templates": []}
    try:
        from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar
        syslog_stats["total"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)}",
            {"t": week_ago},
        ) or 0)
        syslog_stats["errors"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"t": week_ago},
        ) or 0)

        # Top templates by WEEKLY count (from ClickHouse, not all-time)
        top_tpl_rows = await ch_query(
            "SELECT template_hash, count() AS cnt "
            "FROM syslog_messages "
            "WHERE timestamp >= {t:DateTime64(3)} AND template_hash != '' "
            "GROUP BY template_hash "
            "ORDER BY cnt DESC LIMIT 10",
            {"t": week_ago},
        )
        if top_tpl_rows:
            hashes = [r["template_hash"] for r in top_tpl_rows]
            weekly_counts = {r["template_hash"]: r["cnt"] for r in top_tpl_rows}
            tpl_result = await db.execute(
                select(LogTemplate).where(LogTemplate.template_hash.in_(hashes))
            )
            tpl_by_hash = {t.template_hash: t for t in tpl_result.scalars().all()}
            for r in top_tpl_rows:
                tpl = tpl_by_hash.get(r["template_hash"])
                if tpl:
                    syslog_stats["top_templates"].append({
                        "template": tpl.template[:120],
                        "count": r["cnt"],
                        "noise_score": tpl.noise_score,
                    })
    except Exception:
        logger.debug("Syslog stats unavailable", exc_info=True)

    # Fallback: if ClickHouse template query failed, use PostgreSQL all-time counts
    if not syslog_stats["top_templates"]:
        try:
            top_tpls = (await db.execute(
                select(LogTemplate)
                .where(LogTemplate.last_seen >= week_ago)
                .order_by(LogTemplate.count.desc())
                .limit(10)
            )).scalars().all()
            syslog_stats["top_templates"] = [
                {"template": t.template[:120], "count": t.count, "noise_score": t.noise_score}
                for t in top_tpls
            ]
        except Exception:
            pass

    digest["syslog"] = syslog_stats

    # ── Integrations (single batch query) ──────────────────────────────────
    configs_result = await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)
    )
    configs = configs_result.scalars().all()

    # One query: total + ok per (entity_type, entity_id)
    snap_rows = (await db.execute(
        select(
            Snapshot.entity_type,
            Snapshot.entity_id,
            func.count().label("total"),
            func.count(case((Snapshot.ok == True, 1))).label("ok"),
        )
        .where(Snapshot.timestamp >= week_ago)
        .group_by(Snapshot.entity_type, Snapshot.entity_id)
    )).all()

    snap_stats = {(r.entity_type, r.entity_id): (r.ok, r.total) for r in snap_rows}

    int_stats = []
    for cfg in configs:
        ok_count, total = snap_stats.get((cfg.type, cfg.id), (0, 0))
        success_rate = round(ok_count / total * 100, 1) if total > 0 else None
        int_stats.append({
            "name": cfg.name,
            "type": cfg.type,
            "success_rate": success_rate,
            "total_snapshots": total,
            "failures": total - ok_count,
        })

    int_stats.sort(key=lambda x: x["success_rate"] if x["success_rate"] is not None else 100)
    digest["integrations"] = int_stats

    # ── Storage predictions ────────────────────────────────────────────────
    try:
        from services.predictions import predict_disk_full
        preds = await predict_disk_full(db)
        digest["storage_predictions"] = [
            p for p in preds.values()
            if p.get("days_until_full") is not None and p["confidence"] >= 0.3
        ]
        digest["storage_predictions"].sort(key=lambda x: x.get("days_until_full") or 9999)
    except Exception:
        digest["storage_predictions"] = []

    # ── SSL expiry ─────────────────────────────────────────────────────────
    try:
        ssl_hosts = (await db.execute(
            select(PingHost).where(
                PingHost.ssl_expiry_days.isnot(None),
                PingHost.ssl_expiry_days <= 30,
            ).order_by(PingHost.ssl_expiry_days.asc())
        )).scalars().all()
        digest["ssl_expiring"] = [
            {"name": h.name, "hostname": h.hostname, "days": h.ssl_expiry_days}
            for h in ssl_hosts
        ]
    except Exception:
        digest["ssl_expiring"] = []

    return digest


def format_digest_html(digest: dict) -> str:
    """Format digest data as a styled HTML email body."""
    period = f"{digest['period_start'].strftime('%Y-%m-%d')} — {digest['period_end'].strftime('%Y-%m-%d')}"

    # ── Incidents section ──
    inc = digest.get("incidents", {})
    inc_total = inc.get("total", 0)
    mttr = inc.get("mttr_min")
    by_sev = inc.get("by_severity", {})
    sev_parts = ", ".join(f"{k}: {v}" for k, v in sorted(by_sev.items())) or "none"

    inc_rows = ""
    for i in inc.get("top", [])[:5]:
        title = getattr(i, "title", str(i)) if not isinstance(i, dict) else i.get("title", "")
        severity = getattr(i, "severity", "") if not isinstance(i, dict) else i.get("severity", "")
        status = getattr(i, "status", "") if not isinstance(i, dict) else i.get("status", "")
        sev_color = {"critical": "#FB7185", "warning": "#FBBF24"}.get(severity, "#38BDF8")
        inc_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#CBD5E1;font-size:13px">{_esc(title)}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:{sev_color};font-size:13px">{severity}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#94A3B8;font-size:13px">{status}</td>
        </tr>"""

    # ── Host availability section ──
    hosts = digest.get("hosts", {})
    avg_uptime = hosts.get("avg_uptime", 100)
    worst_hosts = hosts.get("worst", [])[:5]
    host_rows = ""
    for h in worst_hosts:
        uptime = h.get("uptime_pct", 100)
        color = "#FB7185" if uptime < 95 else "#FBBF24" if uptime < 99 else "#34D399"
        host_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#CBD5E1;font-size:13px">{_esc(h.get('name', ''))}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:{color};font-size:13px">{uptime}%</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#94A3B8;font-size:13px">{h.get('failures', 0)} failures</td>
        </tr>"""

    # ── Syslog section ──
    syslog = digest.get("syslog", {})
    syslog_total = syslog.get("total", 0)
    syslog_errors = syslog.get("errors", 0)

    # ── Integrations section ──
    integrations = digest.get("integrations", [])
    int_rows = ""
    for i in integrations[:5]:
        rate = i.get("success_rate")
        rate_str = f"{rate}%" if rate is not None else "—"
        color = "#FB7185" if rate is not None and rate < 90 else "#34D399"
        int_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#CBD5E1;font-size:13px">{_esc(i.get('name', ''))}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#94A3B8;font-size:13px">{i.get('type', '')}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:{color};font-size:13px">{rate_str}</td>
        </tr>"""

    # ── SSL section ──
    ssl_expiring = digest.get("ssl_expiring", [])
    ssl_rows = ""
    for s in ssl_expiring[:5]:
        days = s.get("days", 0)
        color = "#FB7185" if days <= 7 else "#FBBF24" if days <= 14 else "#38BDF8"
        ssl_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:#CBD5E1;font-size:13px">{_esc(s.get('name', ''))}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #1E293B;color:{color};font-size:13px">{days} days</td>
        </tr>"""

    table_style = 'style="width:100%;border-collapse:collapse;margin:8px 0 16px 0"'
    th_style = 'style="padding:6px 12px;text-align:left;border-bottom:2px solid #334155;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px"'

    def _section(title: str, content: str) -> str:
        return f"""
        <div style="margin-bottom:24px">
            <h2 style="color:#E2E8F0;font-size:15px;font-weight:600;margin:0 0 8px 0;padding-bottom:8px;border-bottom:1px solid #1E293B">{title}</h2>
            {content}
        </div>"""

    # Build sections
    sections = []

    # Summary stats
    summary = f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap">
        <div style="background:#1E293B;border-radius:8px;padding:12px 16px;flex:1;min-width:120px">
            <div style="color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px">Incidents</div>
            <div style="color:#E2E8F0;font-size:24px;font-weight:700;margin-top:4px">{inc_total}</div>
            <div style="color:#94A3B8;font-size:12px">{sev_parts}</div>
        </div>
        <div style="background:#1E293B;border-radius:8px;padding:12px 16px;flex:1;min-width:120px">
            <div style="color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px">Avg Uptime</div>
            <div style="color:{'#34D399' if avg_uptime >= 99 else '#FBBF24' if avg_uptime >= 95 else '#FB7185'};font-size:24px;font-weight:700;margin-top:4px">{avg_uptime}%</div>
        </div>
        <div style="background:#1E293B;border-radius:8px;padding:12px 16px;flex:1;min-width:120px">
            <div style="color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px">MTTR</div>
            <div style="color:#E2E8F0;font-size:24px;font-weight:700;margin-top:4px">{f'{mttr} min' if mttr else '—'}</div>
        </div>
        <div style="background:#1E293B;border-radius:8px;padding:12px 16px;flex:1;min-width:120px">
            <div style="color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px">Syslog</div>
            <div style="color:#E2E8F0;font-size:24px;font-weight:700;margin-top:4px">{_fmt_count(syslog_total)}</div>
            <div style="color:#FB7185;font-size:12px">{_fmt_count(syslog_errors)} errors</div>
        </div>
    </div>"""
    sections.append(_section("Weekly Summary", summary))

    # Incidents table
    if inc_rows:
        sections.append(_section("Top Incidents", f"""
            <table {table_style}><thead><tr>
                <th {th_style}>Title</th><th {th_style}>Severity</th><th {th_style}>Status</th>
            </tr></thead><tbody>{inc_rows}</tbody></table>"""))

    # Host availability
    if host_rows:
        sections.append(_section("Lowest Availability", f"""
            <table {table_style}><thead><tr>
                <th {th_style}>Host</th><th {th_style}>Uptime</th><th {th_style}>Issues</th>
            </tr></thead><tbody>{host_rows}</tbody></table>"""))

    # Integrations
    if int_rows:
        sections.append(_section("Integration Health", f"""
            <table {table_style}><thead><tr>
                <th {th_style}>Name</th><th {th_style}>Type</th><th {th_style}>Success Rate</th>
            </tr></thead><tbody>{int_rows}</tbody></table>"""))

    # SSL
    if ssl_rows:
        sections.append(_section("SSL Certificates Expiring Soon", f"""
            <table {table_style}><thead><tr>
                <th {th_style}>Host</th><th {th_style}>Expires In</th>
            </tr></thead><tbody>{ssl_rows}</tbody></table>"""))

    body = "".join(sections)

    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:640px;margin:0 auto;padding:0">
  <div style="background:#0B1120;border-radius:12px;overflow:hidden;border:1px solid #1E293B">
    <div style="padding:24px 28px;border-bottom:1px solid #1E293B;background:linear-gradient(135deg,#0B1120,#1E293B)">
      <div style="color:#38BDF8;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:3px;margin-bottom:8px">Weekly Digest</div>
      <h1 style="color:#E2E8F0;font-size:20px;font-weight:600;margin:0">{period}</h1>
    </div>
    <div style="padding:24px 28px">
      {body}
    </div>
    <div style="padding:16px 28px;border-top:1px solid #1E293B;text-align:center">
      <span style="color:#475569;font-size:11px;letter-spacing:3px;font-weight:500">NODEGLOW</span>
    </div>
  </div>
</div>"""


def format_digest_text(digest: dict) -> str:
    """Format digest data as plain text email body."""
    period = f"{digest['period_start'].strftime('%Y-%m-%d')} — {digest['period_end'].strftime('%Y-%m-%d')}"
    inc = digest.get("incidents", {})
    hosts = digest.get("hosts", {})
    syslog = digest.get("syslog", {})

    lines = [
        f"NODEGLOW Weekly Digest — {period}",
        "=" * 50,
        "",
        f"Incidents: {inc.get('total', 0)}",
        f"MTTR: {inc.get('mttr_min', '—')} min",
        f"Avg Uptime: {hosts.get('avg_uptime', 100)}%",
        f"Syslog: {syslog.get('total', 0)} messages, {syslog.get('errors', 0)} errors",
        "",
    ]

    worst = hosts.get("worst", [])[:5]
    if worst:
        lines.append("Lowest Availability:")
        for h in worst:
            lines.append(f"  {h.get('name', '')}: {h.get('uptime_pct', 100)}% ({h.get('failures', 0)} failures)")
        lines.append("")

    ssl = digest.get("ssl_expiring", [])
    if ssl:
        lines.append("SSL Expiring Soon:")
        for s in ssl:
            lines.append(f"  {s.get('name', '')}: {s.get('days', 0)} days")

    return "\n".join(lines)


async def build_daily_summary_data(db: AsyncSession) -> dict:
    """Aggregate last-24h stats for the AI daily summary."""
    now = datetime.utcnow()
    yesterday = now - timedelta(hours=24)

    data: dict = {"period_start": yesterday, "period_end": now}

    # ── Incidents (24h) ───────────────────────────────────────────────────
    incidents_result = await db.execute(
        select(Incident).where(Incident.created_at >= yesterday)
        .order_by(Incident.created_at.desc())
    )
    all_incidents = incidents_result.scalars().all()

    resolved = [i for i in all_incidents if i.status == "resolved"]
    mttr_seconds = []
    for i in resolved:
        if i.resolved_at and i.created_at:
            mttr_seconds.append((i.resolved_at - i.created_at).total_seconds())

    data["incidents"] = {
        "total": len(all_incidents),
        "open": len([i for i in all_incidents if i.status == "open"]),
        "resolved": len(resolved),
        "mttr_min": round(sum(mttr_seconds) / len(mttr_seconds) / 60, 1) if mttr_seconds else None,
        "items": [
            {
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
                "rule": i.rule,
                "created": i.created_at.strftime("%H:%M") if i.created_at else "?",
                "resolved": i.resolved_at.strftime("%H:%M") if i.resolved_at else None,
            }
            for i in all_incidents[:20]
        ],
    }

    # ── Host availability (24h) ───────────────────────────────────────────
    hosts_result = await db.execute(
        select(PingHost).where(PingHost.enabled == True)
    )
    all_hosts = hosts_result.scalars().all()

    from services.clickhouse_client import get_ping_uptime
    uptime_stats = await get_ping_uptime([h.id for h in all_hosts], hours=24)
    uptime_by_host = {
        hid: (stats["ok"], stats["total"])
        for hid, stats in uptime_stats.items()
    }

    down_hosts = []
    for host in all_hosts:
        ok, total = uptime_by_host.get(host.id, (0, 0))
        if total == 0:
            continue
        uptime_pct = round(ok / total * 100, 2)
        if uptime_pct < 100:
            down_hosts.append({
                "name": host.name or host.hostname,
                "uptime_pct": uptime_pct,
                "failures": total - ok,
            })

    down_hosts.sort(key=lambda x: x["uptime_pct"])
    data["hosts"] = {
        "total": len(all_hosts),
        "down": down_hosts[:10],
        "avg_uptime": round(
            sum(
                (uptime_by_host.get(h.id, (0, 1))[0] / max(uptime_by_host.get(h.id, (0, 1))[1], 1) * 100)
                for h in all_hosts
            ) / max(len(all_hosts), 1), 2
        ),
    }

    # ── Syslog (24h) ─────────────────────────────────────────────────────
    syslog_stats = {"total": 0, "errors": 0, "top_templates": []}
    try:
        from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar
        syslog_stats["total"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)}",
            {"t": yesterday},
        ) or 0)
        syslog_stats["errors"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"t": yesterday},
        ) or 0)

        top_tpl_rows = await ch_query(
            "SELECT template_hash, count() AS cnt "
            "FROM syslog_messages "
            "WHERE timestamp >= {t:DateTime64(3)} AND template_hash != '' "
            "GROUP BY template_hash ORDER BY cnt DESC LIMIT 5",
            {"t": yesterday},
        )
        if top_tpl_rows:
            hashes = [r["template_hash"] for r in top_tpl_rows]
            tpl_result = await db.execute(
                select(LogTemplate).where(LogTemplate.template_hash.in_(hashes))
            )
            tpl_by_hash = {t.template_hash: t for t in tpl_result.scalars().all()}
            for r in top_tpl_rows:
                tpl = tpl_by_hash.get(r["template_hash"])
                if tpl:
                    syslog_stats["top_templates"].append({
                        "template": tpl.template[:120],
                        "count": r["cnt"],
                    })
    except Exception:
        logger.debug("Syslog stats unavailable for daily summary", exc_info=True)

    data["syslog"] = syslog_stats

    # ── Integration failures (24h) ────────────────────────────────────────
    configs_result = await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)
    )
    configs = configs_result.scalars().all()

    snap_rows = (await db.execute(
        select(
            Snapshot.entity_type,
            Snapshot.entity_id,
            func.count().label("total"),
            func.count(case((Snapshot.ok == True, 1))).label("ok"),
        )
        .where(Snapshot.timestamp >= yesterday)
        .group_by(Snapshot.entity_type, Snapshot.entity_id)
    )).all()

    snap_stats = {(r.entity_type, r.entity_id): (r.ok, r.total) for r in snap_rows}

    unhealthy_integrations = []
    for cfg in configs:
        ok_count, total = snap_stats.get((cfg.type, cfg.id), (0, 0))
        if total == 0:
            continue
        success_rate = round(ok_count / total * 100, 1)
        if success_rate < 100:
            unhealthy_integrations.append({
                "name": cfg.name,
                "type": cfg.type,
                "success_rate": success_rate,
                "failures": total - ok_count,
            })

    unhealthy_integrations.sort(key=lambda x: x["success_rate"])
    data["integrations"] = unhealthy_integrations

    # ── SSL expiring soon ─────────────────────────────────────────────────
    try:
        ssl_hosts = (await db.execute(
            select(PingHost).where(
                PingHost.ssl_expiry_days.isnot(None),
                PingHost.ssl_expiry_days <= 14,
            ).order_by(PingHost.ssl_expiry_days.asc())
        )).scalars().all()
        data["ssl_expiring"] = [
            {"name": h.name or h.hostname, "days": h.ssl_expiry_days}
            for h in ssl_hosts
        ]
    except Exception:
        data["ssl_expiring"] = []

    return data


def format_daily_summary_prompt(data: dict) -> str:
    """Format collected data into a compact prompt for AI analysis."""
    lines = [
        f"Period: last 24 hours ({data['period_start'].strftime('%Y-%m-%d %H:%M')} — {data['period_end'].strftime('%Y-%m-%d %H:%M')} UTC)",
        "",
    ]

    inc = data.get("incidents", {})
    lines.append(f"## Incidents: {inc['total']} total, {inc['open']} open, {inc['resolved']} resolved")
    if inc.get("mttr_min"):
        lines.append(f"  MTTR: {inc['mttr_min']} min")
    for item in inc.get("items", []):
        resolved_str = f" → resolved {item['resolved']}" if item["resolved"] else ""
        lines.append(f"  - [{item['severity']}] {item['title']} (rule: {item['rule']}, {item['created']}{resolved_str}) [{item['status']}]")

    hosts = data.get("hosts", {})
    lines.append(f"\n## Hosts: {hosts['total']} monitored, avg uptime {hosts['avg_uptime']}%")
    for h in hosts.get("down", []):
        lines.append(f"  - {h['name']}: {h['uptime_pct']}% ({h['failures']} failures)")

    syslog = data.get("syslog", {})
    lines.append(f"\n## Syslog: {syslog['total']} messages, {syslog['errors']} errors")
    for t in syslog.get("top_templates", []):
        lines.append(f"  - [{t['count']}x] {t['template']}")

    integrations = data.get("integrations", [])
    if integrations:
        lines.append(f"\n## Unhealthy Integrations:")
        for i in integrations:
            lines.append(f"  - {i['name']} ({i['type']}): {i['success_rate']}% success ({i['failures']} failures)")

    ssl = data.get("ssl_expiring", [])
    if ssl:
        lines.append(f"\n## SSL Certificates Expiring Soon:")
        for s in ssl:
            lines.append(f"  - {s['name']}: {s['days']} days")

    return "\n".join(lines)


_DAILY_SUMMARY_SYSTEM_PROMPT = """\
You are Nodeglow's AI operations assistant. You analyze the last 24 hours of homelab/infrastructure monitoring data and produce a concise daily briefing.

Your report should:
1. Start with a one-line overall health assessment (good/warning/critical)
2. Highlight the most important incidents and their likely root causes
3. For each significant issue, suggest concrete resolution steps
4. Flag recurring patterns or trends that need attention
5. Note any upcoming risks (SSL expiry, degrading integrations)

Keep it concise and actionable. Use short bullet points. No fluff.
If everything is healthy, say so briefly — don't invent problems.
Write in plain text (no markdown), suitable for Telegram/Discord messages.
Max ~800 words."""


def _esc(s: str) -> str:
    """Escape HTML entities."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _fmt_count(n: int) -> str:
    """Format large numbers with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

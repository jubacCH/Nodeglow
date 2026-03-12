"""
Weekly digest service — aggregate stats from the last 7 days.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, PingResult
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

    # ── Host availability ──────────────────────────────────────────────────
    hosts_result = await db.execute(
        select(PingHost).where(PingHost.enabled == True)
    )
    all_hosts = hosts_result.scalars().all()

    host_uptimes = []
    for host in all_hosts:
        total_result = await db.execute(
            select(func.count()).select_from(PingResult).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= week_ago,
            )
        )
        total = total_result.scalar() or 0

        success_result = await db.execute(
            select(func.count()).select_from(PingResult).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= week_ago,
                PingResult.success == True,
            )
        )
        success = success_result.scalar() or 0

        uptime_pct = round(success / total * 100, 2) if total > 0 else 100.0
        host_uptimes.append({
            "name": host.name,
            "hostname": host.hostname,
            "uptime_pct": uptime_pct,
            "total_pings": total,
            "failures": total - success,
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
    except Exception:
        logger.debug("Syslog stats unavailable", exc_info=True)

    # Top error templates by count
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

    # ── Integrations ──────────────────────────────────────────────────────
    configs_result = await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)
    )
    configs = configs_result.scalars().all()

    int_stats = []
    for cfg in configs:
        total_result = await db.execute(
            select(func.count()).select_from(Snapshot).where(
                Snapshot.entity_type == cfg.type,
                Snapshot.entity_id == cfg.id,
                Snapshot.timestamp >= week_ago,
            )
        )
        total = total_result.scalar() or 0

        ok_result = await db.execute(
            select(func.count()).select_from(Snapshot).where(
                Snapshot.entity_type == cfg.type,
                Snapshot.entity_id == cfg.id,
                Snapshot.timestamp >= week_ago,
                Snapshot.ok == True,
            )
        )
        ok_count = ok_result.scalar() or 0

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

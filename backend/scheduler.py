"""
Background scheduler – generic integration collection + ping checks + cleanup.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select

from database import AsyncSessionLocal, PingHost, PingResult
from models.integration import IntegrationConfig
from services import integration as int_svc
from services import snapshot as snap_svc

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


# ── Generic integration collection ───────────────────────────────────────────


async def run_integration_checks():
    """
    Generic collector loop: for each registered integration, fetch all configs
    and run collect(). Stores results as Snapshots.
    """
    from integrations import get_registry

    registry = get_registry()
    if not registry:
        return

    async with AsyncSessionLocal() as db:
        # Get all enabled configs in one query
        result = await db.execute(
            select(IntegrationConfig).where(IntegrationConfig.enabled == True)
        )
        all_configs = result.scalars().all()

    if not all_configs:
        return

    # Group by type
    by_type: dict[str, list] = {}
    for cfg in all_configs:
        by_type.setdefault(cfg.type, []).append(cfg)

    for integration_type, configs in by_type.items():
        integration_cls = registry.get(integration_type)
        if not integration_cls:
            continue

        async with AsyncSessionLocal() as db:
            for cfg in configs:
                try:
                    try:
                        config_dict = int_svc.decrypt_config(cfg.config_json)
                    except Exception as dec_exc:
                        logger.error(
                            "Failed to decrypt config [%s/%s]: %s",
                            integration_type, cfg.name, dec_exc,
                        )
                        await snap_svc.save(
                            db, integration_type, cfg.id,
                            ok=False, error=f"Decryption failed: {dec_exc}",
                        )
                        continue
                    instance = integration_cls(config=config_dict)
                    result = await instance.collect()

                    if result.success:
                        await snap_svc.save(
                            db, integration_type, cfg.id,
                            ok=True, data=result.data,
                        )
                        # Run post-snapshot hook (e.g., auto-import hosts)
                        try:
                            await instance.on_snapshot(result.data, config_dict, db)
                        except Exception as hook_exc:
                            logger.warning(
                                "on_snapshot hook failed [%s/%s]: %s",
                                integration_type, cfg.name, hook_exc,
                            )
                    else:
                        await snap_svc.save(
                            db, integration_type, cfg.id,
                            ok=False, error=result.error,
                        )
                except Exception as exc:
                    logger.error(
                        "Integration collect [%s/%s]: %s",
                        integration_type, cfg.name, exc,
                    )
                    await snap_svc.save(
                        db, integration_type, cfg.id,
                        ok=False, error=str(exc),
                    )
            await db.commit()

    logger.debug("Integration check done for %d type(s), %d config(s)",
                 len(by_type), len(all_configs))


# ── Ping checks ──────────────────────────────────────────────────────────────


async def run_ping_checks():
    """Ping all enabled hosts concurrently and store results."""
    import asyncio as _asyncio
    from utils.ping import check_host

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PingHost).where(PingHost.enabled == True))
        hosts = result.scalars().all()

        # Auto-clear expired maintenance windows
        now = datetime.utcnow()
        for h in hosts:
            if h.maintenance and h.maintenance_until and h.maintenance_until <= now:
                h.maintenance = False
                h.maintenance_until = None
        await db.commit()

    if not hosts:
        return

    active_hosts = [h for h in hosts if not h.maintenance]
    if not active_hosts:
        return

    # Separate agent-sourced hosts — they use agent heartbeat, not ICMP
    agent_hosts = [h for h in active_hosts if h.source == "agent"]
    ping_hosts = [h for h in active_hosts if h.source != "agent"]

    # Handle agent-sourced hosts via agent last_seen
    if agent_hosts:
        from models.agent import Agent
        from sqlalchemy import func as sa_func
        async with AsyncSessionLocal() as db:
            for host in agent_hosts:
                # Find matching agent by name (PingHost.name == Agent.hostname)
                agent_r = await db.execute(
                    select(Agent).where(sa_func.lower(Agent.hostname) == host.name.lower())
                )
                agent = agent_r.scalar_one_or_none()
                success = False
                if agent and agent.last_seen:
                    success = (datetime.utcnow() - agent.last_seen).total_seconds() < 120
                db.add(PingResult(
                    host_id=host.id,
                    timestamp=datetime.utcnow(),
                    success=success,
                    latency_ms=0 if success else None,
                ))
                from services.websocket import broadcast_ping_update
                _asyncio.create_task(broadcast_ping_update(host.id, host.name, success, 0 if success else None))
            await db.commit()

    active_hosts = ping_hosts

    # Load previous results for state-change detection
    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sa_func
        sub = (
            select(PingResult.host_id, sa_func.max(PingResult.timestamp).label("max_ts"))
            .group_by(PingResult.host_id)
            .subquery()
        )
        prev_rows = await db.execute(
            select(PingResult.host_id, PingResult.success)
            .join(sub, (PingResult.host_id == sub.c.host_id) & (PingResult.timestamp == sub.c.max_ts))
        )
        prev_success: dict[int, bool] = {row.host_id: row.success for row in prev_rows}

    # Run all checks concurrently with semaphore to limit parallelism
    sem = _asyncio.Semaphore(50)

    async def _check_one(host):
        async with sem:
            online, port_error, latency, detail = await check_host(host)
            return host, online, port_error, latency, detail

    results = await _asyncio.gather(*[_check_one(h) for h in active_hosts])

    # Batch-write all results in one transaction
    import json as _json
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        for host, online, port_error, latency, detail in results:
            db.add(PingResult(
                host_id=host.id,
                timestamp=now,
                success=online,
                latency_ms=latency,
            ))

            # Update port_error flag and check detail on the host
            host_obj = await db.get(PingHost, host.id)
            if host_obj:
                host_obj.port_error = port_error
                host_obj.check_detail = _json.dumps(detail) if detail else None

            # Broadcast live update via WebSocket
            from services.websocket import broadcast_ping_update
            _asyncio.create_task(broadcast_ping_update(host.id, host.name, online, latency))

            # Notify on state change
            prev = prev_success.get(host.id)
            if prev is True and not online:
                from notifications import notify
                _asyncio.create_task(notify(
                    f"Host offline: {host.name}",
                    f"Host {host.hostname} is no longer reachable.",
                    "critical"
                ))
            elif prev is False and online:
                from notifications import notify
                _asyncio.create_task(notify(
                    f"Host back online: {host.name}",
                    f"Host {host.hostname} is reachable again.",
                    "info"
                ))

        await db.commit()

    logger.debug("Ping check done for %d hosts (concurrent)", len(active_hosts))


# ── SSL expiry check ─────────────────────────────────────────────────────────


async def update_ssl_expiry():
    """Update ssl_expiry_days for all HTTPS hosts."""
    from utils.ping import get_ssl_expiry_days
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PingHost).where(PingHost.enabled == True, PingHost.check_type == "https")
        )
        hosts = result.scalars().all()

    if not hosts:
        return

    async with AsyncSessionLocal() as db:
        for host in hosts:
            hostname = host.hostname
            for prefix in ("https://", "http://"):
                if hostname.startswith(prefix):
                    hostname = hostname[len(prefix):]
                    break
            hostname = hostname.split("/")[0].split(":")[0]
            days = await get_ssl_expiry_days(hostname, port=host.port or 443)
            if days is not None:
                await db.execute(
                    sa_update(PingHost).where(PingHost.id == host.id).values(ssl_expiry_days=days)
                )
        await db.commit()


# ── Cleanup ───────────────────────────────────────────────────────────────────


async def cleanup_old_results():
    """Delete old ping results, snapshots, and syslog messages."""
    from database import get_setting

    async with AsyncSessionLocal() as db:
        ping_ret = int(await get_setting(db, "ping_retention_days", "30"))
        int_ret = int(await get_setting(db, "integration_retention_days", "7"))

    async with AsyncSessionLocal() as db:
        # Ping results
        ping_cutoff = datetime.utcnow() - timedelta(days=ping_ret)
        await db.execute(delete(PingResult).where(PingResult.timestamp < ping_cutoff))
        # Integration snapshots
        await snap_svc.cleanup_all(db, int_ret)
        # Syslog retention handled by ClickHouse TTL — no cleanup needed here
        total_deleted = 0
        # Agent snapshots: keep 7 days
        from models.agent import AgentSnapshot
        agent_cutoff = datetime.utcnow() - timedelta(days=7)
        await db.execute(delete(AgentSnapshot).where(AgentSnapshot.timestamp < agent_cutoff))
        # SNMP results: keep 7 days
        from models.snmp import SnmpResult
        snmp_cutoff = datetime.utcnow() - timedelta(days=7)
        await db.execute(delete(SnmpResult).where(SnmpResult.timestamp < snmp_cutoff))
        await db.commit()

    logger.info("Cleanup done (ping: %dd, integrations: %dd, syslog: %d msgs)", ping_ret, int_ret, total_deleted)


# ── Disk space health check ──────────────────────────────────────────────────

DISK_WARNING_PCT = 80
DISK_CRITICAL_PCT = 90
DISK_HISTORY_MAX_SAMPLES = 336  # ~7 days at 30min intervals


async def _record_disk_sample(used_gb: float, total_gb: float):
    """Store a disk usage sample in the settings table for trend analysis."""
    import json
    from database import get_setting, set_setting

    async with AsyncSessionLocal() as db:
        raw = await get_setting(db, "disk_usage_history", "[]")
        try:
            history = json.loads(raw)
        except Exception:
            history = []

        history.append({
            "ts": datetime.utcnow().isoformat(),
            "used_gb": used_gb,
            "total_gb": total_gb,
        })

        # Keep only last N samples
        if len(history) > DISK_HISTORY_MAX_SAMPLES:
            history = history[-DISK_HISTORY_MAX_SAMPLES:]

        await set_setting(db, "disk_usage_history", json.dumps(history))
        await db.commit()


def compute_disk_forecast(history: list[dict], total_gb: float) -> dict | None:
    """Linear regression on disk usage history to predict days until full."""
    if len(history) < 6:  # Need at least 3 hours of data
        return None

    from datetime import datetime as dt

    # Parse timestamps and usage values
    points = []
    for sample in history:
        try:
            ts = dt.fromisoformat(sample["ts"])
            points.append((ts.timestamp(), sample["used_gb"]))
        except Exception:
            continue

    if len(points) < 6:
        return None

    # Simple linear regression
    n = len(points)
    t0 = points[0][0]
    xs = [(p[0] - t0) / 86400 for p in points]  # days since first sample
    ys = [p[1] for p in points]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-10:
        return {"growth_gb_per_day": 0, "days_until_full": None, "trend": "stable"}

    slope = (n * sum_xy - sum_x * sum_y) / denom  # GB per day

    if slope <= 0.001:
        return {"growth_gb_per_day": round(slope, 3), "days_until_full": None, "trend": "stable"}

    current_used = ys[-1]
    remaining = total_gb - current_used
    days_until_full = round(remaining / slope, 1)

    trend = "critical" if days_until_full <= 7 else "warning" if days_until_full <= 30 else "normal"

    return {
        "growth_gb_per_day": round(slope, 3),
        "days_until_full": days_until_full,
        "trend": trend,
    }


async def check_disk_space():
    """Monitor disk usage and create incidents when thresholds are exceeded."""
    import shutil
    from hashlib import sha256

    from models.incident import Incident, IncidentEvent

    try:
        usage = shutil.disk_usage("/")
    except Exception:
        logger.debug("disk_usage check failed", exc_info=True)
        return

    used_pct = round(usage.used / usage.total * 100, 1)
    free_gb = round(usage.free / (1024 ** 3), 1)
    used_gb = round(usage.used / (1024 ** 3), 2)
    total_gb = round(usage.total / (1024 ** 3), 2)

    # Record sample for trend analysis
    await _record_disk_sample(used_gb, total_gb)

    if used_pct < DISK_WARNING_PCT:
        # All clear — auto-resolve any existing disk incident
        async with AsyncSessionLocal() as db:
            existing = (await db.execute(
                select(Incident).where(
                    Incident.rule == "disk_space",
                    Incident.status.in_(["open", "acknowledged"]),
                )
            )).scalars().first()
            if existing:
                existing.status = "resolved"
                existing.resolved_at = datetime.utcnow()
                db.add(IncidentEvent(
                    incident_id=existing.id,
                    event_type="resolved",
                    message=f"Disk usage back to {used_pct}% ({free_gb} GB free)",
                ))
                await db.commit()
                logger.info("Disk space incident resolved: %.1f%% used", used_pct)
        return

    severity = "critical" if used_pct >= DISK_CRITICAL_PCT else "warning"
    title = f"Disk usage at {used_pct}% ({free_gb} GB free)"

    async with AsyncSessionLocal() as db:
        # Dedup: only one open disk incident at a time
        existing = (await db.execute(
            select(Incident).where(
                Incident.rule == "disk_space",
                Incident.status.in_(["open", "acknowledged"]),
            )
        )).scalars().first()

        if existing:
            # Update severity if it escalated
            if severity == "critical" and existing.severity != "critical":
                existing.severity = "critical"
            existing.title = title
            existing.updated_at = datetime.utcnow()
            db.add(IncidentEvent(
                incident_id=existing.id,
                event_type="disk_warning",
                message=title,
            ))
        else:
            inc = Incident(
                rule="disk_space",
                title=title,
                severity=severity,
                status="open",
                host_ids_hash=sha256(b"self-disk").hexdigest()[:16],
            )
            db.add(inc)
            await db.flush()
            db.add(IncidentEvent(
                incident_id=inc.id,
                event_type="created",
                message=title,
            ))

        await db.commit()

    logger.warning("Disk space %s: %.1f%% used (%.1f GB free)", severity, used_pct, free_gb)


# ── ClickHouse maintenance ───────────────────────────────────────────────────


async def cleanup_clickhouse_logs():
    """Truncate ClickHouse system log tables if they exceed size thresholds."""
    try:
        from services.clickhouse_client import query as ch_query

        SAFE_TABLES = {"query_log", "processors_profile_log", "trace_log",
                       "metric_log", "asynchronous_metric_log", "part_log"}

        rows = await ch_query(
            "SELECT table, sum(bytes_on_disk) AS bytes "
            "FROM system.parts WHERE active AND database = 'system' "
            "GROUP BY table ORDER BY bytes DESC"
        )
        for row in rows:
            size_mb = row["bytes"] / (1024 * 1024)
            if size_mb > 500 and row["table"] in SAFE_TABLES:
                await ch_query(f"TRUNCATE TABLE system.{row['table']}")
                logger.info("Truncated system.%s (was %.0f MB)", row["table"], size_mb)
    except Exception:
        logger.debug("ClickHouse log cleanup skipped", exc_info=True)


# ── Scheduler lifecycle ──────────────────────────────────────────────────────


async def run_correlation():
    """Run the correlation engine."""
    from services.correlation import run_correlation as _run
    await _run()


async def run_log_intelligence():
    """Run the log intelligence engine (template flush, baselines, precursors)."""
    from services.log_intelligence import run_intelligence
    await run_intelligence()


async def run_scheduled_scans():
    """Run due scheduled subnet scans."""
    from routers.subnet_scanner import run_scheduled_scans as _run
    await _run()


async def run_snmp_polls():
    """Run due SNMP polls."""
    from routers.snmp import run_snmp_polls as _run
    await _run()


async def run_port_discovery():
    """Discover open ports and SSL certs on monitored hosts."""
    from services.port_discovery import run_port_discovery as _run
    await _run()


async def run_weekly_digest():
    """Build and send weekly digest email."""
    from database import get_setting, decrypt_value
    from services.digest import build_weekly_digest, format_digest_html, format_digest_text
    from notifications import _send_email, _log_notification

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "digest_enabled", "0")
        if enabled != "1":
            return

        smtp_host = await get_setting(db, "smtp_host", "")
        smtp_user = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_to = await get_setting(db, "smtp_to", "")
        if not (smtp_host and smtp_user and smtp_pw_enc and smtp_to):
            logger.warning("Weekly digest skipped: SMTP not configured")
            return

        smtp_port = int(await get_setting(db, "smtp_port", "587"))
        smtp_from = await get_setting(db, "smtp_from", "") or smtp_user

        try:
            smtp_pw = decrypt_value(smtp_pw_enc)
        except Exception:
            smtp_pw = smtp_pw_enc

        digest = await build_weekly_digest(db)

    html_body = format_digest_html(digest)
    text_body = format_digest_text(digest)

    try:
        await _send_email(
            smtp_host, smtp_port, smtp_user, smtp_pw,
            smtp_from, smtp_to,
            "[Nodeglow] Weekly Digest", text_body, html_body,
        )
        await _log_notification("email", "Weekly Digest", "Automated weekly digest", "info", "sent")
        logger.info("Weekly digest email sent to %s", smtp_to)
    except Exception as exc:
        await _log_notification("email", "Weekly Digest", "Automated weekly digest", "info", "failed", str(exc))
        logger.error("Weekly digest email failed: %s", exc)


async def run_daily_ai_summary():
    """Build AI-powered daily summary and send via configured notification channels."""
    from database import get_setting, set_setting
    from services.digest import (
        build_daily_summary_data, format_daily_summary_prompt,
        _DAILY_SUMMARY_SYSTEM_PROMPT,
    )
    from services.ai_client import generate_completion
    from notifications import (
        _send_telegram, _send_discord, _log_notification,
        _send_webhook, _send_email, _build_html_email,
    )
    from models.ai_usage import AiUsageLog

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "daily_ai_summary_enabled", "0")
        if enabled != "1":
            return

        # ── Duplicate protection: skip if already sent today ──────────────
        last_sent_str = await get_setting(db, "daily_ai_summary_last_sent", "")
        if last_sent_str:
            try:
                last_sent = datetime.fromisoformat(last_sent_str)
                if (datetime.utcnow() - last_sent).total_seconds() < 20 * 3600:
                    logger.info("Daily AI summary skipped: already sent at %s", last_sent_str)
                    return
            except ValueError:
                pass  # corrupted value, proceed

        # Check if AI key is configured
        claude_key = await get_setting(db, "claude_api_key", "")
        if not claude_key:
            logger.warning("Daily AI summary skipped: no Claude API key configured")
            return

        # Collect 24h data
        data = await build_daily_summary_data(db)

    # Skip if nothing happened
    inc_total = data.get("incidents", {}).get("total", 0)
    down_hosts = len(data.get("hosts", {}).get("down", []))
    syslog_errors = data.get("syslog", {}).get("errors", 0)
    unhealthy_int = len(data.get("integrations", []))
    ssl_expiring = len(data.get("ssl_expiring", []))

    if inc_total == 0 and down_hosts == 0 and syslog_errors == 0 and unhealthy_int == 0 and ssl_expiring == 0:
        logger.info("Daily AI summary skipped: no events in last 24h")
        return

    # ── Generate AI analysis (with token tracking) ────────────────────────
    prompt = format_daily_summary_prompt(data)
    try:
        summary, usage = await generate_completion(
            _DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True,
        )
    except Exception as exc:
        logger.error("Daily AI summary generation failed: %s", exc)
        await _log_notification("ai", "Daily AI Summary", "AI generation failed", "info", "failed", str(exc))
        return

    # ── Log token usage ───────────────────────────────────────────────────
    try:
        # Haiku pricing: $1/MTok input, $5/MTok output
        _COST_PER_INPUT = 1.0 / 1_000_000
        _COST_PER_OUTPUT = 5.0 / 1_000_000
        cost = (usage["input_tokens"] * _COST_PER_INPUT
                + usage["output_tokens"] * _COST_PER_OUTPUT)
        async with AsyncSessionLocal() as db:
            db.add(AiUsageLog(
                feature="daily_summary",
                model=usage.get("model", "unknown"),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=round(cost, 6),
            ))
            await db.commit()
        logger.info("Daily AI summary: %d in + %d out tokens ($%.4f)",
                     usage["input_tokens"], usage["output_tokens"], cost)
    except Exception as exc:
        logger.warning("Failed to log AI usage: %s", exc)

    # ── Read channel config + selected channels ───────────────────────────
    title = "Daily AI Summary"
    sent = False

    async with AsyncSessionLocal() as db:
        notify_enabled = await get_setting(db, "notify_enabled", "0")
        if notify_enabled != "1":
            logger.warning("Daily AI summary: notifications disabled")
            return

        # Which channels should receive the summary (default: all)
        channels_csv = await get_setting(db, "daily_ai_summary_channels", "telegram,discord,webhook,email")
        selected = {c.strip() for c in channels_csv.split(",") if c.strip()}

        tg_token = await get_setting(db, "telegram_bot_token", "")
        tg_chat = await get_setting(db, "telegram_chat_id", "")
        dc_webhook = await get_setting(db, "discord_webhook_url", "")
        wh_url = await get_setting(db, "webhook_url", "")
        wh_secret = await get_setting(db, "webhook_secret", "")
        smtp_host = await get_setting(db, "smtp_host", "")
        smtp_user = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_to = await get_setting(db, "smtp_to", "")
        smtp_port = int(await get_setting(db, "smtp_port", "587"))
        smtp_from = await get_setting(db, "smtp_from", "") or smtp_user

    # Telegram
    if "telegram" in selected and tg_token and tg_chat:
        try:
            tg_text = f"<b>🤖 {title}</b>\n\n{summary}"
            if len(tg_text) > 4096:
                tg_text = tg_text[:4090] + "\n[…]"
            await _send_telegram(tg_token, tg_chat, tg_text)
            await _log_notification("telegram", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Telegram failed: %s", exc)
            await _log_notification("telegram", title, summary[:200], "info", "failed", str(exc))

    # Discord
    if "discord" in selected and dc_webhook:
        try:
            desc = summary[:4090] if len(summary) > 4090 else summary
            await _send_discord(dc_webhook, f"🤖 {title}", desc, 0x8B5CF6)
            await _log_notification("discord", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Discord failed: %s", exc)
            await _log_notification("discord", title, summary[:200], "info", "failed", str(exc))

    # Webhook
    if "webhook" in selected and wh_url:
        try:
            await _send_webhook(wh_url, wh_secret, title, summary, "info")
            await _log_notification("webhook", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Webhook failed: %s", exc)
            await _log_notification("webhook", title, summary[:200], "info", "failed", str(exc))

    # Email
    if "email" in selected and smtp_host and smtp_user and smtp_pw_enc and smtp_to:
        try:
            from database import decrypt_value
            try:
                smtp_pw = decrypt_value(smtp_pw_enc)
            except Exception:
                smtp_pw = smtp_pw_enc
            html_body = _build_html_email(title, summary, "info")
            await _send_email(
                smtp_host, smtp_port, smtp_user, smtp_pw,
                smtp_from, smtp_to,
                f"[Nodeglow] {title}", f"{title}\n\n{summary}", html_body,
            )
            await _log_notification("email", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Email failed: %s", exc)
            await _log_notification("email", title, summary[:200], "info", "failed", str(exc))

    # ── Mark as sent (duplicate protection) ───────────────────────────────
    if sent:
        async with AsyncSessionLocal() as db:
            await set_setting(db, "daily_ai_summary_last_sent", datetime.utcnow().isoformat())
            await db.commit()
        logger.info("Daily AI summary sent successfully")
    else:
        logger.warning("Daily AI summary: no notification channel configured or selected")


async def update_geoip():
    """Weekly GeoIP database update (if enabled and license key is set)."""
    from database import get_setting, decrypt_value
    from services.geoip_updater import download_geolite2

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "geoip_enabled", "0")
        if enabled != "1":
            return

        key_enc = await get_setting(db, "geoip_license_key", "")
        if not key_enc:
            return

        try:
            license_key = decrypt_value(key_enc)
        except Exception:
            license_key = key_enc

    result = await download_geolite2(license_key)

    if result["success"]:
        from database import set_setting
        async with AsyncSessionLocal() as db:
            await set_setting(db, "geoip_last_updated", datetime.utcnow().isoformat())
            await db.commit()
        logger.info("GeoIP database updated (%.1f MB)", result.get("size_mb", 0))
    else:
        logger.error("GeoIP update failed: %s", result.get("message", "unknown error"))


async def run_alert_rules():
    """Evaluate all user-defined alert rules."""
    from services.rules import evaluate_rules
    async with AsyncSessionLocal() as db:
        triggered = await evaluate_rules(db)
        if triggered:
            logger.info("Alert rules: %d rule(s) triggered", triggered)


async def start_scheduler():
    """Read intervals from DB, then register and start all jobs."""
    from database import get_setting

    async with AsyncSessionLocal() as db:
        ping_interval = int(await get_setting(db, "ping_interval", "60"))
        integration_interval = int(await get_setting(db, "proxmox_interval", "60"))

    scheduler.add_job(run_ping_checks, "interval", seconds=ping_interval,
                      id="ping_checks", replace_existing=True)
    scheduler.add_job(run_integration_checks, "interval", seconds=integration_interval,
                      id="integration_checks", replace_existing=True)
    scheduler.add_job(run_correlation, "interval", seconds=60,
                      id="correlation", replace_existing=True)
    scheduler.add_job(update_ssl_expiry, "interval", hours=6,
                      id="ssl_expiry", replace_existing=True)
    scheduler.add_job(cleanup_old_results, "cron", hour=3, minute=0,
                      id="cleanup", replace_existing=True)
    scheduler.add_job(run_log_intelligence, "interval", seconds=30,
                      id="log_intelligence", replace_existing=True)
    scheduler.add_job(run_scheduled_scans, "interval", seconds=60,
                      id="subnet_scans", replace_existing=True)
    scheduler.add_job(run_snmp_polls, "interval", seconds=30,
                      id="snmp_polls", replace_existing=True)
    scheduler.add_job(run_alert_rules, "interval", seconds=60,
                      id="alert_rules", replace_existing=True)
    scheduler.add_job(run_port_discovery, "interval", hours=6,
                      id="port_discovery", replace_existing=True)
    scheduler.add_job(check_disk_space, "interval", minutes=30,
                      id="disk_space", replace_existing=True)
    scheduler.add_job(cleanup_clickhouse_logs, "cron", hour=4, minute=0,
                      id="ch_cleanup", replace_existing=True)

    # GeoIP database update (Tuesdays at 03:00 — MaxMind updates weekly on Tuesdays)
    scheduler.add_job(update_geoip, "cron",
                      day_of_week="tue", hour=3, minute=0,
                      id="geoip_update", replace_existing=True)

    # Weekly digest email (default: Monday 9:00, configurable)
    digest_day = int(await get_setting(db, "digest_day", "0"))  # 0=Mon
    digest_hour = int(await get_setting(db, "digest_hour", "9"))
    scheduler.add_job(run_weekly_digest, "cron",
                      day_of_week=digest_day, hour=digest_hour, minute=0,
                      id="weekly_digest", replace_existing=True)

    # Daily AI summary (default: 08:00, configurable)
    ai_summary_hour = int(await get_setting(db, "daily_ai_summary_hour", "8"))
    scheduler.add_job(run_daily_ai_summary, "cron",
                      hour=ai_summary_hour, minute=0,
                      id="daily_ai_summary", replace_existing=True)

    scheduler.start()

    # Seed default SNMP OIDs
    try:
        from services.snmp import seed_default_oids
        async with AsyncSessionLocal() as db:
            await seed_default_oids(db)
    except Exception:
        pass
    logger.info("Scheduler started (ping=%ds, integrations=%ds)",
                ping_interval, integration_interval)


def stop_scheduler():
    scheduler.shutdown(wait=False)

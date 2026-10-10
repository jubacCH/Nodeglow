"""AI daily summary (enterprise).

Collects the last 24 hours, asks the configured AI provider for a short
briefing and sends it to the selected notification channels. Scheduled daily
(``daily_ai_summary_hour``, default 08:00) and triggerable once from the
settings tab (POST /settings/ai/test-summary).

The settings it reads (``daily_ai_summary_enabled/_hour/_channels``) are
stored by the core settings endpoints; the opt-in and provider come from
``services.ai_config``.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, PingHost, get_db, get_setting
from models.incident import Incident
from models.integration import IntegrationConfig, Snapshot
from models.log_template import LogTemplate
from notification_channels import DAILY_SUMMARY_DEFAULT_CHANNELS
from ratelimit import rate_limit
from routers.settings._helpers import require_admin
from services import ai_client
from services.ai_config import AI_DISABLED_MESSAGE, load_ai_config

from nodeglow_ee import license_runtime

logger = logging.getLogger("nodeglow.ee.daily_summary")

# Functions are looked up through the module at call time, so tests can patch
# ``nodeglow_ee.ai.daily_summary.build_daily_summary_data`` and friends.
_self = sys.modules[__name__]

JOB_ID = "daily_ai_summary"
CRON_MISFIRE_GRACE_SECONDS = 3600

router = APIRouter(prefix="/settings")


# ── Data + prompt ─────────────────────────────────────────────────────────────


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


DAILY_SUMMARY_SYSTEM_PROMPT = """\
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


# ── Scheduled job ─────────────────────────────────────────────────────────────


async def run_daily_ai_summary():
    """Build AI-powered daily summary and send via configured notification channels."""
    from database import set_setting
    from notifications import (
        _send_telegram, _send_discord, _log_notification,
        _send_webhook, _send_email, _build_html_email,
    )
    from models.ai_usage import AiUsageLog

    if not await license_runtime.is_active("ai_daily_summary"):
        logger.info("Daily AI summary skipped: not covered by the license")
        return

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

        # Opt-in and provider: nothing is collected or sent without them.
        ai_cfg = await load_ai_config(db)
        if not ai_cfg.enabled:
            logger.info("Daily AI summary skipped: AI features are disabled (Settings > AI)")
            return
        if not ai_cfg.configured:
            logger.warning("Daily AI summary skipped: AI provider not configured")
            return

        # Collect 24h data
        data = await _self.build_daily_summary_data(db)

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
    prompt = _self.format_daily_summary_prompt(data)
    try:
        summary, usage = await ai_client.generate_completion(
            DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True, config=ai_cfg,
        )
    except Exception as exc:
        logger.error("Daily AI summary generation failed: %s", exc)
        await _log_notification("ai", "Daily AI Summary", "AI generation failed", "info", "failed", str(exc))
        return

    # ── Log token usage ───────────────────────────────────────────────────
    try:
        cost = ai_client.estimate_cost_usd(usage)
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
        from notification_channels import load_config as load_channel_config
        from services.channel_secrets import reveal
        channels_csv = (await get_setting(db, "daily_ai_summary_channels", "")
                        or DAILY_SUMMARY_DEFAULT_CHANNELS)
        selected = {c.strip() for c in channels_csv.split(",") if c.strip()}

        tg_token = reveal(await get_setting(db, "telegram_bot_token", ""))
        tg_chat = await get_setting(db, "telegram_chat_id", "")
        dc_webhook = reveal(await get_setting(db, "discord_webhook_url", ""))
        wh_url = reveal(await get_setting(db, "webhook_url", ""))
        wh_secret = reveal(await get_setting(db, "webhook_secret", ""))
        smtp_host = await get_setting(db, "smtp_host", "")
        smtp_user = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_to = await get_setting(db, "smtp_to", "")
        smtp_port = int(await get_setting(db, "smtp_port", "587"))
        smtp_from = await get_setting(db, "smtp_from", "") or smtp_user
        from database import decrypt_value as _decrypt
        channel_cfg = await load_channel_config(db, get_setting, _decrypt)

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

    # Teams / Slack / ntfy
    from notification_channels import send_to_selected
    for ch, exc in await send_to_selected(channel_cfg, selected, f"🤖 {title}", summary):
        if exc is None:
            await _log_notification(ch, title, summary[:200], "info", "sent")
            sent = True
        else:
            logger.error("Daily AI summary %s failed: %s", ch, exc)
            await _log_notification(ch, title, summary[:200], "info", "failed", str(exc))

    # ── Mark as sent (duplicate protection) ───────────────────────────────
    if sent:
        async with AsyncSessionLocal() as db:
            await set_setting(db, "daily_ai_summary_last_sent", datetime.utcnow().isoformat())
            await db.commit()
        logger.info("Daily AI summary sent successfully")
    else:
        logger.warning("Daily AI summary: no notification channel configured or selected")


async def schedule(scheduler) -> None:
    """Scheduler hook: add the daily job at the configured hour (default 08:00)."""
    import database  # looked up at call time, like the core start_scheduler()

    async with AsyncSessionLocal() as db:
        hour = int(await database.get_setting(db, "daily_ai_summary_hour", "8") or "8")
    scheduler.add_job(run_daily_ai_summary, "cron",
                      hour=hour, minute=0,
                      id=JOB_ID, replace_existing=True,
                      misfire_grace_time=CRON_MISFIRE_GRACE_SECONDS)


# ── One-off test send (settings tab) ──────────────────────────────────────────


@router.post("/ai/test-summary")
@rate_limit(max_requests=3, window_seconds=60)
async def test_daily_ai_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a one-off daily AI summary (ignores schedule + duplicate protection)."""
    if err := require_admin(request):
        return err
    if refused := await license_runtime.blocked("ai_daily_summary"):
        return refused

    from notifications import (
        _send_telegram, _send_discord, _send_webhook, _send_email, _build_html_email,
    )
    from database import decrypt_value

    ai_cfg = await load_ai_config(db)
    if not ai_cfg.enabled:
        return JSONResponse({"ok": False, "code": "ai_disabled", "message": AI_DISABLED_MESSAGE},
                            status_code=409)
    if not ai_cfg.configured:
        return JSONResponse({"ok": False, "code": "ai_not_configured",
                             "message": "AI provider not configured"}, status_code=400)

    data = await _self.build_daily_summary_data(db)

    prompt = _self.format_daily_summary_prompt(data)
    try:
        summary, usage = await ai_client.generate_completion(
            DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True, config=ai_cfg,
        )
    except Exception as exc:
        logger.error("Test AI summary generation failed: %s", exc)
        return JSONResponse({"ok": False, "message": f"AI generation failed: {exc}"}, status_code=500)

    try:
        from models.ai_usage import AiUsageLog
        cost = ai_client.estimate_cost_usd(usage)
        db.add(AiUsageLog(
            feature="daily_summary_test",
            model=usage.get("model", "unknown"),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=round(cost, 6),
        ))
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to log AI usage: %s", exc)

    title = "Daily AI Summary (Test)"
    channels_csv = await get_setting(db, "daily_ai_summary_channels", "") or DAILY_SUMMARY_DEFAULT_CHANNELS
    selected = {c.strip() for c in channels_csv.split(",") if c.strip()}
    sent = False
    errors = []

    from services.channel_secrets import reveal
    tg_token = reveal(await get_setting(db, "telegram_bot_token", ""))
    tg_chat = await get_setting(db, "telegram_chat_id", "")
    dc_webhook = reveal(await get_setting(db, "discord_webhook_url", ""))
    wh_url = reveal(await get_setting(db, "webhook_url", ""))
    wh_secret = reveal(await get_setting(db, "webhook_secret", ""))
    smtp_host = await get_setting(db, "smtp_host", "")
    smtp_user = await get_setting(db, "smtp_user", "")
    smtp_pw_enc = await get_setting(db, "smtp_password", "")
    smtp_to = await get_setting(db, "smtp_to", "")
    smtp_port = int(await get_setting(db, "smtp_port", "587"))
    smtp_from = await get_setting(db, "smtp_from", "") or smtp_user

    if "telegram" in selected and tg_token and tg_chat:
        try:
            tg_text = f"<b>🤖 {title}</b>\n\n{summary}"
            if len(tg_text) > 4096:
                tg_text = tg_text[:4090] + "\n[…]"
            await _send_telegram(tg_token, tg_chat, tg_text)
            sent = True
        except Exception as exc:
            errors.append(f"Telegram: {exc}")

    if "discord" in selected and dc_webhook:
        try:
            desc = summary[:4090] if len(summary) > 4090 else summary
            await _send_discord(dc_webhook, f"🤖 {title}", desc, 0x8B5CF6)
            sent = True
        except Exception as exc:
            errors.append(f"Discord: {exc}")

    if "webhook" in selected and wh_url:
        try:
            await _send_webhook(wh_url, wh_secret, title, summary, "info")
            sent = True
        except Exception as exc:
            errors.append(f"Webhook: {exc}")

    if "email" in selected and smtp_host and smtp_user and smtp_pw_enc and smtp_to:
        try:
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
            sent = True
        except Exception as exc:
            errors.append(f"Email: {exc}")

    from notification_channels import load_config as load_channel_config, send_to_selected
    channel_cfg = await load_channel_config(db, get_setting, decrypt_value)
    for ch, exc in await send_to_selected(channel_cfg, selected, f"🤖 {title}", summary):
        if exc is None:
            sent = True
        else:
            errors.append(f"{ch}: {exc}")

    if sent:
        msg = "Test summary sent"
        if errors:
            msg += f" (some channels failed: {'; '.join(errors)})"
        return JSONResponse({"ok": True, "message": msg})
    elif errors:
        return JSONResponse({"ok": False, "message": f"All channels failed: {'; '.join(errors)}"}, status_code=500)
    else:
        return JSONResponse({"ok": False, "message": "No notification channels configured"}, status_code=400)

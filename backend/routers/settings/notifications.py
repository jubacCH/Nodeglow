"""Notification settings: channel config, digest schedule, test, history."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import encrypt_value, get_db, set_setting
from ratelimit import rate_limit
from services.audit import log_action

from ._helpers import log, require_admin

router = APIRouter()

EXTRA_CHANNELS = ("teams", "slack", "ntfy")
# Secrets of the Teams/Slack/ntfy channels, stored via encrypt_value like
# smtp_password. Teams Workflows and Slack webhook URLs carry their own
# credential, so they count as secrets and are never sent back to the UI.
EXTRA_SECRET_KEYS = ("teams_webhook_url", "slack_webhook_url", "ntfy_token")


def _validate_extra_channels(*, public_url, teams_webhook_url, slack_webhook_url,
                             ntfy_server_url, ntfy_topic) -> str | None:
    """Return an error message for an unusable value, else None."""
    from notification_channels import NTFY_TOPIC_RE
    from notifications import _is_safe_url

    if public_url and public_url.strip():
        if not public_url.strip().lower().startswith(("http://", "https://")):
            return "Public URL must start with http:// or https://"
    for label, url in (("Teams webhook URL", teams_webhook_url),
                       ("Slack webhook URL", slack_webhook_url),
                       ("ntfy server URL", ntfy_server_url)):
        if url and url.strip() and not _is_safe_url(url.strip()):
            return f"{label} is not allowed"
    if ntfy_topic and ntfy_topic.strip() and not NTFY_TOPIC_RE.match(ntfy_topic.strip()):
        return "ntfy topic may only contain letters, digits, '-' and '_' (max 64)"
    return None


async def _save_extra_channels(db: AsyncSession, valid_severities: set[str],
                               values: dict[str, str | None]) -> None:
    from notification_channels import DEFAULT_NTFY_SERVER

    def given(key: str) -> bool:
        return values.get(key) is not None

    if given("public_url"):
        await set_setting(db, "public_url", values["public_url"].strip().rstrip("/"))
    for ch in EXTRA_CHANNELS:
        if given(f"{ch}_enabled"):
            await set_setting(db, f"{ch}_enabled",
                              "1" if values[f"{ch}_enabled"] in ("1", "on", "true") else "0")
        sev_key = f"notify_{ch}_min_severity"
        if given(sev_key):
            sev = values[sev_key].strip()
            await set_setting(db, sev_key, sev if sev in valid_severities else "all")
    if given("ntfy_server_url"):
        await set_setting(db, "ntfy_server_url",
                          values["ntfy_server_url"].strip().rstrip("/") or DEFAULT_NTFY_SERVER)
    if given("ntfy_topic"):
        await set_setting(db, "ntfy_topic", values["ntfy_topic"].strip())
    for key in EXTRA_SECRET_KEYS:
        if values.get(f"{key}_clear") == "1":
            await set_setting(db, key, "")
        elif given(key) and values[key].strip():
            await set_setting(db, key, encrypt_value(values[key].strip()))



@router.post("/notifications/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_notifications(
    request: Request,
    notify_enabled:      str = Form("0"),
    notify_grace_minutes: str = Form("5"),
    correlation_min_failures: str = Form("3"),
    correlation_min_cycles:   str = Form("2"),
    telegram_bot_token:  str = Form(""),
    telegram_chat_id:    str = Form(""),
    discord_webhook_url: str = Form(""),
    webhook_url:         str = Form(""),
    webhook_secret:      str = Form(""),
    smtp_host:           str = Form(""),
    smtp_port:           str = Form("587"),
    smtp_user:           str = Form(""),
    smtp_password:       str = Form(""),
    smtp_from:           str = Form(""),
    smtp_to:             str = Form(""),
    notify_telegram_min_severity: str = Form("all"),
    notify_discord_min_severity:  str = Form("all"),
    notify_webhook_min_severity:  str = Form("all"),
    notify_email_min_severity:    str = Form("all"),
    # Teams / Slack / ntfy and the public URL. None = field not sent, keep the
    # stored value (older clients don't know these fields). Secret fields
    # left blank keep their stored value; *_clear="1" removes it.
    public_url:                str | None = Form(None),
    teams_enabled:             str | None = Form(None),
    teams_webhook_url:         str | None = Form(None),
    teams_webhook_url_clear:   str | None = Form(None),
    notify_teams_min_severity: str | None = Form(None),
    slack_enabled:             str | None = Form(None),
    slack_webhook_url:         str | None = Form(None),
    slack_webhook_url_clear:   str | None = Form(None),
    notify_slack_min_severity: str | None = Form(None),
    ntfy_enabled:              str | None = Form(None),
    ntfy_server_url:           str | None = Form(None),
    ntfy_topic:                str | None = Form(None),
    ntfy_token:                str | None = Form(None),
    ntfy_token_clear:          str | None = Form(None),
    notify_ntfy_min_severity:  str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if err := require_admin(request):
        return err
    extra_error = _validate_extra_channels(
        public_url=public_url, teams_webhook_url=teams_webhook_url,
        slack_webhook_url=slack_webhook_url, ntfy_server_url=ntfy_server_url,
        ntfy_topic=ntfy_topic,
    )
    if extra_error:
        return JSONResponse({"ok": False, "message": extra_error}, status_code=400)
    await set_setting(db, "notify_enabled", "1" if notify_enabled == "on" else "0")
    try:
        grace_val = str(max(0, int(notify_grace_minutes.strip() or "5")))
    except (ValueError, TypeError):
        grace_val = "5"
    await set_setting(db, "notify_grace_minutes", grace_val)

    try:
        min_fail = str(max(1, min(10, int(correlation_min_failures.strip() or "3"))))
    except (ValueError, TypeError):
        min_fail = "3"
    await set_setting(db, "correlation_min_failures", min_fail)
    try:
        min_cyc = str(max(1, min(10, int(correlation_min_cycles.strip() or "2"))))
    except (ValueError, TypeError):
        min_cyc = "2"
    await set_setting(db, "correlation_min_cycles", min_cyc)

    await set_setting(db, "telegram_bot_token", telegram_bot_token.strip())
    await set_setting(db, "telegram_chat_id", telegram_chat_id.strip())
    await set_setting(db, "discord_webhook_url", discord_webhook_url.strip())
    await set_setting(db, "webhook_url", webhook_url.strip())
    await set_setting(db, "webhook_secret", webhook_secret.strip())
    await set_setting(db, "smtp_host", smtp_host.strip())
    await set_setting(db, "smtp_port", smtp_port.strip() or "587")
    await set_setting(db, "smtp_user", smtp_user.strip())
    await set_setting(db, "smtp_from", smtp_from.strip())
    await set_setting(db, "smtp_to", smtp_to.strip())
    if smtp_password.strip():
        await set_setting(db, "smtp_password", encrypt_value(smtp_password.strip()))

    valid_severities = {"all", "warning", "error", "critical"}
    for key, val in [
        ("notify_telegram_min_severity", notify_telegram_min_severity),
        ("notify_discord_min_severity", notify_discord_min_severity),
        ("notify_webhook_min_severity", notify_webhook_min_severity),
        ("notify_email_min_severity", notify_email_min_severity),
    ]:
        await set_setting(db, key, val.strip() if val.strip() in valid_severities else "all")

    await _save_extra_channels(db, valid_severities, {
        "public_url": public_url,
        "teams_enabled": teams_enabled,
        "teams_webhook_url": teams_webhook_url,
        "teams_webhook_url_clear": teams_webhook_url_clear,
        "notify_teams_min_severity": notify_teams_min_severity,
        "slack_enabled": slack_enabled,
        "slack_webhook_url": slack_webhook_url,
        "slack_webhook_url_clear": slack_webhook_url_clear,
        "notify_slack_min_severity": notify_slack_min_severity,
        "ntfy_enabled": ntfy_enabled,
        "ntfy_server_url": ntfy_server_url,
        "ntfy_topic": ntfy_topic,
        "ntfy_token": ntfy_token,
        "ntfy_token_clear": ntfy_token_clear,
        "notify_ntfy_min_severity": notify_ntfy_min_severity,
    })

    await db.commit()
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1&tab=notifications", status_code=303)


@router.post("/digest/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_digest_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if err := require_admin(request):
        return err
    body = await request.json()
    digest_enabled = "1" if body.get("digest_enabled") else "0"
    digest_day = str(max(0, min(6, int(body.get("digest_day", 0)))))
    digest_hour = str(max(0, min(23, int(body.get("digest_hour", 9)))))

    await set_setting(db, "digest_enabled", digest_enabled)
    await set_setting(db, "digest_day", digest_day)
    await set_setting(db, "digest_hour", digest_hour)

    from scheduler import scheduler
    job = scheduler.get_job("weekly_digest")
    if job:
        job.reschedule(trigger="cron", day_of_week=int(digest_day),
                       hour=int(digest_hour), minute=0)

    await log_action(db, request, "digest.settings.update")
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/notifications/test")
@rate_limit(max_requests=10, window_seconds=60)
async def test_notification(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        channel = body.get("channel", "")
    else:
        form = await request.form()
        channel = form.get("channel", "")
    from notifications import (
        _send_telegram, _send_discord, _send_webhook, _send_email,
        _build_html_email, _log_notification,
    )
    from database import decrypt_value, get_setting
    try:
        if channel == "telegram":
            token = await get_setting(db, "telegram_bot_token", "")
            chat  = await get_setting(db, "telegram_chat_id", "")
            await _send_telegram(token, chat, "<b>Nodeglow Test</b>\nNotifications are working ✓")
        elif channel == "discord":
            url = await get_setting(db, "discord_webhook_url", "")
            await _send_discord(url, "Nodeglow Test", "Notifications are working ✓", 0x3498db)
        elif channel == "webhook":
            url    = await get_setting(db, "webhook_url", "")
            secret = await get_setting(db, "webhook_secret", "")
            await _send_webhook(url, secret, "Nodeglow Test", "Notifications are working", "info")
        elif channel == "email":
            host  = await get_setting(db, "smtp_host", "")
            port  = int(await get_setting(db, "smtp_port", "587"))
            user  = await get_setting(db, "smtp_user", "")
            pw    = decrypt_value(await get_setting(db, "smtp_password", ""))
            frm   = await get_setting(db, "smtp_from", "") or user
            to    = await get_setting(db, "smtp_to", "")
            html  = _build_html_email("Nodeglow Test", "Notifications are working ✓", "info")
            await _send_email(host, port, user, pw, frm, to,
                              "[Nodeglow] Test", "Nodeglow Test\nNotifications are working ✓", html)
        elif channel in EXTRA_CHANNELS:
            from notification_channels import build_link, load_config, make_send
            cfg = await load_config(db, get_setting, decrypt_value)
            await make_send(cfg, channel, "Nodeglow Test", "Notifications are working ✓",
                            "info", build_link(cfg.public_url, None))
        else:
            return JSONResponse({"ok": False, "message": "Unknown channel"}, status_code=400)
        await _log_notification(channel, "Test Notification", "Manual test", "info", "sent")
        return JSONResponse({"ok": True, "message": "Test notification sent"})
    except Exception as e:
        log.error("Test notification failed: %s", e)
        await _log_notification(channel, "Test Notification", "Manual test", "info", "failed", str(e))
        from notification_channels import ChannelError
        if isinstance(e, ChannelError):
            # Built without URLs or tokens, so it is safe to show.
            return JSONResponse({"ok": False, "message": str(e)}, status_code=500)
        return JSONResponse({"ok": False, "message": "Notification failed. Check server logs."}, status_code=500)


@router.get("/notifications/history")
async def notification_history(db: AsyncSession = Depends(get_db)):
    """Return last 50 notification log entries."""
    from models.notification import NotificationLog
    result = await db.execute(
        select(NotificationLog)
        .order_by(NotificationLog.timestamp.desc())
        .limit(50)
    )
    logs = result.scalars().all()
    return JSONResponse([
        {
            "id": n.id,
            "timestamp": n.timestamp.isoformat() if n.timestamp else None,
            "channel": n.channel,
            "title": n.title,
            "severity": n.severity,
            "status": n.status,
            "error": n.error,
        }
        for n in logs
    ])

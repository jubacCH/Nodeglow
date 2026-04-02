import hashlib
import logging
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import encrypt_value, get_db, get_setting, set_setting
from models.api_key import ApiKey
from models.integration import IntegrationConfig
from ratelimit import rate_limit
from services.audit import log_action

router = APIRouter(prefix="/settings")
log = logging.getLogger(__name__)


def _require_admin(request: Request):
    """Return a 403 JSONResponse if the current user is not an admin, else None."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return None


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    site_name           = await get_setting(db, "site_name", "NODEGLOW")
    ping_interval       = await get_setting(db, "ping_interval", "60")
    proxmox_interval    = await get_setting(db, "proxmox_interval", "60")
    ping_retention      = await get_setting(db, "ping_retention_days", "30")
    proxmox_retention   = await get_setting(db, "proxmox_retention_days", "7")
    anomaly_threshold   = await get_setting(db, "anomaly_threshold", "2.0")
    timezone            = await get_setting(db, "timezone", "UTC")

    latency_threshold   = await get_setting(db, "latency_threshold_ms", "")
    cpu_threshold       = await get_setting(db, "proxmox_cpu_threshold", "85")
    ram_threshold       = await get_setting(db, "proxmox_ram_threshold", "85")
    disk_threshold      = await get_setting(db, "proxmox_disk_threshold", "90")

    phpipam_url         = await get_setting(db, "phpipam_url", "")
    phpipam_app_id      = await get_setting(db, "phpipam_app_id", "")
    phpipam_username    = await get_setting(db, "phpipam_username", "")
    phpipam_has_pw      = bool(await get_setting(db, "phpipam_password", ""))
    phpipam_verify_ssl  = await get_setting(db, "phpipam_verify_ssl", "1")
    phpipam_sync_hours  = await get_setting(db, "phpipam_sync_hours", "0")
    integration_retention = await get_setting(db, "integration_retention_days", "7")

    syslog_port         = await get_setting(db, "syslog_port", "1514")
    syslog_allowlist    = await get_setting(db, "syslog_allowlist_only", "0")

    digest_enabled      = await get_setting(db, "digest_enabled", "0")
    digest_day          = await get_setting(db, "digest_day", "0")
    digest_hour         = await get_setting(db, "digest_hour", "9")

    notify_enabled      = await get_setting(db, "notify_enabled", "0")
    telegram_bot_token  = await get_setting(db, "telegram_bot_token", "")
    telegram_chat_id    = await get_setting(db, "telegram_chat_id", "")
    discord_webhook_url = await get_setting(db, "discord_webhook_url", "")
    webhook_url         = await get_setting(db, "webhook_url", "")
    webhook_secret      = await get_setting(db, "webhook_secret", "")
    smtp_host           = await get_setting(db, "smtp_host", "")
    smtp_port           = await get_setting(db, "smtp_port", "587")
    smtp_user           = await get_setting(db, "smtp_user", "")
    smtp_has_pw         = bool(await get_setting(db, "smtp_password", ""))
    smtp_from           = await get_setting(db, "smtp_from", "")
    smtp_to             = await get_setting(db, "smtp_to", "")

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "site_name": site_name,
        "ping_interval": ping_interval,
        "proxmox_interval": proxmox_interval,
        "ping_retention": ping_retention,
        "proxmox_retention": proxmox_retention,
        "anomaly_threshold": anomaly_threshold,
        "timezone": timezone,
        "latency_threshold": latency_threshold,
        "cpu_threshold": cpu_threshold,
        "ram_threshold": ram_threshold,
        "disk_threshold": disk_threshold,
        "phpipam_url": phpipam_url,
        "phpipam_app_id": phpipam_app_id,
        "phpipam_username": phpipam_username,
        "phpipam_has_pw": phpipam_has_pw,
        "phpipam_verify_ssl": phpipam_verify_ssl,
        "phpipam_sync_hours": phpipam_sync_hours,
        "integration_retention": integration_retention,
        "syslog_port": syslog_port,
        "syslog_allowlist_only": syslog_allowlist,
        "digest_enabled": digest_enabled,
        "digest_day": digest_day,
        "digest_hour": digest_hour,
        "notify_enabled": notify_enabled,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
        "discord_webhook_url": discord_webhook_url,
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_has_pw": smtp_has_pw,
        "smtp_from": smtp_from,
        "smtp_to": smtp_to,
        "active_page": "settings",
        "saved": request.query_params.get("saved"),
    })


@router.get("/json")
async def settings_json(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current settings as JSON for the SPA frontend."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    keys = [
        "site_name", "timezone", "ping_interval", "latency_threshold_ms",
        "proxmox_interval", "notify_enabled", "notify_grace_minutes",
        "telegram_bot_token", "telegram_chat_id",
        "discord_webhook_url", "webhook_url", "webhook_secret",
        "smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_to",
        "notify_telegram_min_severity", "notify_discord_min_severity",
        "notify_webhook_min_severity", "notify_email_min_severity",
        "ping_retention_days", "proxmox_retention_days", "integration_retention_days",
        "anomaly_threshold", "proxmox_cpu_threshold", "proxmox_ram_threshold",
        "proxmox_disk_threshold", "syslog_port", "syslog_allowlist_only",
        "digest_enabled", "digest_day", "digest_hour",
        "daily_ai_summary_enabled", "daily_ai_summary_hour", "daily_ai_summary_channels",
        "geoip_enabled", "geoip_last_updated",
    ]
    result = {}
    for key in keys:
        result[key] = await get_setting(db, key, "")
    # Defaults
    defaults = {
        "site_name": "NODEGLOW", "ping_interval": "60", "proxmox_interval": "60",
        "timezone": "UTC", "smtp_port": "587", "ping_retention_days": "30",
        "proxmox_retention_days": "7", "integration_retention_days": "7",
        "anomaly_threshold": "2.0", "proxmox_cpu_threshold": "85",
        "proxmox_ram_threshold": "85", "proxmox_disk_threshold": "90",
        "syslog_port": "1514",
        "notify_grace_minutes": "5",
        "digest_day": "0", "digest_hour": "9",
        "daily_ai_summary_hour": "8",
        "daily_ai_summary_channels": "telegram,discord,webhook,email",
    }
    for key, default in defaults.items():
        if not result.get(key):
            result[key] = default
    # Don't expose passwords, just indicate if set
    result["smtp_has_pw"] = bool(await get_setting(db, "smtp_password", ""))
    result["claude_has_key"] = bool(await get_setting(db, "claude_api_key", ""))
    result["geoip_has_key"] = bool(await get_setting(db, "geoip_license_key", ""))
    # LDAP settings
    ldap_keys = [
        "ldap_enabled", "ldap_server", "ldap_bind_dn", "ldap_base_dn",
        "ldap_user_filter", "ldap_display_attr", "ldap_group_attr",
        "ldap_admin_group", "ldap_editor_group", "ldap_use_ssl", "ldap_start_tls",
    ]
    ldap_defaults = {
        "ldap_user_filter": "(&(objectClass=person)(sAMAccountName={username}))",
        "ldap_display_attr": "displayName",
        "ldap_group_attr": "memberOf",
    }
    for key in ldap_keys:
        result[key] = await get_setting(db, key, ldap_defaults.get(key, ""))
    result["ldap_has_bind_pw"] = bool(await get_setting(db, "ldap_bind_password", ""))
    return JSONResponse(result)


@router.post("/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_settings(
    request: Request,
    site_name:          str = Form("NODEGLOW"),
    ping_interval:      str = Form("60"),
    proxmox_interval:   str = Form("60"),
    ping_retention:     str = Form("30"),
    proxmox_retention:  str = Form("7"),
    anomaly_threshold:  str = Form("2.0"),
    timezone:           str = Form("UTC"),
    latency_threshold:  str = Form(""),
    cpu_threshold:      str = Form("85"),
    ram_threshold:      str = Form("85"),
    disk_threshold:     str = Form("90"),
    integration_retention: str = Form("7"),
    syslog_port:        str = Form("1514"),
    syslog_allowlist_only: str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    if err := _require_admin(request):
        return err
    await set_setting(db, "site_name", site_name.strip())

    try:
        p_interval = max(10, min(3600, int(ping_interval)))
    except ValueError:
        p_interval = 60
    await set_setting(db, "ping_interval", str(p_interval))

    try:
        px_interval = max(10, min(3600, int(proxmox_interval)))
    except ValueError:
        px_interval = 60
    await set_setting(db, "proxmox_interval", str(px_interval))

    try:
        p_ret = max(1, min(365, int(ping_retention)))
    except ValueError:
        p_ret = 30
    await set_setting(db, "ping_retention_days", str(p_ret))

    try:
        px_ret = max(1, min(90, int(proxmox_retention)))
    except ValueError:
        px_ret = 7
    await set_setting(db, "proxmox_retention_days", str(px_ret))

    try:
        threshold = max(1.5, min(10.0, float(anomaly_threshold)))
    except ValueError:
        threshold = 2.0
    await set_setting(db, "anomaly_threshold", str(threshold))

    await set_setting(db, "timezone", timezone.strip() or "UTC")

    # Latency threshold (global default, empty = disabled)
    if latency_threshold.strip():
        try:
            lt = max(1, int(float(latency_threshold)))
            await set_setting(db, "latency_threshold_ms", str(lt))
        except ValueError:
            pass
    else:
        await set_setting(db, "latency_threshold_ms", "")

    try:
        int_ret = max(1, min(90, int(integration_retention)))
    except ValueError:
        int_ret = 7
    await set_setting(db, "integration_retention_days", str(int_ret))

    for key, val, lo, hi, default in [
        ("proxmox_cpu_threshold",  cpu_threshold,  1, 100, 85),
        ("proxmox_ram_threshold",  ram_threshold,  1, 100, 85),
        ("proxmox_disk_threshold", disk_threshold, 1, 100, 90),
    ]:
        try:
            pct = max(lo, min(hi, int(float(val))))
        except ValueError:
            pct = default
        await set_setting(db, key, str(pct))

    # Syslog port
    old_syslog_port = await get_setting(db, "syslog_port", "1514")
    try:
        new_sp = max(1, min(65535, int(syslog_port)))
    except ValueError:
        new_sp = 1514
    await set_setting(db, "syslog_port", str(new_sp))

    # Syslog allowlist (only accept from known hosts)
    await set_setting(db, "syslog_allowlist_only", "1" if syslog_allowlist_only == "1" else "0")

    # Restart syslog server if port changed
    if str(new_sp) != old_syslog_port:
        try:
            from services.syslog import stop_syslog_server, start_syslog_server
            await stop_syslog_server()
            await start_syslog_server(udp_port=new_sp, tcp_port=new_sp)
        except Exception:
            pass  # logged internally

    # Reschedule both jobs live
    from scheduler import scheduler
    ping_job = scheduler.get_job("ping_checks")
    if ping_job:
        ping_job.reschedule(trigger="interval", seconds=p_interval)
    px_job = scheduler.get_job("proxmox_checks")
    if px_job:
        px_job.reschedule(trigger="interval", seconds=px_interval)

    from main import invalidate_settings_cache
    invalidate_settings_cache()

    await log_action(db, request, "settings.update")
    await db.commit()

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


# ── phpIPAM ───────────────────────────────────────────────────────────────────

@router.post("/phpipam/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_phpipam(
    request: Request,
    phpipam_url:        str = Form(""),
    phpipam_app_id:     str = Form(""),
    phpipam_username:   str = Form(""),
    phpipam_password:   str = Form(""),
    phpipam_verify_ssl: str = Form("0"),
    phpipam_sync_hours: str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    if err := _require_admin(request):
        return err
    await set_setting(db, "phpipam_url", phpipam_url.strip().rstrip("/"))
    await set_setting(db, "phpipam_app_id", phpipam_app_id.strip())
    await set_setting(db, "phpipam_username", phpipam_username.strip())
    await set_setting(db, "phpipam_verify_ssl", "1" if phpipam_verify_ssl == "on" else "0")

    if phpipam_password.strip():
        await set_setting(db, "phpipam_password", encrypt_value(phpipam_password.strip()))

    try:
        sync_h = max(0, min(168, int(phpipam_sync_hours)))
    except ValueError:
        sync_h = 0
    await set_setting(db, "phpipam_sync_hours", str(sync_h))

    # Reschedule or remove phpIPAM sync job
    from scheduler import scheduler
    from integrations.phpipam import sync_phpipam_hosts
    from services import integration as _int_svc
    job = scheduler.get_job("phpipam_sync")
    if sync_h > 0:
        if job:
            job.reschedule(trigger="interval", hours=sync_h)
        else:
            async def _sync():
                from database import AsyncSessionLocal
                from models.integration import IntegrationConfig
                async with AsyncSessionLocal() as _db:
                    cfgs = (await _db.execute(
                        select(IntegrationConfig).where(IntegrationConfig.type == "phpipam")
                    )).scalars().all()
                    for cfg in cfgs:
                        config_dict = _int_svc.decrypt_config(cfg.config_json)
                        await sync_phpipam_hosts(_db, config_dict)
            scheduler.add_job(_sync, "interval", hours=sync_h, id="phpipam_sync", replace_existing=True)
    else:
        if job:
            job.remove()

    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/phpipam/sync")
@rate_limit(max_requests=10, window_seconds=60)
async def manual_phpipam_sync(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a manual phpIPAM host import. Returns JSON result."""
    if err := _require_admin(request):
        return err
    from integrations.phpipam import sync_phpipam_hosts
    from services import integration as _int_svc
    cfgs = (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.type == "phpipam")
    )).scalars().all()
    results = []
    for cfg in cfgs:
        config_dict = _int_svc.decrypt_config(cfg.config_json)
        result = await sync_phpipam_hosts(db, config_dict)
        results.append(result)
    return JSONResponse(results[0] if len(results) == 1 else {"synced": results})


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_settings(request: Request, db: AsyncSession = Depends(get_db)):
    # This is handled inline in the main settings page, just redirect
    return RedirectResponse(url="/settings?tab=notifications")


@router.post("/notifications/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_notifications(
    request: Request,
    notify_enabled:      str = Form("0"),
    notify_grace_minutes: str = Form("5"),
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
    db: AsyncSession = Depends(get_db),
):
    if err := _require_admin(request):
        return err
    await set_setting(db, "notify_enabled", "1" if notify_enabled == "on" else "0")
    try:
        grace_val = str(max(0, int(notify_grace_minutes.strip() or "5")))
    except (ValueError, TypeError):
        grace_val = "5"
    await set_setting(db, "notify_grace_minutes", grace_val)
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

    # Per-channel severity filters
    valid_severities = {"all", "warning", "error", "critical"}
    for key, val in [
        ("notify_telegram_min_severity", notify_telegram_min_severity),
        ("notify_discord_min_severity", notify_discord_min_severity),
        ("notify_webhook_min_severity", notify_webhook_min_severity),
        ("notify_email_min_severity", notify_email_min_severity),
    ]:
        await set_setting(db, key, val.strip() if val.strip() in valid_severities else "all")

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
    if err := _require_admin(request):
        return err
    body = await request.json()
    digest_enabled = "1" if body.get("digest_enabled") else "0"
    digest_day = str(max(0, min(6, int(body.get("digest_day", 0)))))
    digest_hour = str(max(0, min(23, int(body.get("digest_hour", 9)))))

    await set_setting(db, "digest_enabled", digest_enabled)
    await set_setting(db, "digest_day", digest_day)
    await set_setting(db, "digest_hour", digest_hour)

    # Reschedule the digest job
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
    if err := _require_admin(request):
        return err
    # Accept both JSON and form data
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
        await _log_notification(channel, "Test Notification", "Manual test", "info", "sent")
        return JSONResponse({"ok": True, "message": "Test notification sent"})
    except Exception as e:
        log.error("Test notification failed: %s", e)
        await _log_notification(channel, "Test Notification", "Manual test", "info", "failed", str(e))
        return JSONResponse({"ok": False, "message": "Notification failed. Check server logs."}, status_code=500)


# ── Notification History ─────────────────────────────────────────────────────

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


# ── API Keys (web UI management) ────────────────────────────────────────────


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    keys = result.scalars().all()
    return JSONResponse([
        {"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role,
         "enabled": k.enabled,
         "last_used": k.last_used.isoformat() if k.last_used else None,
         "created_at": k.created_at.isoformat() if k.created_at else None}
        for k in keys
    ])


@router.post("/api-keys/create")
@rate_limit(max_requests=10, window_seconds=60)
async def create_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    if err := _require_admin(request):
        return err
    body = await request.json()
    name = (body.get("name") or "").strip()
    role = body.get("role", "readonly")
    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)
    if role not in ("readonly", "editor", "admin"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    import hmac
    from config import SECRET_KEY
    raw_key = f"ng_{os.urandom(24).hex()}"
    prefix = raw_key[:8]
    key_hash = hmac.new(SECRET_KEY.encode(), raw_key.encode(), hashlib.sha256).hexdigest()

    user = getattr(request.state, "current_user", None)
    api_key = ApiKey(
        name=name, key_hash=key_hash, prefix=prefix, role=role,
        created_by=user.username if user else None,
    )
    db.add(api_key)
    await db.commit()
    return JSONResponse({"ok": True, "key": raw_key, "id": api_key.id, "prefix": prefix})


@router.post("/geoip/download")
@rate_limit(max_requests=10, window_seconds=60)
async def geoip_download(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a manual GeoLite2-City database download. Admin only."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    from database import decrypt_value
    from services.geoip_updater import download_geolite2

    key_enc = await get_setting(db, "geoip_license_key", "")
    if not key_enc:
        return JSONResponse({"success": False, "message": "No GeoIP license key configured"}, status_code=400)

    try:
        license_key = decrypt_value(key_enc)
    except Exception:
        license_key = key_enc

    result = await download_geolite2(license_key)

    if result["success"]:
        from datetime import datetime
        await set_setting(db, "geoip_last_updated", datetime.utcnow().isoformat())
        await db.commit()

    status_code = 200 if result["success"] else 500
    return JSONResponse(result, status_code=status_code)


@router.post("/ai/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_ai_settings(
    request: Request,
    claude_api_key: str = Form(""),
    daily_ai_summary_enabled: str = Form(""),
    daily_ai_summary_hour: str = Form(""),
    daily_ai_summary_channels: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Save Claude AI settings. Admin only."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    if claude_api_key.strip():
        await set_setting(db, "claude_api_key", encrypt_value(claude_api_key.strip()))

    # Daily AI summary settings
    if daily_ai_summary_enabled:
        await set_setting(db, "daily_ai_summary_enabled", "1" if daily_ai_summary_enabled == "on" else "0")
    if daily_ai_summary_hour:
        await set_setting(db, "daily_ai_summary_hour", daily_ai_summary_hour)
    if daily_ai_summary_channels is not None:
        await set_setting(db, "daily_ai_summary_channels", daily_ai_summary_channels)

    await db.commit()

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/ai/test-summary")
@rate_limit(max_requests=3, window_seconds=60)
async def test_daily_ai_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger a one-off daily AI summary (ignores schedule + duplicate protection)."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    from services.digest import (
        build_daily_summary_data, format_daily_summary_prompt,
        _DAILY_SUMMARY_SYSTEM_PROMPT,
    )
    from services.ai_client import generate_completion
    from notifications import (
        _send_telegram, _send_discord, _log_notification,
        _send_webhook, _send_email, _build_html_email,
    )
    from database import decrypt_value

    claude_key = await get_setting(db, "claude_api_key", "")
    if not claude_key:
        return JSONResponse({"ok": False, "message": "No Claude API key configured"}, status_code=400)

    # Collect data
    data = await build_daily_summary_data(db)

    # Generate AI analysis
    prompt = format_daily_summary_prompt(data)
    try:
        summary, usage = await generate_completion(
            _DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True,
        )
    except Exception as exc:
        log.error("Test AI summary generation failed: %s", exc)
        return JSONResponse({"ok": False, "message": f"AI generation failed: {exc}"}, status_code=500)

    # Log token usage
    try:
        from models.ai_usage import AiUsageLog
        _COST_PER_INPUT = 1.0 / 1_000_000
        _COST_PER_OUTPUT = 5.0 / 1_000_000
        cost = (usage["input_tokens"] * _COST_PER_INPUT
                + usage["output_tokens"] * _COST_PER_OUTPUT)
        db.add(AiUsageLog(
            feature="daily_summary_test",
            model=usage.get("model", "unknown"),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=round(cost, 6),
        ))
        await db.commit()
    except Exception as exc:
        log.warning("Failed to log AI usage: %s", exc)

    # Send to configured channels
    title = "Daily AI Summary (Test)"
    channels_csv = await get_setting(db, "daily_ai_summary_channels", "telegram,discord,webhook,email")
    selected = {c.strip() for c in channels_csv.split(",") if c.strip()}
    sent = False
    errors = []

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

    if sent:
        msg = "Test summary sent"
        if errors:
            msg += f" (some channels failed: {'; '.join(errors)})"
        return JSONResponse({"ok": True, "message": msg})
    elif errors:
        return JSONResponse({"ok": False, "message": f"All channels failed: {'; '.join(errors)}"}, status_code=500)
    else:
        return JSONResponse({"ok": False, "message": "No notification channels configured"}, status_code=400)


@router.get("/ai/usage")
async def ai_usage_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Return AI token usage statistics (current month + all-time)."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    from models.ai_usage import AiUsageLog
    from datetime import datetime

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Monthly stats
    monthly = (await db.execute(
        select(
            func.coalesce(func.sum(AiUsageLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AiUsageLog.cost_usd), 0).label("cost_usd"),
            func.count().label("calls"),
        ).where(AiUsageLog.timestamp >= month_start)
    )).one()

    # All-time stats
    total = (await db.execute(
        select(
            func.coalesce(func.sum(AiUsageLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AiUsageLog.cost_usd), 0).label("cost_usd"),
            func.count().label("calls"),
        )
    )).one()

    return JSONResponse({
        "monthly": {
            "input_tokens": monthly.input_tokens,
            "output_tokens": monthly.output_tokens,
            "cost_usd": round(float(monthly.cost_usd), 4),
            "calls": monthly.calls,
            "month": now.strftime("%Y-%m"),
        },
        "total": {
            "input_tokens": total.input_tokens,
            "output_tokens": total.output_tokens,
            "cost_usd": round(float(total.cost_usd), 4),
            "calls": total.calls,
        },
    })


@router.delete("/api-keys/{key_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def delete_api_key(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := _require_admin(request):
        return err
    key = await db.get(ApiKey, key_id)
    if not key:
        return JSONResponse({"error": "Not found"}, status_code=404)
    await db.delete(key)
    await db.commit()
    return JSONResponse({"ok": True})


# ── LDAP Settings ────────────────────────────────────────────────────────────

@router.post("/ldap/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_ldap_settings(
    request: Request,
    ldap_enabled:      str = Form("0"),
    ldap_server:       str = Form(""),
    ldap_bind_dn:      str = Form(""),
    ldap_bind_password: str = Form(""),
    ldap_base_dn:      str = Form(""),
    ldap_user_filter:  str = Form(""),
    ldap_display_attr: str = Form("displayName"),
    ldap_group_attr:   str = Form("memberOf"),
    ldap_admin_group:  str = Form(""),
    ldap_editor_group: str = Form(""),
    ldap_use_ssl:      str = Form("0"),
    ldap_start_tls:    str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    """Save LDAP configuration. Admin only."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    await set_setting(db, "ldap_enabled", "1" if ldap_enabled in ("1", "true") else "0")
    await set_setting(db, "ldap_server", ldap_server.strip())
    await set_setting(db, "ldap_bind_dn", ldap_bind_dn.strip())
    if ldap_bind_password.strip():
        await set_setting(db, "ldap_bind_password", encrypt_value(ldap_bind_password.strip()))
    await set_setting(db, "ldap_base_dn", ldap_base_dn.strip())
    await set_setting(db, "ldap_user_filter", ldap_user_filter.strip() or
                      "(&(objectClass=person)(sAMAccountName={username}))")
    await set_setting(db, "ldap_display_attr", ldap_display_attr.strip() or "displayName")
    await set_setting(db, "ldap_group_attr", ldap_group_attr.strip() or "memberOf")
    await set_setting(db, "ldap_admin_group", ldap_admin_group.strip())
    await set_setting(db, "ldap_editor_group", ldap_editor_group.strip())
    await set_setting(db, "ldap_use_ssl", "1" if ldap_use_ssl in ("1", "true") else "0")
    await set_setting(db, "ldap_start_tls", "1" if ldap_start_tls in ("1", "true") else "0")
    await db.commit()

    await log_action(db, request, "settings.update", "setting", target_name="ldap")
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/ldap/test")
@rate_limit(max_requests=10, window_seconds=60)
async def test_ldap(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Test LDAP connection with currently saved settings. Admin only."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    from routers.auth import _get_ldap_config
    from services.ldap_auth import test_ldap_connection
    ldap_cfg = await _get_ldap_config(db)
    if not ldap_cfg or not ldap_cfg.server:
        return JSONResponse({"ok": False, "error": "LDAP not configured or not enabled"})

    result = await test_ldap_connection(ldap_cfg)
    return JSONResponse(result)

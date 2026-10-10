"""Notification senders for Nodeglow – Telegram, Discord, Webhook, Email.

Microsoft Teams, Slack and ntfy live in ``notification_channels.py``; the
dispatcher below pulls their send coroutines in from there.

Features:
  - Multi-channel delivery (fire-and-forget, non-blocking)
  - Rate limiting / cooldown to prevent alert fatigue
  - Notification history persisted to DB
  - HTML email with styled template
"""
import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape

import httpx

logger = logging.getLogger(__name__)


def _is_safe_url(url: str) -> bool:
    """Validate URL is not targeting internal/private resources (SSRF protection).

    NOTE: Private IPs (10.x, 192.168.x, 172.16.x) are intentionally allowed
    because this is a homelab monitoring tool where webhooks to local services
    are legitimate use cases.

    Resolves the name and rejects loopback, link-local, 0.0.0.0, multicast,
    cloud metadata and our own compose service names (see utils.net_safety).
    May block briefly on DNS. Send paths additionally use
    :func:`_webhook_client`, which re-checks the address at connect time
    (DNS rebinding) and never follows redirects.
    """
    from utils.net_safety import is_safe_url
    return is_safe_url(url)


def _webhook_client(timeout: float = 10) -> httpx.AsyncClient:
    """HTTP client for user-configured webhook URLs: connect-time SSRF guard,
    no redirects (a 30x to an internal address is not followed)."""
    from utils.net_safety import safe_async_client
    return safe_async_client(timeout=timeout, follow_redirects=False)


async def _url_is_safe(url: str) -> bool:
    """Non-blocking :func:`_is_safe_url` for async send paths."""
    return await asyncio.to_thread(_is_safe_url, url)


# ── Rate Limiting ─────────────────────────────────────────────────────────────
# In-memory cooldown: same title won't fire again within cooldown window
_recent: dict[str, float] = {}
_COOLDOWN_SECONDS = 300  # 5 minutes


def _is_rate_limited(title: str) -> bool:
    """Check if this notification was sent recently (prevents alert storms)."""
    now = time.monotonic()
    # Cleanup old entries
    stale = [k for k, v in _recent.items() if now - v > _COOLDOWN_SECONDS * 2]
    for k in stale:
        del _recent[k]
    key = title.strip().lower()
    if key in _recent and now - _recent[key] < _COOLDOWN_SECONDS:
        return True
    _recent[key] = now
    return False


# ── History Logging ───────────────────────────────────────────────────────────

async def _log_notification(channel: str, title: str, message: str,
                            severity: str, status: str = "sent",
                            error: str | None = None) -> None:
    """Persist notification to DB for audit trail."""
    try:
        from database import AsyncSessionLocal
        from models.notification import NotificationLog
        async with AsyncSessionLocal() as db:
            db.add(NotificationLog(
                channel=channel, title=title, message=message,
                severity=severity, status=status, error=error,
            ))
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to log notification: %s", exc)


# ── Channel Senders ───────────────────────────────────────────────────────────

async def _send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        resp.raise_for_status()


async def _send_discord(webhook_url: str, title: str, message: str, color: int = 0xe74c3c) -> None:
    if not await _url_is_safe(webhook_url):
        logger.warning("Blocked webhook to unsafe URL: %s", webhook_url)
        return
    payload = {"embeds": [{"title": title, "description": message, "color": color}]}
    async with _webhook_client() as client:
        for attempt in range(4):  # initial + 3 retries
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 1.0 * (2 ** attempt))
                logger.warning("Discord 429 – retrying in %.1fs (attempt %d)", retry_after, attempt + 1)
                await asyncio.sleep(min(retry_after, 10))
                continue
            resp.raise_for_status()
            return
        resp.raise_for_status()  # final attempt failed – raise


async def _send_webhook(url: str, secret: str, title: str, message: str,
                        severity: str = "critical") -> None:
    if not await _url_is_safe(url):
        logger.warning("Blocked webhook to unsafe URL: %s", url)
        return
    payload = {"title": title, "message": message, "severity": severity,
               "timestamp": int(time.time()), "source": "nodeglow"}
    body = json.dumps(payload, separators=(",", ":"))
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Nodeglow-Signature"] = f"sha256={sig}"
    async with _webhook_client() as client:
        resp = await client.post(url, content=body, headers=headers)
        resp.raise_for_status()


def _build_html_email(title: str, message: str, severity: str) -> str:
    """Build a styled HTML email body."""
    title = html_escape(title)
    message = html_escape(message)
    colors = {
        "critical": ("#FB7185", "#1a0a0e"),
        "warning":  ("#FBBF24", "#1a1408"),
        "info":     ("#38BDF8", "#0a1628"),
    }
    accent, bg_tint = colors.get(severity, colors["info"])
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:0 auto;padding:0">
  <div style="background:#0B1120;border-radius:12px;overflow:hidden;border:1px solid #1E293B">
    <div style="padding:24px 28px;border-bottom:1px solid #1E293B;background:{bg_tint}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{accent}"></span>
        <span style="color:{accent};font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:2px">{severity}</span>
      </div>
      <h1 style="color:#E2E8F0;font-size:18px;font-weight:600;margin:0;line-height:1.4">{title}</h1>
    </div>
    <div style="padding:20px 28px">
      <p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0;white-space:pre-line">{message}</p>
    </div>
    <div style="padding:16px 28px;border-top:1px solid #1E293B;text-align:center">
      <span style="color:#475569;font-size:11px;letter-spacing:3px;font-weight:500">NODEGLOW</span>
    </div>
  </div>
</div>"""


async def _send_email(host: str, port: int, user: str, password: str,
                      from_addr: str, to_addr: str, subject: str,
                      body_text: str, body_html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    loop = asyncio.get_event_loop()

    def _send():
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx) as s:
                s.login(user, password)
                s.sendmail(from_addr, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host, port) as s:
                s.starttls(context=ctx)
                s.login(user, password)
                s.sendmail(from_addr, [to_addr], msg.as_string())
    await loop.run_in_executor(None, _send)


# ── Severity Filtering ────────────────────────────────────────────────────────

# Numeric severity levels (lower = more severe, syslog-style)
_SEVERITY_LEVELS: dict[str, int] = {
    "critical": 2,
    "error": 3,
    "warning": 4,
    "info": 7,
    "all": 7,
}


def _severity_passes(incident_severity: str, min_severity: str) -> bool:
    """Check if an incident severity meets the minimum threshold for a channel.

    Lower numeric value = more severe. A channel set to 'warning' (4) will
    accept critical(2), error(3), and warning(4) but reject info(7).
    """
    if not min_severity or min_severity == "all":
        return True
    threshold = _SEVERITY_LEVELS.get(min_severity, 7)
    actual = _SEVERITY_LEVELS.get(incident_severity, 7)
    return actual <= threshold


# ── Public API ────────────────────────────────────────────────────────────────

async def notify(title: str, message: str, severity: str = "critical",
                 channels: list[str] | None = None,
                 link_path: str | None = None) -> None:
    """Send notification to configured channels. If channels is given, only send to those.

    ``link_path`` (e.g. ``/incidents/42``) becomes a link in channels that
    support one, when the ``public_url`` setting is set.
    """
    # Rate limit check
    if _is_rate_limited(title):
        logger.debug("Notification rate-limited: %s", title)
        return

    from database import AsyncSessionLocal, decrypt_value, get_setting
    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "notify_enabled", "0")
        if enabled != "1":
            return

        tg_token    = await get_setting(db, "telegram_bot_token", "")
        tg_chat     = await get_setting(db, "telegram_chat_id", "")
        dc_webhook  = await get_setting(db, "discord_webhook_url", "")
        wh_url      = await get_setting(db, "webhook_url", "")
        wh_secret   = await get_setting(db, "webhook_secret", "")
        smtp_host   = await get_setting(db, "smtp_host", "")
        smtp_port   = await get_setting(db, "smtp_port", "587")
        smtp_user   = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_from   = await get_setting(db, "smtp_from", "")
        smtp_to     = await get_setting(db, "smtp_to", "")

        # Per-channel minimum severity filters
        min_sev_telegram = await get_setting(db, "notify_telegram_min_severity", "all")
        min_sev_discord  = await get_setting(db, "notify_discord_min_severity", "all")
        min_sev_webhook  = await get_setting(db, "notify_webhook_min_severity", "all")
        min_sev_email    = await get_setting(db, "notify_email_min_severity", "all")

        from notification_channels import load_config as _load_extra_channels
        extra_cfg = await _load_extra_channels(db, get_setting, decrypt_value)

    channels_list = []  # (name, coroutine) pairs
    color = {"critical": 0xe74c3c, "warning": 0xf39c12, "info": 0x2ecc71}.get(severity, 0x2ecc71)

    if tg_token and tg_chat and _severity_passes(severity, min_sev_telegram):
        tg_text = f"<b>{html_escape(title)}</b>\n{html_escape(message)}"
        channels_list.append(("telegram", _send_telegram(tg_token, tg_chat, tg_text)))
    if dc_webhook and _severity_passes(severity, min_sev_discord):
        channels_list.append(("discord", _send_discord(dc_webhook, title, message, color)))
    if wh_url and _severity_passes(severity, min_sev_webhook):
        channels_list.append(("webhook", _send_webhook(wh_url, wh_secret, title, message, severity)))
    if smtp_host and smtp_user and smtp_pw_enc and smtp_to and _severity_passes(severity, min_sev_email):
        try:
            smtp_pw = decrypt_value(smtp_pw_enc)
        except Exception:
            smtp_pw = smtp_pw_enc
        html_body = _build_html_email(title, message, severity)
        channels_list.append(("email", _send_email(
            smtp_host, int(smtp_port), smtp_user, smtp_pw,
            smtp_from or smtp_user, smtp_to,
            f"[Nodeglow] {title}", f"{title}\n{message}", html_body,
        )))

    from notification_channels import build_sends as _extra_channel_sends
    channels_list.extend(_extra_channel_sends(
        extra_cfg, title, message, severity, link_path=link_path, channels=channels,
    ))

    if not channels_list:
        return

    # Filter to requested channels if specified
    if channels:
        for name, coro in channels_list:
            if name not in channels:
                coro.close()  # never awaited: avoid "coroutine was never awaited"
        channels_list = [(name, coro) for name, coro in channels_list if name in channels]
    if not channels_list:
        return

    results = await asyncio.gather(*[coro for _, coro in channels_list], return_exceptions=True)

    # Log results to DB
    for (ch_name, _), result in zip(channels_list, results):
        if isinstance(result, Exception):
            logger.warning("Notification %s failed: %s", ch_name, result)
            await _log_notification(ch_name, title, message, severity, "failed", str(result))
        else:
            await _log_notification(ch_name, title, message, severity, "sent")

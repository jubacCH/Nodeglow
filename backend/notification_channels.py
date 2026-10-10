"""Additional notification channels: Microsoft Teams, Slack, ntfy.

Kept apart from ``notifications.py`` so the dispatcher there only has to ask
this module for its configuration and its send coroutines. Each channel has:

  - a pure payload builder (golden-tested),
  - an async sender with a timeout that raises ``ChannelError`` on failure.
    Error text never contains the target URL: webhook URLs for Teams and
    Slack are bearer credentials and the error ends up in notification_logs.

URL safety reuses ``notifications._is_safe_url`` (looked up at call time, so
hardening there applies here too).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import httpx

import notifications as _notifications

logger = logging.getLogger(__name__)

CHANNELS: tuple[str, ...] = ("teams", "slack", "ntfy")
# Every channel the daily AI summary can go to; also its default selection.
DAILY_SUMMARY_DEFAULT_CHANNELS = "telegram,discord,webhook,email,teams,slack,ntfy"

HTTP_TIMEOUT = 10.0
_MAX_RETRIES = 2          # extra attempts after a 429
_MAX_RETRY_WAIT = 10.0    # seconds

DEFAULT_NTFY_SERVER = "https://ntfy.sh"
# ntfy's own topic rule: 1-64 chars of letters, digits, '-' and '_'.
NTFY_TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")


class ChannelError(Exception):
    """A channel delivery failed. The message is safe to store and show."""


# ── Severity presentation ────────────────────────────────────────────────────

_SEVERITY_STYLE: dict[str, dict[str, Any]] = {
    # container style / text colour are Adaptive Card enum values
    "critical": {"emoji": "\U0001F534", "label": "CRITICAL", "container": "attention",
                 "color": "Attention", "ntfy_priority": 5, "ntfy_tag": "rotating_light"},
    "error":    {"emoji": "\U0001F7E0", "label": "ERROR", "container": "attention",
                 "color": "Attention", "ntfy_priority": 4, "ntfy_tag": "x"},
    "warning":  {"emoji": "\U0001F7E1", "label": "WARNING", "container": "warning",
                 "color": "Warning", "ntfy_priority": 4, "ntfy_tag": "warning"},
    "info":     {"emoji": "\U0001F535", "label": "INFO", "container": "accent",
                 "color": "Accent", "ntfy_priority": 3, "ntfy_tag": "information_source"},
}


def severity_style(severity: str) -> dict[str, Any]:
    return _SEVERITY_STYLE.get((severity or "").lower(), _SEVERITY_STYLE["info"])


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_link(public_url: str, link_path: str | None) -> str | None:
    """Absolute link into the Nodeglow UI, or None without a public URL."""
    base = (public_url or "").strip().rstrip("/")
    if not base:
        return None
    if not link_path:
        return base
    return f"{base}/{link_path.lstrip('/')}"


# ── Payload builders ─────────────────────────────────────────────────────────

def build_teams_payload(title: str, message: str, severity: str,
                        link: str | None = None) -> dict:
    """Adaptive Card 1.4 wrapped in the message envelope Teams Workflows accept."""
    style = severity_style(severity)
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": [
            {
                "type": "Container",
                "style": style["container"],
                "bleed": True,
                "items": [{
                    "type": "TextBlock",
                    "text": f"{style['emoji']} {style['label']}",
                    "weight": "Bolder",
                    "size": "Small",
                    "color": style["color"],
                }],
            },
            {
                "type": "TextBlock",
                "text": _truncate(title, 400),
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                # Teams' markdown needs a blank line for a visible line break.
                "text": re.sub(r"\n+", "\n\n", _truncate(message, 3000)),
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Nodeglow",
                "isSubtle": True,
                "size": "Small",
                "spacing": "Medium",
            },
        ],
    }
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Nodeglow", "url": link}]
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": card,
        }],
    }


def _slack_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_payload(title: str, message: str, severity: str,
                        link: str | None = None) -> dict:
    """Block Kit message (header, section, context) with a plain-text fallback."""
    style = severity_style(severity)
    fallback = f"{style['emoji']} [{style['label']}] {title}: {message}"
    blocks: list[dict[str, Any]] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": _truncate(title, 150), "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn", "text": _truncate(_slack_escape(message), 3000) or " "}},
    ]
    if link:
        blocks.append({"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Nodeglow", "emoji": True},
            "url": link,
        }]})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"{style['emoji']} *{style['label']}* · Nodeglow"},
    ]})
    return {"text": _truncate(_slack_escape(fallback), 3000), "blocks": blocks}


def build_ntfy_payload(topic: str, title: str, message: str, severity: str,
                       link: str | None = None) -> dict:
    """JSON body for publishing to the ntfy server root.

    JSON (rather than Title/Priority headers) keeps non-Latin-1 titles such
    as the emoji in incident titles intact.
    """
    style = severity_style(severity)
    tag = style["ntfy_tag"]
    if "resolved" in (title or "").lower():
        tag = "white_check_mark"
    payload: dict[str, Any] = {
        "topic": topic,
        "title": _truncate(title, 250),
        "message": _truncate(message, 4000) or title,
        "priority": style["ntfy_priority"],
        "tags": [tag, (severity or "info").lower(), "nodeglow"],
    }
    if link:
        payload["click"] = link
    return payload


def ntfy_auth_header(token: str) -> dict[str, str]:
    """Access token -> Bearer; ``user:password`` -> Basic."""
    token = (token or "").strip()
    if not token:
        return {}
    if ":" in token and not token.startswith("tk_"):
        return {"Authorization": "Basic " + base64.b64encode(token.encode()).decode()}
    return {"Authorization": f"Bearer {token}"}


# ── Transport ────────────────────────────────────────────────────────────────

def _redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


async def _post_json(url: str, payload: dict, *, headers: dict | None = None,
                     transport: httpx.AsyncBaseTransport | None = None,
                     label: str) -> None:
    if not _notifications._is_safe_url(url):
        raise ChannelError(f"{label}: URL rejected by safety check")
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, transport=transport) as client:
            for attempt in range(_MAX_RETRIES + 1):
                resp = await client.post(url, json=payload, headers=headers or {})
                if resp.status_code == 429 and attempt < _MAX_RETRIES:
                    try:
                        wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                    except ValueError:
                        wait = float(2 ** attempt)
                    logger.warning("%s 429 - retrying in %.1fs", label, wait)
                    await asyncio.sleep(min(max(wait, 0.0), _MAX_RETRY_WAIT))
                    continue
                if resp.status_code >= 400:
                    body = _redact(resp.text[:200].strip(), url)
                    raise ChannelError(f"{label}: HTTP {resp.status_code}" + (f" {body}" if body else ""))
                return
    except ChannelError:
        raise
    except httpx.TimeoutException as exc:
        raise ChannelError(f"{label}: timed out after {HTTP_TIMEOUT:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise ChannelError(f"{label}: {type(exc).__name__}: {_redact(str(exc), url)}") from exc


async def send_teams(webhook_url: str, title: str, message: str, severity: str,
                     link: str | None = None, *,
                     transport: httpx.AsyncBaseTransport | None = None) -> None:
    await _post_json(webhook_url, build_teams_payload(title, message, severity, link),
                     transport=transport, label="Teams")


async def send_slack(webhook_url: str, title: str, message: str, severity: str,
                     link: str | None = None, *,
                     transport: httpx.AsyncBaseTransport | None = None) -> None:
    await _post_json(webhook_url, build_slack_payload(title, message, severity, link),
                     transport=transport, label="Slack")


async def send_ntfy(server_url: str, topic: str, token: str, title: str, message: str,
                    severity: str, link: str | None = None, *,
                    transport: httpx.AsyncBaseTransport | None = None) -> None:
    if not NTFY_TOPIC_RE.match(topic or ""):
        raise ChannelError("ntfy: invalid topic")
    server = (server_url or DEFAULT_NTFY_SERVER).strip().rstrip("/")
    headers = ntfy_auth_header(token)
    try:
        await _post_json(server, build_ntfy_payload(topic, title, message, severity, link),
                         headers=headers, transport=transport, label="ntfy")
    except ChannelError as exc:
        raise ChannelError(_redact(str(exc), (token or "").strip())) from exc


# ── Configuration + dispatch ─────────────────────────────────────────────────

@dataclass
class ExtraChannelConfig:
    public_url: str = ""
    teams_enabled: bool = False
    teams_webhook_url: str = ""
    teams_min_severity: str = "all"
    slack_enabled: bool = False
    slack_webhook_url: str = ""
    slack_min_severity: str = "all"
    ntfy_enabled: bool = False
    ntfy_server_url: str = DEFAULT_NTFY_SERVER
    ntfy_topic: str = ""
    ntfy_token: str = ""
    ntfy_min_severity: str = "all"

    def configured(self, channel: str) -> bool:
        if channel == "teams":
            return bool(self.teams_webhook_url)
        if channel == "slack":
            return bool(self.slack_webhook_url)
        if channel == "ntfy":
            return bool(self.ntfy_topic)
        return False


def reveal(value: str, decrypt: Callable[[str], str]) -> str:
    """Decrypt a value stored via encrypt_value; tolerate legacy plaintext."""
    if not value:
        return ""
    try:
        return decrypt(value)
    except Exception:
        return value


async def load_config(db, get_setting: Callable[..., Awaitable[Any]],
                      decrypt: Callable[[str], str]) -> ExtraChannelConfig:
    async def g(key: str, default: str = "") -> str:
        return (await get_setting(db, key, default)) or default

    return ExtraChannelConfig(
        public_url=(await g("public_url")).strip().rstrip("/"),
        teams_enabled=await g("teams_enabled", "0") == "1",
        teams_webhook_url=reveal(await g("teams_webhook_url"), decrypt),
        teams_min_severity=await g("notify_teams_min_severity", "all"),
        slack_enabled=await g("slack_enabled", "0") == "1",
        slack_webhook_url=reveal(await g("slack_webhook_url"), decrypt),
        slack_min_severity=await g("notify_slack_min_severity", "all"),
        ntfy_enabled=await g("ntfy_enabled", "0") == "1",
        ntfy_server_url=(await g("ntfy_server_url", DEFAULT_NTFY_SERVER)).strip() or DEFAULT_NTFY_SERVER,
        ntfy_topic=(await g("ntfy_topic")).strip(),
        ntfy_token=reveal(await g("ntfy_token"), decrypt),
        ntfy_min_severity=await g("notify_ntfy_min_severity", "all"),
    )


def make_send(cfg: ExtraChannelConfig, channel: str, title: str, message: str,
              severity: str, link: str | None) -> Coroutine[Any, Any, None]:
    """Coroutine delivering to one channel, regardless of enabled/severity."""
    if not cfg.configured(channel):
        raise ChannelError(f"{channel}: not configured")
    if channel == "teams":
        return send_teams(cfg.teams_webhook_url, title, message, severity, link)
    if channel == "slack":
        return send_slack(cfg.slack_webhook_url, title, message, severity, link)
    if channel == "ntfy":
        return send_ntfy(cfg.ntfy_server_url, cfg.ntfy_topic, cfg.ntfy_token,
                         title, message, severity, link)
    raise ChannelError(f"unknown channel {channel!r}")


async def send_to_selected(cfg: ExtraChannelConfig, selected: set[str] | list[str],
                           title: str, message: str, severity: str = "info",
                           link_path: str | None = None,
                           ) -> list[tuple[str, Exception | None]]:
    """Deliver to each enabled, configured Teams/Slack/ntfy channel in
    ``selected`` (used by the daily AI summary). Returns (channel, error)."""
    link = build_link(cfg.public_url, link_path)
    results: list[tuple[str, Exception | None]] = []
    for name in CHANNELS:
        if name not in selected or not getattr(cfg, f"{name}_enabled") or not cfg.configured(name):
            continue
        try:
            await make_send(cfg, name, title, message, severity, link)
            results.append((name, None))
        except Exception as exc:
            results.append((name, exc))
    return results


def build_sends(cfg: ExtraChannelConfig, title: str, message: str, severity: str, *,
                link_path: str | None = None,
                channels: list[str] | None = None) -> list[tuple[str, Coroutine]]:
    """(name, coroutine) pairs for every enabled, configured channel that the
    severity filter and the optional per-rule channel selection let through."""
    link = build_link(cfg.public_url, link_path)
    sends: list[tuple[str, Coroutine]] = []
    for name in CHANNELS:
        if channels and name not in channels:
            continue
        if not getattr(cfg, f"{name}_enabled") or not cfg.configured(name):
            continue
        if not _notifications._severity_passes(severity, getattr(cfg, f"{name}_min_severity")):
            continue
        sends.append((name, make_send(cfg, name, title, message, severity, link)))
    return sends

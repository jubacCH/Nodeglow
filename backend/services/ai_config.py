"""AI feature configuration: opt-in, provider, redaction.

All AI settings are read through :func:`load_ai_config`. Today they are
installation-wide rows in the ``settings`` table; when multi-tenancy arrives
the scope moves here (e.g. ``load_ai_config(db, tenant_id)``) and every AI
entry point follows automatically, because none of them reads the keys
directly.

Settings keys (all strings, like every other setting):

=========================  ====================================================
``ai_enabled``             "1" = AI features on. Absent/"0" = off (default).
``ai_enabled_by``          who switched it on last (username / "system: ...").
``ai_enabled_at``          when (ISO, UTC).
``ai_provider``            "anthropic" (default) | "openai_compatible"
``claude_api_key``         Anthropic key, encrypt_value()'d (pre-existing key).
``ai_anthropic_model``     model id, default :data:`DEFAULT_ANTHROPIC_MODEL`.
``ai_openai_base_url``     e.g. https://x.openai.azure.com, http://10.0.0.5:11434/v1
``ai_openai_api_key``      encrypt_value()'d; optional for local servers.
``ai_openai_model``        model name, or the deployment name on Azure.
``ai_openai_api_version``  Azure only, e.g. 2024-10-21. Set = Azure URL scheme.
``ai_redact_enabled``      "1" (default) = redact personal data before sending.
``ai_redact_hostnames``    "1" = also replace hostnames/FQDNs. Default "0".
``ai_restore_placeholders`` "1" (default) = map placeholders back in answers.
``ai_optin_migrated``      marker: the one-time upgrade check has run.
=========================  ====================================================
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("nodeglow.ai")

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # fast + cheap for monitoring queries

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai_compatible"
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_OPENAI)
PROVIDER_LABELS = {
    PROVIDER_ANTHROPIC: "Anthropic (Claude)",
    PROVIDER_OPENAI: "OpenAI-compatible endpoint",
}

AI_DISABLED_MESSAGE = (
    "AI features are disabled. An admin can enable them under Settings > AI."
)
AI_NOT_CONFIGURED_MESSAGE = (
    "AI is enabled but the provider is not configured. An admin can finish the "
    "setup under Settings > AI."
)

MIGRATION_MARKER = "ai_optin_migrated"


class AIError(RuntimeError):
    code = "ai_error"


class AIDisabledError(AIError):
    code = "ai_disabled"

    def __init__(self, message: str = AI_DISABLED_MESSAGE):
        super().__init__(message)


class AINotConfiguredError(AIError):
    code = "ai_not_configured"

    def __init__(self, message: str = AI_NOT_CONFIGURED_MESSAGE):
        super().__init__(message)


class AIUnsupportedError(AIError):
    """The selected provider cannot do what the feature needs."""
    code = "ai_unsupported"


@dataclass
class AIConfig:
    enabled: bool = False
    provider: str = PROVIDER_ANTHROPIC
    anthropic_api_key: str = ""
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_api_version: str = ""
    redact: bool = True
    redact_hostnames: bool = False
    restore_placeholders: bool = True
    enabled_by: str = ""
    enabled_at: str = ""

    @property
    def is_azure(self) -> bool:
        return self.provider == PROVIDER_OPENAI and bool(self.openai_api_version)

    @property
    def model(self) -> str:
        return self.anthropic_model if self.provider == PROVIDER_ANTHROPIC else self.openai_model

    @property
    def provider_label(self) -> str:
        if self.is_azure:
            return "Azure OpenAI"
        return PROVIDER_LABELS.get(self.provider, self.provider)

    def missing(self) -> list[str]:
        """What still has to be filled in before the provider can be used."""
        if self.provider == PROVIDER_ANTHROPIC:
            return [] if self.anthropic_api_key else ["claude_api_key"]
        out = []
        if not self.openai_base_url:
            out.append("ai_openai_base_url")
        if not self.openai_model:
            out.append("ai_openai_model")
        if self.is_azure and not self.openai_api_key:
            out.append("ai_openai_api_key")
        return out

    @property
    def configured(self) -> bool:
        return not self.missing()

    def destination(self) -> str:
        """Human readable "where does the data go" for notices and docs."""
        if self.provider == PROVIDER_ANTHROPIC:
            return "Anthropic API (api.anthropic.com)"
        host = urlparse(self.openai_base_url).hostname or self.openai_base_url or "?"
        return f"{self.provider_label} at {host}"


def _decrypt(raw: str) -> str:
    if not raw:
        return ""
    from database import decrypt_value
    try:
        return decrypt_value(raw)
    except Exception:
        return raw  # stored unencrypted (or already decrypted by get_setting)


async def load_ai_config(db: AsyncSession) -> AIConfig:
    from database import get_setting

    async def s(key: str, default: str = "") -> str:
        v = await get_setting(db, key, default)
        return default if v is None else str(v)

    provider = await s("ai_provider", PROVIDER_ANTHROPIC) or PROVIDER_ANTHROPIC
    if provider not in PROVIDERS:
        provider = PROVIDER_ANTHROPIC
    return AIConfig(
        enabled=(await s("ai_enabled", "0")) == "1",
        provider=provider,
        anthropic_api_key=_decrypt(await s("claude_api_key")),
        anthropic_model=(await s("ai_anthropic_model")).strip() or DEFAULT_ANTHROPIC_MODEL,
        openai_base_url=(await s("ai_openai_base_url")).strip(),
        openai_api_key=_decrypt(await s("ai_openai_api_key")),
        openai_model=(await s("ai_openai_model")).strip(),
        openai_api_version=(await s("ai_openai_api_version")).strip(),
        redact=(await s("ai_redact_enabled", "1")) != "0",
        redact_hostnames=(await s("ai_redact_hostnames", "0")) == "1",
        restore_placeholders=(await s("ai_restore_placeholders", "1")) != "0",
        enabled_by=await s("ai_enabled_by"),
        enabled_at=await s("ai_enabled_at"),
    )


async def get_ai_config() -> AIConfig:
    """Load the config with a short-lived session of its own."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await load_ai_config(db)


async def is_ai_enabled(db: AsyncSession) -> bool:
    from database import get_setting
    return (await get_setting(db, "ai_enabled", "0")) == "1"


# ── Endpoint validation ──────────────────────────────────────────────────────

def validate_ai_base_url(url: str, resolve: bool = True) -> str | None:
    """Return an error message if ``url`` must not be used as an LLM endpoint.

    Deliberately MORE permissive than the integration SSRF check: loopback
    (``localhost``, 127.0.0.1, ::1) and RFC1918/ULA ranges are ALLOWED,
    because a local LLM (Ollama, vLLM, LM Studio) on the same host or LAN is
    a primary use case, and only an admin can set this URL. What stays
    blocked are targets that are never an LLM but are dangerous to hand our
    API key and prompts to: cloud metadata services, link-local, multicast,
    0.0.0.0 and Nodeglow's own compose services (db, clickhouse, updater...).
    Redirects are not followed by the client, so a 3xx cannot bounce the
    request elsewhere. A name rebound after validation is not caught here.
    """
    from utils.net_safety import (
        INTERNAL_HOSTS as _INTERNAL_HOSTS,
        METADATA_HOSTS as _METADATA_HOSTS,
        METADATA_IPS as _METADATA_IPS,
        _ZERO_NET,
        resolve_all as _resolve_all,
    )

    if not url:
        return "Base URL is required"
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return "Base URL is not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return "Base URL must start with http:// or https://"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return "Base URL has no host"
    if parsed.username or parsed.password:
        return "Put credentials in the API key field, not in the URL"

    loopback_names = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
    if host in _INTERNAL_HOSTS and host not in loopback_names:
        return "Nodeglow's own services cannot be used as an AI endpoint"
    if host in _METADATA_HOSTS:
        return "Cloud metadata endpoints are not allowed"

    def reason(addr) -> str | None:
        if getattr(addr, "ipv4_mapped", None) is not None:
            addr = addr.ipv4_mapped
        if addr in _METADATA_IPS:
            return "Cloud metadata endpoints are not allowed"
        if addr.is_unspecified or (addr.version == 4 and addr in _ZERO_NET):
            return "Unspecified addresses (0.0.0.0) are not allowed"
        if addr.is_link_local:
            return "Link-local addresses are not allowed (cloud metadata risk)"
        if addr.is_multicast:
            return "Multicast addresses are not allowed"
        return None  # loopback and private ranges are fine here (see docstring)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return reason(literal)
    if resolve and host not in loopback_names:
        for addr in _resolve_all(host):
            r = reason(addr)
            if r:
                return f"{r} ({host} resolves to {addr})"
    return None


async def validate_ai_base_url_async(url: str) -> str | None:
    import asyncio
    return await asyncio.to_thread(validate_ai_base_url, url)


# ── One-time upgrade compatibility ───────────────────────────────────────────

async def migrate_ai_opt_in(session_factory=None) -> bool:
    """Run once per installation: keep AI working where it already worked.

    Before the opt-in existed, configuring a Claude API key was the opt-in.
    An installation that has a key when this first runs therefore gets
    ``ai_enabled=1`` (recorded in the audit log as done by the system), so an
    upgrade never silently switches AI off in production. New installations
    have no key at that point and stay off until an admin enables AI.

    The marker row is inserted in the same transaction; its primary key makes
    the check race-free when several workers start at once. Returns True if
    AI was enabled by this call.
    """
    from database import Setting
    from services.audit import log_action

    if session_factory is None:
        from database import AsyncSessionLocal as session_factory

    async with session_factory() as db:
        if await db.get(Setting, MIGRATION_MARKER) is not None:
            return False
        key_row = await db.get(Setting, "claude_api_key")
        enabled_row = await db.get(Setting, "ai_enabled")
        now = datetime.utcnow().isoformat(timespec="seconds")
        db.add(Setting(key=MIGRATION_MARKER, value=now))
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return False  # another worker got there first

        enabled = False
        if key_row is not None and key_row.value and enabled_row is None:
            who = "system: upgrade (existing Claude API key)"
            for k, v in (
                ("ai_enabled", "1"), ("ai_enabled_by", who), ("ai_enabled_at", now),
                ("ai_provider", PROVIDER_ANTHROPIC),
            ):
                row = await db.get(Setting, k)
                if row is None:
                    db.add(Setting(key=k, value=v))
                else:
                    row.value = v
            await log_action(
                db, None, "settings.ai_enabled", "setting", None, "ai_enabled",
                {"by": who, "provider": PROVIDER_ANTHROPIC,
                 "reason": "A Claude API key was configured before AI became opt-in; "
                           "enabled once so existing AI features keep working."},
            )
            enabled = True
        await db.commit()

    if enabled:
        log.warning(
            "AI opt-in: a Claude API key was already configured, so ai_enabled was "
            "switched on once to keep Glow, postmortems and the daily summary "
            "working after the upgrade. Disable it under Settings > AI if unwanted."
        )
    else:
        log.info("AI opt-in: upgrade check done, AI features stay %s",
                 "on" if enabled_row is not None and enabled_row.value == "1" else "off")
    return enabled

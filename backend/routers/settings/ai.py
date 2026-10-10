"""AI settings — opt-in, provider, redaction, test connection, daily summary, usage.

These are the core AI building blocks every edition has. The features built on
them (Glow chat, postmortems, the daily summary job and its test send) live in
the enterprise package; the daily-summary *settings* are stored here so the
settings tab keeps one save path.
"""
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import encrypt_value, get_db, get_setting, set_setting
from notification_channels import DAILY_SUMMARY_DEFAULT_CHANNELS
from ratelimit import rate_limit
from services.ai_config import (
    DEFAULT_ANTHROPIC_MODEL,
    PROVIDER_OPENAI,
    PROVIDERS,
    AIConfig,
    load_ai_config,
    validate_ai_base_url_async,
)
from services.audit import log_action

from ._helpers import log, require_admin

router = APIRouter()

_BOOL_ON = ("1", "on", "true", "yes")

# Settings the AI tab writes, besides the secrets and the opt-in itself.
_PLAIN_KEYS = (
    "ai_provider", "ai_anthropic_model", "ai_openai_base_url",
    "ai_openai_model", "ai_openai_api_version",
)
_FLAG_KEYS = ("ai_redact_enabled", "ai_redact_hostnames", "ai_restore_placeholders")


def _actor_name(request: Request) -> str:
    user = getattr(request.state, "current_user", None)
    return getattr(user, "username", None) or "admin"


def _config_payload(cfg: AIConfig) -> dict:
    """AI config for the settings UI. Secrets are reported as present/absent only."""
    return {
        "ai_enabled": cfg.enabled,
        "ai_enabled_by": cfg.enabled_by,
        "ai_enabled_at": cfg.enabled_at,
        "ai_provider": cfg.provider,
        "ai_anthropic_model": cfg.anthropic_model,
        "ai_anthropic_default_model": DEFAULT_ANTHROPIC_MODEL,
        "claude_has_key": bool(cfg.anthropic_api_key),
        "ai_openai_base_url": cfg.openai_base_url,
        "ai_openai_has_key": bool(cfg.openai_api_key),
        "ai_openai_model": cfg.openai_model,
        "ai_openai_api_version": cfg.openai_api_version,
        "ai_redact_enabled": cfg.redact,
        "ai_redact_hostnames": cfg.redact_hostnames,
        "ai_restore_placeholders": cfg.restore_placeholders,
        "configured": cfg.configured,
        "missing": cfg.missing(),
        "provider_label": cfg.provider_label,
        "destination": cfg.destination(),
    }


@router.get("/ai/config")
async def get_ai_settings(request: Request, db: AsyncSession = Depends(get_db)):
    """Current AI settings (no secret values). Admin only."""
    if err := require_admin(request):
        return err
    cfg = await load_ai_config(db)
    data = _config_payload(cfg)
    data["daily_ai_summary_enabled"] = (await get_setting(db, "daily_ai_summary_enabled", "0")) == "1"
    data["daily_ai_summary_hour"] = await get_setting(db, "daily_ai_summary_hour", "") or "8"
    data["daily_ai_summary_channels"] = (
        await get_setting(db, "daily_ai_summary_channels", "") or DAILY_SUMMARY_DEFAULT_CHANNELS
    )
    return JSONResponse(data)


def _merge_form(cfg: AIConfig, form: dict[str, str]) -> AIConfig:
    """Config as it would be after saving ``form`` (unsent fields keep their value)."""
    new = replace(cfg)
    if form.get("ai_provider"):
        new.provider = form["ai_provider"]
    if "ai_anthropic_model" in form:
        new.anthropic_model = form["ai_anthropic_model"].strip() or DEFAULT_ANTHROPIC_MODEL
    if form.get("claude_api_key", "").strip():
        new.anthropic_api_key = form["claude_api_key"].strip()
    if "ai_openai_base_url" in form:
        new.openai_base_url = form["ai_openai_base_url"].strip()
    if "ai_openai_model" in form:
        new.openai_model = form["ai_openai_model"].strip()
    if "ai_openai_api_version" in form:
        new.openai_api_version = form["ai_openai_api_version"].strip()
    if form.get("ai_openai_api_key", "").strip():
        new.openai_api_key = form["ai_openai_api_key"].strip()
    if form.get("clear_openai_api_key") in _BOOL_ON:
        new.openai_api_key = ""
    if "ai_redact_enabled" in form:
        new.redact = form["ai_redact_enabled"] in _BOOL_ON
    if "ai_redact_hostnames" in form:
        new.redact_hostnames = form["ai_redact_hostnames"] in _BOOL_ON
    return new


async def _read_form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


@router.post("/ai/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_ai_settings(request: Request, db: AsyncSession = Depends(get_db)):
    """Save AI settings (opt-in, provider, redaction, daily summary). Admin only.

    Every form field is optional: a field that is not sent keeps its value.
    """
    if err := require_admin(request):
        return err

    form = await _read_form(request)
    old = await load_ai_config(db)

    provider = form.get("ai_provider", "")
    if provider and provider not in PROVIDERS:
        return JSONResponse({"ok": False, "error": f"Unknown provider: {provider}"}, status_code=400)

    new = _merge_form(old, form)
    if new.provider == PROVIDER_OPENAI and new.openai_base_url:
        if url_err := await validate_ai_base_url_async(new.openai_base_url):
            return JSONResponse({"ok": False, "error": f"Base URL rejected: {url_err}"}, status_code=400)

    want_enabled = old.enabled
    if "ai_enabled" in form:
        want_enabled = form["ai_enabled"] in _BOOL_ON
    if want_enabled and not new.configured:
        return JSONResponse({
            "ok": False,
            "error": "Configure the AI provider before enabling AI features "
                     f"(missing: {', '.join(new.missing())}).",
        }, status_code=400)

    changed: list[str] = []

    # Secrets: encrypted like every other credential; never echoed back.
    if form.get("claude_api_key", "").strip():
        await set_setting(db, "claude_api_key", encrypt_value(form["claude_api_key"].strip()))
        changed.append("claude_api_key")
    if form.get("clear_openai_api_key") in _BOOL_ON:
        await set_setting(db, "ai_openai_api_key", "")
        changed.append("ai_openai_api_key")
    elif form.get("ai_openai_api_key", "").strip():
        await set_setting(db, "ai_openai_api_key", encrypt_value(form["ai_openai_api_key"].strip()))
        changed.append("ai_openai_api_key")

    for key in _PLAIN_KEYS + _FLAG_KEYS:
        if key not in form:
            continue
        value = ("1" if form[key] in _BOOL_ON else "0") if key in _FLAG_KEYS else form[key].strip()
        if await get_setting(db, key, None) != value:
            await set_setting(db, key, value)
            changed.append(key)

    # Daily summary (unchanged semantics: "on" enables, anything else disables).
    if form.get("daily_ai_summary_enabled"):
        await set_setting(db, "daily_ai_summary_enabled",
                          "1" if form["daily_ai_summary_enabled"] == "on" else "0")
    if form.get("daily_ai_summary_hour"):
        await set_setting(db, "daily_ai_summary_hour", form["daily_ai_summary_hour"])
    if "daily_ai_summary_channels" in form:
        await set_setting(db, "daily_ai_summary_channels", form["daily_ai_summary_channels"])

    # Opt-in: who and when, in the settings and in the audit log.
    if want_enabled != old.enabled:
        who = _actor_name(request)
        await set_setting(db, "ai_enabled", "1" if want_enabled else "0")
        if want_enabled:
            await set_setting(db, "ai_enabled_by", who)
            await set_setting(db, "ai_enabled_at", datetime.utcnow().isoformat(timespec="seconds"))
        await log_action(
            db, request, "settings.ai_enabled" if want_enabled else "settings.ai_disabled",
            "setting", None, "ai_enabled",
            {"provider": new.provider, "destination": new.destination(), "redaction": new.redact},
        )
        log.warning("AI features %s by %s (provider: %s)",
                    "ENABLED" if want_enabled else "disabled", who, new.provider_label)
    elif changed:
        # Secrets are named, never logged by value.
        await log_action(db, request, "settings.ai_update", "setting", None, "ai",
                         {"changed": sorted(set(changed))})

    await db.commit()

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "config": _config_payload(await load_ai_config(db))})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/ai/test-connection")
@rate_limit(max_requests=6, window_seconds=60)
async def test_ai_connection(request: Request, db: AsyncSession = Depends(get_db)):
    """Check the provider with the values in the form (saved values fill the gaps).

    Sends a fixed, data-free prompt, so it also works before AI is enabled.
    """
    if err := require_admin(request):
        return err
    from services.ai_client import check_connection

    form = await _read_form(request)
    provider = form.get("ai_provider", "")
    if provider and provider not in PROVIDERS:
        return JSONResponse({"ok": False, "message": f"Unknown provider: {provider}"}, status_code=400)
    cfg = _merge_form(await load_ai_config(db), form)
    result = await check_connection(cfg)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.get("/ai/usage")
async def ai_usage_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Return AI token usage statistics (current month + all-time)."""
    if err := require_admin(request):
        return err

    from models.ai_usage import AiUsageLog

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    monthly = (await db.execute(
        select(
            func.coalesce(func.sum(AiUsageLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AiUsageLog.cost_usd), 0).label("cost_usd"),
            func.count().label("calls"),
        ).where(AiUsageLog.timestamp >= month_start)
    )).one()

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

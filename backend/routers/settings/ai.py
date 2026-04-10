"""Claude AI settings — save key, test daily summary, usage stats."""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import encrypt_value, get_db, get_setting, set_setting
from ratelimit import rate_limit

from ._helpers import log, require_admin

router = APIRouter()


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
    if err := require_admin(request):
        return err

    if claude_api_key.strip():
        await set_setting(db, "claude_api_key", encrypt_value(claude_api_key.strip()))

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
    if err := require_admin(request):
        return err

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

    data = await build_daily_summary_data(db)

    prompt = format_daily_summary_prompt(data)
    try:
        summary, usage = await generate_completion(
            _DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True,
        )
    except Exception as exc:
        log.error("Test AI summary generation failed: %s", exc)
        return JSONResponse({"ok": False, "message": f"AI generation failed: {exc}"}, status_code=500)

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

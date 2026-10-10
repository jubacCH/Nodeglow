"""Glow, the AI assistant chat (enterprise): POST /api/v1/glow/chat."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_key import ApiKey
from models.base import get_db
from routers.api_v1 import require_api_key
from services import ai_client
from services.ai_config import AIError, load_ai_config

from nodeglow_ee.ai import context as ai_context
from nodeglow_ee.ai.common import ai_unavailable

router = APIRouter(prefix="/api/v1", tags=["API v1"])

_glow_log = logging.getLogger("glow")

GLOW_SYSTEM_PROMPT = """Nodeglow Glow assistant. Analyse the infrastructure data below and answer concisely.
Rules: be specific, use bullet points, reference host/incident names. Never invent data not in context.

{context}"""

_MAX_HISTORY = 10  # keep last N messages to limit token usage


@router.post("/glow/chat", summary="Glow AI chat (streaming SSE)")
async def glow_chat(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    """Stream an AI copilot response as SSE events."""
    body = await request.json()
    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not user_message:
        raise HTTPException(400, "message is required")

    # Opt-in + provider check before gathering any context.
    ai_cfg = await load_ai_config(db)
    if not ai_cfg.enabled:
        return ai_unavailable("ai_disabled")
    if not ai_cfg.configured:
        return ai_unavailable("ai_not_configured")

    # Gather live infrastructure context
    try:
        context = await ai_context.gather_infrastructure_context(db)
    except Exception as e:
        _glow_log.warning("Failed to gather context: %s", e)
        context = "Infrastructure context unavailable."

    system_prompt = GLOW_SYSTEM_PROMPT.format(context=context)

    # Build messages: keep only last N history entries to limit tokens
    messages = []
    for h in history[-_MAX_HISTORY:]:
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    async def event_stream():
        try:
            async for delta in ai_client.stream_completion(system_prompt, messages, config=ai_cfg):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except AIError as e:
            _glow_log.warning("Glow request failed: %s", e)
            yield f"data: {json.dumps({'error': str(e), 'code': e.code, 'done': True})}\n\n"
        except Exception:
            _glow_log.exception("Glow stream error")
            yield f"data: {json.dumps({'error': 'An unexpected error occurred.', 'done': True})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

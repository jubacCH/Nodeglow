"""Inbound events: other systems open and resolve Nodeglow incidents.

``POST /api/v1/events``               one event or a list (generic format)
``POST /api/v1/events/alertmanager``  Prometheus Alertmanager webhook
``POST /api/v1/events/pagerduty``     PagerDuty webhook (v3): acknowledge / resolve

Both need an API key with the editor role, sent as ``X-API-Key`` or as
``Authorization: Bearer <key>`` (Alertmanager's ``http_config`` can only set
the latter). See services/external_events.py for the semantics.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_key import ApiKey
from models.base import get_db
from ratelimit import rate_limit
from routers.api_v1 import require_editor
from services import external_events as ext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["Inbound events"])

MAX_EVENTS = 200
MAX_BODY_BYTES = 1_000_000


async def _json_body(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, f"request body too large (max {MAX_BODY_BYTES} bytes)")
    try:
        import json
        return json.loads(raw)
    except ValueError:
        raise HTTPException(400, "request body is not valid JSON") from None


async def _ingest(db: AsyncSession, events: list[ext.ExternalEvent]) -> dict:
    results: list[dict] = []
    notices: list[ext.Notice] = []
    for event in events:
        result, notice = await ext.apply_event(db, event)
        # Two events for the same key in one request must see each other.
        await db.flush()
        results.append(result)
        if notice is not None:
            notices.append(notice)
    await db.commit()
    errors = await ext.send_notices(notices)
    if errors:
        logger.warning("Inbound events: %d notification(s) failed: %s", len(errors), errors)
    return {"ok": True, "results": results}


@router.post("", summary="Report events (open, update or resolve incidents)")
@rate_limit(max_requests=120, window_seconds=60)
async def post_events(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    body = await _json_body(request)
    items = body if isinstance(body, list) else [body]
    if not items:
        raise HTTPException(400, "no events in request")
    if len(items) > MAX_EVENTS:
        raise HTTPException(400, f"too many events in one request (max {MAX_EVENTS})")
    try:
        # All-or-nothing: one invalid event rejects the request, so a sender
        # never believes an event was taken that was dropped.
        events = [ext.parse_event(item) for item in items]
    except ext.EventError as exc:
        raise HTTPException(400, str(exc)) from None
    return await _ingest(db, events)


@router.post("/alertmanager", summary="Prometheus Alertmanager webhook receiver")
@rate_limit(max_requests=120, window_seconds=60)
async def post_alertmanager(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    body = await _json_body(request)
    try:
        events = ext.parse_alertmanager(body)
    except ext.EventError as exc:
        raise HTTPException(400, str(exc)) from None
    return await _ingest(db, events)


@router.post("/pagerduty", summary="PagerDuty webhook (v3): acknowledge and resolve from the pager")
@rate_limit(max_requests=240, window_seconds=60)
async def post_pagerduty(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    """An acknowledge or resolve done in PagerDuty is applied to the Nodeglow
    incident the alert belongs to. The subscription authenticates with an API
    key in a custom header (``X-API-Key``). Always answers 200 for a body it
    could read: PagerDuty retries anything else."""
    from database import get_setting
    from services import oncall_feedback

    body = await _json_body(request)
    public_url = (await get_setting(db, "public_url", "")) or ""
    result, resolved = await oncall_feedback.apply_pagerduty_event(db, body, public_url)
    if result["action"] in ("acknowledged", "resolved"):
        from services.audit import log_action
        action = {"acknowledged": "incident.acknowledge", "resolved": "incident.resolve"}[result["action"]]
        await log_action(db, request, action, "incident", result["incident_id"], "via PagerDuty")
    await db.commit()
    if resolved is not None:
        from extensions import fire_incident_resolved
        fire_incident_resolved([resolved.id])
        # The other channels learn about it too; for PagerDuty itself the
        # resolve is a no-op on an already resolved alert.
        try:
            from notifications import notify
            await notify(f"✅ Resolved: {resolved.title}", "Resolved in PagerDuty",
                         severity="info", link_path=f"/incidents/{resolved.id}")
        except Exception as exc:
            logger.warning("Failed to send resolve notification: %s", exc)
    return {"ok": True, **result}

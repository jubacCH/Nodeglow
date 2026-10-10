"""POST /api/v1/incidents/{id}/postmortem — (re)generate an AI postmortem (enterprise)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_key import ApiKey
from models.base import get_db
from models.incident import Incident
from routers.api_v1 import require_editor
from services.ai_config import load_ai_config

from nodeglow_ee.ai import postmortem
from nodeglow_ee.ai.common import ai_unavailable

router = APIRouter(prefix="/api/v1", tags=["API v1"])


@router.post("/incidents/{incident_id}/postmortem", summary="Generate or regenerate postmortem")
async def regenerate_postmortem(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    if incident.status != "resolved":
        raise HTTPException(400, "Postmortem can only be generated for resolved incidents")

    ai_cfg = await load_ai_config(db)
    if not ai_cfg.enabled:
        return ai_unavailable("ai_disabled")
    if not ai_cfg.configured:
        return ai_unavailable("ai_not_configured")

    try:
        asyncio.create_task(postmortem.generate_postmortem(incident.id))
    except Exception:
        pass
    return {"ok": True, "message": "Postmortem generation started"}


async def on_incident_resolved(incident_id: int) -> None:
    """Incident-resolved hook: draft the postmortem (skips itself when AI is off)."""
    await postmortem.generate_postmortem(incident_id)

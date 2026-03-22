"""Auto-postmortem generation for resolved incidents."""
import asyncio
import logging
from datetime import datetime

from models.base import AsyncSessionLocal
from models.incident import Incident
from services.ai_context import gather_incident_context
from services.ai_client import generate_completion

log = logging.getLogger("nodeglow.postmortem")

SYSTEM_PROMPT = """\
Generate a concise incident postmortem from the data provided. Use markdown with these sections:
## Summary (2-3 sentences)
## Timeline (bullet points)
## Root Cause
## Impact
## Recommendations
Only use provided data. Be brief."""


async def generate_postmortem(incident_id: int) -> None:
    """Generate an AI postmortem for a resolved incident.

    Runs as a fire-and-forget background task with its own DB session.
    All errors are caught and stored — this must never crash.
    """
    try:
        async with AsyncSessionLocal() as db:
            incident = await db.get(Incident, incident_id)
            if not incident:
                log.warning("Postmortem: incident %d not found", incident_id)
                return

            context = await gather_incident_context(db, incident_id)

            try:
                result = await generate_completion(
                    system_prompt=SYSTEM_PROMPT,
                    user_message=context,
                    max_tokens=1024,
                )
                incident.postmortem = result
            except Exception as e:
                log.error("Postmortem generation failed for incident %d: %s", incident_id, e)
                incident.postmortem = f"[Generation failed] {e}"

            incident.postmortem_generated_at = datetime.utcnow()
            await db.commit()
            log.info("Postmortem generated for incident %d", incident_id)

    except Exception as e:
        log.error("Postmortem task failed for incident %d: %s", incident_id, e, exc_info=True)

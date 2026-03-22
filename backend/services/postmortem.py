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
You are an infrastructure postmortem analyst for Nodeglow, a homelab monitoring system.
Generate a concise postmortem report based ONLY on the provided incident data.

Use markdown formatting with the following sections:

## Summary
2-3 sentences describing what happened.

## Timeline
Chronological list of key events.

## Root Cause Analysis
What triggered the incident based on the available data.

## Impact
What systems or services were affected.

## Recommendations
Actionable steps to prevent recurrence.

Rules:
- Be concise and factual.
- Base your analysis ONLY on the provided data — do not make up information.
- If there is insufficient data for a section, say so briefly.
- Use bullet points where appropriate.
"""


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
                    user_message=f"Generate a postmortem for this incident:\n\n{context}",
                    max_tokens=2000,
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

"""Helpers shared by the enterprise AI endpoints."""
from fastapi.responses import JSONResponse

from services.ai_config import AI_DISABLED_MESSAGE, AI_NOT_CONFIGURED_MESSAGE


def ai_unavailable(code: str) -> JSONResponse:
    """Uniform answer of every AI endpoint when AI is off or not set up."""
    message = AI_DISABLED_MESSAGE if code == "ai_disabled" else AI_NOT_CONFIGURED_MESSAGE
    return JSONResponse({"error": message, "code": code}, status_code=409)

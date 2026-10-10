"""Audit log service — record user actions."""
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from models.audit import AuditLog


def _actor(request) -> tuple[int | None, str | None]:
    """Who performed the request: the session user, else the API key.

    An API-key request has no session user (the middleware ignores the cookie
    when X-API-Key is present), so without the key fallback every action taken
    through the REST API was logged with no actor at all.
    """
    if request is None:
        return None, None
    state = getattr(request, "state", None)
    user = getattr(state, "current_user", None) if state is not None else None
    if user is not None:
        return getattr(user, "id", None), getattr(user, "username", None)
    key = getattr(state, "api_key", None) if state is not None else None
    if key is not None and getattr(key, "id", 0):
        # Prefix first: it identifies the key even if a long name is cut.
        prefix = getattr(key, "prefix", None) or "?"
        return None, f"apikey:{prefix}:{key.name}"[:64]
    return None, None


async def log_action(
    db: AsyncSession,
    request,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
    details: dict | None = None,
):
    user_id, username = _actor(request)
    ip = request.client.host if request and request.client else None
    db.add(AuditLog(
        timestamp=datetime.utcnow(),
        user_id=user_id,
        username=username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        details=json.dumps(details) if details else None,
        ip_address=ip,
    ))

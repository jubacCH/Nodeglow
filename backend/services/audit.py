"""Audit log service — record user actions."""
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from models.audit import AuditLog


async def log_action(
    db: AsyncSession,
    request,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
    details: dict | None = None,
):
    user = getattr(request.state, "current_user", None)
    ip = request.client.host if request and request.client else None
    db.add(AuditLog(
        timestamp=datetime.utcnow(),
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        details=json.dumps(details) if details else None,
        ip_address=ip,
    ))

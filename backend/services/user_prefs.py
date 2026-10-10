"""Read and write per-user preferences (models.user_preference)."""
from __future__ import annotations

from datetime import datetime

from models.user_preference import UserPreference


async def dashboard_seen_at(db, user_id: int | None) -> datetime | None:
    if not user_id:
        return None
    row = await db.get(UserPreference, user_id)
    return row.dashboard_seen_at if row else None


async def mark_dashboard_seen(db, user_id: int, at: datetime | None = None) -> tuple[datetime, datetime | None]:
    """Set the user's last dashboard visit; returns (new, previous). Commits."""
    at = at or datetime.utcnow()
    row = await db.get(UserPreference, user_id)
    previous = row.dashboard_seen_at if row else None
    if row is None:
        db.add(UserPreference(user_id=user_id, dashboard_seen_at=at, updated_at=at))
    else:
        row.dashboard_seen_at = at
        row.updated_at = at
    await db.commit()
    return at, previous

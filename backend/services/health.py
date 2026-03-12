"""
Integration health score service — compute 0.0-1.0 health for each integration.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.integration import IntegrationConfig, Snapshot

logger = logging.getLogger(__name__)

# Storage types that have pool % data
_STORAGE_TYPES = {"truenas", "unas", "synology"}


async def compute_integration_health(db: AsyncSession) -> dict[int, float]:
    """Compute health score (0.0 = healthy, 1.0 = critical) per integration config.

    Factors:
    - Latest snapshot failed?  +0.40
    - 24h success rate < 100%? up to +0.25
    - Staleness (no snapshot in 2x expected interval)? +0.20
    - Storage pool > 90%?  +0.15
    """
    now = datetime.utcnow()
    window_24h = now - timedelta(hours=24)

    # Get all enabled configs
    configs_result = await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)
    )
    configs = configs_result.scalars().all()
    if not configs:
        return {}

    # Get latest snapshot per config
    sub = (
        select(
            Snapshot.entity_id,
            Snapshot.entity_type,
            func.max(Snapshot.id).label("max_id"),
        )
        .group_by(Snapshot.entity_type, Snapshot.entity_id)
        .subquery()
    )
    latest_result = await db.execute(
        select(Snapshot).join(sub, Snapshot.id == sub.c.max_id)
    )
    latest_by_key: dict[tuple[str, int], Snapshot] = {}
    for snap in latest_result.scalars().all():
        latest_by_key[(snap.entity_type, snap.entity_id)] = snap

    # Get 24h success rates
    rate_result = await db.execute(
        select(
            Snapshot.entity_type,
            Snapshot.entity_id,
            func.count().label("total"),
            func.count(func.nullif(Snapshot.ok, False)).label("ok_count"),
        )
        .where(Snapshot.timestamp >= window_24h)
        .group_by(Snapshot.entity_type, Snapshot.entity_id)
    )
    rates: dict[tuple[str, int], tuple[int, int]] = {}
    for row in rate_result:
        rates[(row.entity_type, row.entity_id)] = (row.ok_count, row.total)

    scores: dict[int, float] = {}

    for cfg in configs:
        score = 0.0
        snap = latest_by_key.get((cfg.type, cfg.id))

        # Factor 1: Latest snapshot failed
        if not snap:
            score += 0.40
        elif not snap.ok:
            score += 0.40

        # Factor 2: 24h success rate
        ok_count, total = rates.get((cfg.type, cfg.id), (0, 0))
        if total > 0:
            success_rate = ok_count / total
            if success_rate < 1.0:
                score += (1.0 - success_rate) * 0.25
        elif snap:
            # No recent snapshots at all
            score += 0.10

        # Factor 3: Staleness
        if snap and snap.timestamp:
            age_minutes = (now - snap.timestamp).total_seconds() / 60
            if age_minutes > 10:  # stale if > 10min (typical interval is 60s)
                score += min(0.20, age_minutes / 60 * 0.05)

        # Factor 4: Storage pool health
        if snap and snap.ok and snap.data_json and cfg.type in _STORAGE_TYPES:
            try:
                data = json.loads(snap.data_json)
                for pool in data.get("storage_pools", []):
                    pct = pool.get("pct", 0)
                    if pct >= 95:
                        score += 0.15
                        break
                    elif pct >= 90:
                        score += 0.10
                        break
            except (json.JSONDecodeError, TypeError):
                pass

        scores[cfg.id] = round(min(score, 1.0), 3)

    return scores

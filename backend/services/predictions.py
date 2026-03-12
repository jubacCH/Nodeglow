"""
Disk-full prediction service — linear regression on historical snapshot data.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.integration import IntegrationConfig, Snapshot

logger = logging.getLogger(__name__)

# Storage integration types and their pool/volume keys
_STORAGE_TYPES = {
    "truenas": "pools",
    "unas": "pools",
    "synology": "volumes",
}


async def predict_disk_full(db: AsyncSession, days_back: int = 14) -> dict[str, dict]:
    """Predict when each storage pool will be full.

    Returns {"{config_id}:{pool_name}": {config_name, pool_name, source,
             current_pct, trend_pct_per_day, days_until_full, confidence}}
    """
    since = datetime.utcnow() - timedelta(days=days_back)

    # Get all storage configs
    result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.enabled == True,
            IntegrationConfig.type.in_(list(_STORAGE_TYPES.keys())),
        )
    )
    configs = result.scalars().all()
    if not configs:
        return {}

    predictions: dict[str, dict] = {}

    for cfg in configs:
        pools_key = _STORAGE_TYPES[cfg.type]
        label = {"truenas": "TrueNAS", "unas": "UNAS", "synology": "Synology"}[cfg.type]

        # Get snapshot history
        snap_result = await db.execute(
            select(Snapshot)
            .where(
                Snapshot.entity_type == cfg.type,
                Snapshot.entity_id == cfg.id,
                Snapshot.ok == True,
                Snapshot.timestamp >= since,
            )
            .order_by(Snapshot.timestamp.asc())
        )
        snapshots = snap_result.scalars().all()
        if len(snapshots) < 3:
            continue

        # Build time series per pool: {pool_name: [(epoch, pct), ...]}
        pool_series: dict[str, list[tuple[float, float]]] = {}
        for snap in snapshots:
            if not snap.data_json:
                continue
            try:
                data = json.loads(snap.data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            ts = snap.timestamp.timestamp()
            for pool in data.get(pools_key, []):
                name = pool.get("name", "?")
                pct = pool.get("pct")
                if pct is not None:
                    pool_series.setdefault(name, []).append((ts, float(pct)))

        # Linear regression per pool
        for pool_name, series in pool_series.items():
            if len(series) < 3:
                continue

            pred = _linear_predict(series)
            if pred is None:
                continue

            key = f"{cfg.id}:{pool_name}"
            predictions[key] = {
                "config_id": cfg.id,
                "config_name": cfg.name,
                "pool_name": pool_name,
                "source": f"{label}: {cfg.name}",
                "current_pct": pred["current"],
                "trend_pct_per_day": pred["slope_per_day"],
                "days_until_full": pred["days_until_full"],
                "confidence": pred["r_squared"],
                "data_points": len(series),
            }

    return predictions


def _linear_predict(series: list[tuple[float, float]]) -> dict | None:
    """Simple linear regression. Returns slope, intercept, R², prediction."""
    n = len(series)
    if n < 3:
        return None

    # Use days as x-axis (relative to first point)
    t0 = series[0][0]
    xs = [(t - t0) / 86400.0 for t, _ in series]
    ys = [pct for _, pct in series]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R-squared
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    current_pct = ys[-1]

    # Days until 100%
    if slope <= 0.001:  # not growing or barely
        days_until_full = None
    else:
        days_until_full = max(0, round((100.0 - current_pct) / slope))

    return {
        "current": round(current_pct, 1),
        "slope_per_day": round(slope, 4),
        "days_until_full": days_until_full,
        "r_squared": round(max(0, r_squared), 3),
    }

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

from services._sampling import select_sample_indices

# A disk-fill trend is a line through time; a few hundred points define it just
# as well as tens of thousands. The newest are kept exactly so a sudden change
# is not smoothed away.
PREDICTION_SAMPLE_BUDGET = 200
PREDICTION_KEEP_NEWEST = 10


logger = logging.getLogger(__name__)

# Storage integration types (all use standardized "storage_pools" key)
_STORAGE_TYPES = {"truenas", "unas", "synology"}
# Types with node-level disk metrics
_NODE_DISK_TYPES = {"proxmox"}


async def predict_disk_full(db: AsyncSession, days_back: int = 14) -> dict[str, dict]:
    """Predict when each storage pool will be full.

    Returns {"{config_id}:{pool_name}": {config_name, pool_name, source,
             current_pct, trend_pct_per_day, days_until_full, confidence}}
    """
    since = datetime.utcnow() - timedelta(days=days_back)

    # Get all storage + node-disk configs
    all_types = list(_STORAGE_TYPES | _NODE_DISK_TYPES)
    result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.enabled == True,
            IntegrationConfig.type.in_(all_types),
        )
    )
    configs = result.scalars().all()
    if not configs:
        return {}

    predictions: dict[str, dict] = {}

    from integrations import get_meta as _int_meta

    for cfg in configs:
        label = f"{_int_meta(cfg.type)['label']}: {cfg.name}"

        # Get snapshot history.
        #
        # Ids first: data_json runs to ~100 kB a row, and a week of proxmox
        # snapshots is 18'443 rows / 909 MB. Loading all of it per integration
        # made this ~9.6 s, which the dashboard paid for on every cache miss.
        # A disk-fill trend does not need every sample — an evenly spaced subset
        # describes the same line.
        id_rows = (await db.execute(
            select(Snapshot.id)
            .where(
                Snapshot.entity_type == cfg.type,
                Snapshot.entity_id == cfg.id,
                Snapshot.ok == True,
                Snapshot.timestamp >= since,
            )
            .order_by(Snapshot.timestamp.asc())
        )).all()

        if len(id_rows) < 3:
            continue

        wanted = [
            id_rows[i][0] for i in select_sample_indices(
                len(id_rows), PREDICTION_SAMPLE_BUDGET, PREDICTION_KEEP_NEWEST
            )
        ]

        snapshots = (await db.execute(
            select(Snapshot)
            .where(Snapshot.id.in_(wanted))
            .order_by(Snapshot.timestamp.asc())
        )).scalars().all()
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
            for pool in data.get("storage_pools", []):
                name = pool.get("name", "?")
                pct = pool.get("pct")
                if pct is not None:
                    pool_series.setdefault(name, []).append((ts, float(pct)))
            # Proxmox node disks
            if cfg.type in _NODE_DISK_TYPES:
                for node in data.get("nodes", []):
                    name = node.get("name", "?")
                    pct = node.get("disk_pct")
                    if pct is not None and node.get("disk_total_gb", 0) > 0:
                        pool_series.setdefault(name, []).append((ts, float(pct)))

        # Linear regression per pool
        for pool_name, series in pool_series.items():
            if len(series) < 3:
                continue

            pred = _linear_predict(series)
            if pred is None:
                continue

            prefix = "px-" if cfg.type in _NODE_DISK_TYPES else ""
            key = f"{prefix}{cfg.id}:{pool_name}"
            predictions[key] = {
                "config_id": cfg.id,
                "config_name": cfg.name,
                "pool_name": pool_name,
                "source": label,
                "current_pct": pred["current"],
                "trend_pct_per_day": pred["slope_per_day"],
                "days_until_full": pred["days_until_full"],
                "confidence": pred["r_squared"],
                "data_points": len(series),
            }

    return predictions


async def predict_agent_disks(db: AsyncSession, days_back: int = 14) -> dict[str, dict]:
    """Predict disk-full for agent disks from their agent_metrics history.

    Expensive (reads every agent's metric history): run it from the
    scheduler (:func:`refresh_cache`), never on a request path.
    Keys are ``agent-{agent_id}:{mount}``.
    """
    from models.agent import Agent
    from services import clickhouse_client as ch

    predictions: dict[str, dict] = {}
    agents = (await db.execute(select(Agent).where(Agent.enabled == True))).scalars().all()  # noqa: E712

    for agent in agents:
        snapshots = await ch.get_agent_history(agent.id, limit=10000, hours=days_back * 24)
        if len(snapshots) < 3:
            continue

        mount_series: dict[str, list[tuple[float, float]]] = {}
        mount_meta: dict[str, dict] = {}
        for snap in snapshots:
            data_json = snap.get("data_json")
            if not data_json:
                continue
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            ts_val = snap.get("timestamp")
            if not isinstance(ts_val, datetime):
                continue
            ts = ts_val.timestamp()
            for disk in data.get("disks", []):
                mount = disk.get("mount", "/")
                pct = disk.get("pct")
                total = disk.get("total_gb", 0)
                if pct is not None and total > 0.5:
                    mount_series.setdefault(mount, []).append((ts, float(pct)))
                    mount_meta[mount] = {"total_gb": total, "used_gb": disk.get("used_gb")}

        for mount, series in mount_series.items():
            if len(series) < 3:
                continue
            series.sort()
            pred = _linear_predict(series)
            if pred is None:
                continue
            predictions[f"agent-{agent.id}:{mount}"] = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "hostname": agent.hostname,
                "pool_name": mount,
                "source": f"Agent: {agent.hostname or agent.name}",
                "current_pct": pred["current"],
                "trend_pct_per_day": pred["slope_per_day"],
                "days_until_full": pred["days_until_full"],
                "confidence": pred["r_squared"],
                "data_points": len(series),
                **mount_meta.get(mount, {}),
            }

    return predictions


# ── Cache ────────────────────────────────────────────────────────────────────
# Both prediction kinds read days of history. The scheduler refreshes them
# (scheduler.refresh_disk_predictions); request paths only read the cache, the
# integration kind falls back to computing on a miss as the old dashboard did.
# _disk_pred_cache/_disk_pred_cache_ts are the names the old dashboard reads.

INTEGRATION_CACHE_TTL = 300.0
AGENT_CACHE_TTL = 3600.0
_disk_pred_cache: dict | None = None
_disk_pred_cache_ts: float = 0.0
_agent_pred_cache: dict | None = None
_agent_pred_cache_ts: float = 0.0


def cached_predictions(kind: str, max_age: float) -> dict | None:
    import time

    if kind == "agent":
        data, ts = _agent_pred_cache, _agent_pred_cache_ts
    else:
        data, ts = _disk_pred_cache, _disk_pred_cache_ts
    if data is not None and time.time() - ts < max_age:
        return data
    return None


def store_predictions(kind: str, data: dict) -> None:
    import time

    global _disk_pred_cache, _disk_pred_cache_ts, _agent_pred_cache, _agent_pred_cache_ts
    if kind == "agent":
        _agent_pred_cache, _agent_pred_cache_ts = data, time.time()
    else:
        _disk_pred_cache, _disk_pred_cache_ts = data, time.time()


async def integration_predictions(db: AsyncSession) -> dict[str, dict]:
    """Integration/proxmox predictions, from the cache or computed on a miss."""
    hit = cached_predictions("integration", INTEGRATION_CACHE_TTL)
    if hit is not None:
        return hit
    data = await predict_disk_full(db)
    store_predictions("integration", data)
    return data


async def refresh_cache(db: AsyncSession) -> None:
    """Recompute both kinds (scheduler job)."""
    store_predictions("integration", await predict_disk_full(db))
    store_predictions("agent", await predict_agent_disks(db))


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

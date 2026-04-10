"""Ping helper queries used by API v1 and other routers.

Post-cutover: backed by ClickHouse `ping_checks` instead of the deleted
Postgres `PingResult` table. The function signatures still take a `db`
parameter for source-compat with old callers — it is unused.
"""
from __future__ import annotations

from typing import Any

from services import clickhouse_client as ch


async def get_latest_by_host(
    db: Any, host_ids: list[int]
) -> dict[int, dict]:
    """Return {host_id: latest_record_dict} for the given host IDs.

    The dict shape is `{timestamp, success, latency_ms, host_name}`.
    """
    if not host_ids:
        return {}
    return await ch.get_latest_ping_per_host(host_ids)


async def get_uptime_map(db: Any) -> dict[int, dict]:
    """Return {host_id: {h24, d7, d30}} uptime percentages over multiple
    rolling windows. Three CH queries — one per window."""
    out: dict[int, dict] = {}
    for hours, key in ((24, "h24"), (24 * 7, "d7"), (24 * 30, "d30")):
        rows = await ch.get_ping_uptime(hours=hours)
        for host_id, stats in rows.items():
            out.setdefault(host_id, {})[key] = stats["uptime_pct"]
    return out

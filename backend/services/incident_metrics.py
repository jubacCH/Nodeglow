"""Metric series of an incident's hosts (concept F, phase 3, PR 3).

docs/specs/2026-11-15-concept-f-phase-3-design.md, part B. One builder for
the dashboard's ``latency`` section (the worst open incident) and for
``GET /api/v2/incidents/{id}/latency`` (the incident a person opened).

Only rules about reachability get a chart (owner decision 5); for the others
the endpoint answers 204 and the UI shows their last values instead.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import median

from services import host_state as hs

# Rules whose hosts are judged by reachability: latency and loss explain them.
REACHABILITY_RULES = frozenset({
    "host_down", "host_down_syslog", "multi_host_down", "upstream_failure", "integration_host",
})
DEFAULT_HOURS = 2
MAX_HOURS = 24
MAX_HOSTS = 200
NAMES_SHOWN = 10


def has_chart(incident) -> bool:
    """Whether the incident gets a latency chart: a reachability rule with recorded hosts."""
    return incident.rule in REACHABILITY_RULES and bool(hs.parse_host_ids(incident.host_ids))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 2)


async def latency_series(incident, *, names: dict[int, str], now: datetime,
                         hours: int = DEFAULT_HOURS) -> dict:
    """Per-minute median/max latency and packet loss of the incident's hosts.

    ``loss_pct`` is the share of failed checks in the minute: a link that
    drops packets shows little in latency alone. ``median_before_ms`` /
    ``median_since_ms`` compare the medians before and since the onset.
    """
    from services import clickhouse_client as ch

    hours = max(1, min(int(hours), MAX_HOURS))
    host_ids = (hs.parse_host_ids(incident.host_ids) or [])[:MAX_HOSTS]
    since = now - timedelta(hours=hours)
    rows = await ch.query(
        """
        SELECT toStartOfMinute(timestamp) AS minute,
               quantileIf(0.5)(latency_ms, success = 1) AS median_ms,
               maxIf(latency_ms, success = 1) AS max_ms,
               countIf(success = 1) AS ok,
               countIf(success = 0) AS failed
        FROM ping_checks
        WHERE tenant_id = {nodeglow_tenant:UInt32} AND host_id IN ({hids:Array(UInt32)}) AND timestamp >= {since:DateTime64(3)}
        GROUP BY minute ORDER BY minute
        """,
        {"hids": host_ids, "since": since},
    ) if host_ids else []
    points = []
    for r in rows:
        ok = int(r.get("ok") or 0)
        failed = int(r.get("failed") or 0)
        points.append({
            "t": hs.iso(r["minute"]) if isinstance(r.get("minute"), datetime) else str(r.get("minute")),
            "median_ms": _num(r.get("median_ms")) if ok else None,
            "max_ms": _num(r.get("max_ms")) if ok else None,
            "ok": ok, "failed": failed,
            "loss_pct": round(100.0 * failed / (ok + failed), 1) if ok + failed else None,
        })
    onset = incident.created_at
    before = [p["median_ms"] for p, r in zip(points, rows)
              if p["median_ms"] is not None and isinstance(r.get("minute"), datetime) and r["minute"] < onset]
    after = [p["median_ms"] for p, r in zip(points, rows)
             if p["median_ms"] is not None and isinstance(r.get("minute"), datetime) and r["minute"] >= onset]
    return {
        "incident_id": incident.id, "title": incident.title, "severity": incident.severity,
        "host_ids": host_ids, "host_names": [names.get(i) for i in host_ids[:NAMES_SHOWN]],
        "host_count": len(host_ids),
        "onset_at": hs.iso(onset),
        "window_hours": hours,
        "median_before_ms": round(median(before), 2) if before else None,
        "median_since_ms": round(median(after), 2) if after else None,
        "points": points,
    }

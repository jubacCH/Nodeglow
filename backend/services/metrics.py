"""Prometheus-format metrics for self-monitoring.

Exposes a `/metrics` endpoint via main.py and provides decorators / helpers
for instrumenting scheduler jobs, ClickHouse writes, and other internal
operations.

Design notes:
- One global registry (the prometheus_client default registry).
- Jobs use @instrument_job("name") to record duration / failure / run count.
- Counters and gauges are pre-declared at module level so label cardinality
  stays bounded — never invent new labels at call time.
- Cardinality discipline: avoid per-host labels unless the host count is
  small. For high-cardinality data, use ClickHouse instead.
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Awaitable, Callable, TypeVar

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# ── Build / process info ─────────────────────────────────────────────────────

UPTIME_SECONDS = Gauge(
    "nodeglow_uptime_seconds",
    "Seconds since process start.",
)

# Set at import time so /metrics always returns a value, even before the
# scheduler has run any job.
_PROCESS_START = time.time()


# ── Scheduler instrumentation ────────────────────────────────────────────────

SCHEDULER_JOB_RUNS = Counter(
    "nodeglow_scheduler_job_runs_total",
    "Total scheduler job invocations, by job name and status.",
    ["job", "status"],
)

SCHEDULER_JOB_DURATION = Histogram(
    "nodeglow_scheduler_job_duration_seconds",
    "Scheduler job wall-clock duration in seconds, by job name.",
    ["job"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

SCHEDULER_JOB_LAST_SUCCESS = Gauge(
    "nodeglow_scheduler_job_last_success_timestamp",
    "Unix timestamp of the last successful run, by job name.",
    ["job"],
)


# ── Syslog pipeline ──────────────────────────────────────────────────────────

SYSLOG_BUFFER_SIZE = Gauge(
    "nodeglow_syslog_buffer_size",
    "Number of syslog messages currently buffered awaiting flush.",
)

SYSLOG_MESSAGES_TOTAL = Counter(
    "nodeglow_syslog_messages_total",
    "Total syslog messages received, by severity bucket.",
    ["severity"],
)

SYSLOG_MESSAGES_DROPPED = Counter(
    "nodeglow_syslog_messages_dropped_total",
    "Syslog messages dropped due to rate limiting or buffer overflow.",
    ["reason"],
)


# ── ClickHouse client ────────────────────────────────────────────────────────

CLICKHOUSE_INSERTS = Counter(
    "nodeglow_clickhouse_inserts_total",
    "ClickHouse insert calls, by table and status.",
    ["table", "status"],
)

CLICKHOUSE_INSERT_ROWS = Counter(
    "nodeglow_clickhouse_insert_rows_total",
    "Total rows inserted into ClickHouse, by table.",
    ["table"],
)

CLICKHOUSE_QUERY_ERRORS = Counter(
    "nodeglow_clickhouse_query_errors_total",
    "ClickHouse query failures.",
)

CLICKHOUSE_CONNECTION_ERRORS = Counter(
    "nodeglow_clickhouse_connection_errors_total",
    "ClickHouse connection failures during init or reconnect.",
)


# ── Postgres pool ────────────────────────────────────────────────────────────

POSTGRES_POOL_SIZE = Gauge(
    "nodeglow_postgres_pool_size",
    "Current Postgres connection pool size (checked-out + idle).",
)

POSTGRES_POOL_CHECKED_OUT = Gauge(
    "nodeglow_postgres_pool_checked_out",
    "Postgres connections currently checked out from the pool.",
)


# ── Ping / monitoring counts ─────────────────────────────────────────────────
# These are deliberately *not* labeled by host_id (cardinality explosion).
# For per-host data, query ClickHouse `ping_checks` directly.

PING_CHECKS_TOTAL = Counter(
    "nodeglow_ping_checks_total",
    "Ping checks performed, by result.",
    ["result"],  # "success" | "failure"
)


# ── Public helpers ───────────────────────────────────────────────────────────

def render_metrics() -> tuple[bytes, str]:
    """Render the current registry as Prometheus text format.

    Returns (body, content_type) — caller wraps this in a FastAPI Response.
    Refreshes derived gauges (uptime, postgres pool) just before serializing
    so each scrape sees fresh values.
    """
    UPTIME_SECONDS.set(time.time() - _PROCESS_START)
    _refresh_postgres_pool_metrics()
    _refresh_syslog_buffer_metric()
    return generate_latest(), CONTENT_TYPE_LATEST


def _refresh_postgres_pool_metrics() -> None:
    """Pull live pool stats from the SQLAlchemy engine."""
    try:
        from database import engine
        pool = engine.pool
        # AsyncEngine pool exposes .size() and .checkedout() on its sync pool
        sync_pool = getattr(pool, "_pool", pool)
        size = getattr(sync_pool, "size", lambda: 0)
        checked_out = getattr(sync_pool, "checkedout", lambda: 0)
        POSTGRES_POOL_SIZE.set(size() if callable(size) else size)
        POSTGRES_POOL_CHECKED_OUT.set(checked_out() if callable(checked_out) else checked_out)
    except Exception:
        # Pool stats are best-effort; never break /metrics over them.
        pass


def _refresh_syslog_buffer_metric() -> None:
    """Pull current syslog buffer size from the running syslog server."""
    try:
        from services import syslog as syslog_svc
        buf = getattr(syslog_svc, "_buffer", None)
        if buf is not None:
            SYSLOG_BUFFER_SIZE.set(len(buf))
    except Exception:
        pass


T = TypeVar("T")


def instrument_job(name: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that records duration, success/failure, and last-success
    timestamp for an async scheduler job.

    Usage:
        @instrument_job("ping_checks")
        async def run_ping_checks():
            ...
    """
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                SCHEDULER_JOB_RUNS.labels(job=name, status="failure").inc()
                SCHEDULER_JOB_DURATION.labels(job=name).observe(time.time() - start)
                raise
            SCHEDULER_JOB_RUNS.labels(job=name, status="success").inc()
            SCHEDULER_JOB_DURATION.labels(job=name).observe(time.time() - start)
            SCHEDULER_JOB_LAST_SUCCESS.labels(job=name).set(time.time())
            return result
        return wrapper
    return decorator

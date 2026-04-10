"""MonitoringSource — thin convention for register-and-forget pollers.

The goal: replace the manual `@instrument_job + scheduler.add_job` boilerplate
in scheduler.py for poll-style monitoring sources (subnet_scanner, snmp,
port_discovery, ssl, etc.) with a single declarative interface.

What you get for free by subclassing `MonitoringSource`:

1. Auto-registration into a global registry on import (no manual `add_job`)
2. Standard Prometheus metrics via the existing @instrument_job decorator
3. Standard OpenTelemetry span via the same decorator
4. Consistent error handling and logging
5. Lifecycle hook the scheduler calls once at startup

Usage:

    from services.monitoring_source import MonitoringSource

    class SubnetScanner(MonitoringSource):
        name = "subnet_scanner"
        interval_seconds = 60

        async def poll(self):
            from routers.subnet_scanner import run_scheduled_scans
            await run_scheduled_scans()

The scheduler discovers all registered sources at startup and schedules
them. Migrating an existing job from scheduler.py to this pattern takes
~6 lines and removes the manual boilerplate.

Why not unify with `BaseIntegration`? The integration plugin pattern is
for external systems with config-per-instance, secrets, and
collect-into-snapshots. Monitoring sources are global, schedule-driven,
and have no per-instance config. They're a different shape — bending
either to fit the other costs more than it saves.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger("nodeglow.monitoring_source")

# Module-level registry. Populated on subclass creation via __init_subclass__.
_REGISTRY: dict[str, type["MonitoringSource"]] = {}


class MonitoringSource(ABC):
    """Base class for poll-style monitoring sources.

    Subclass attributes:
        name              — unique identifier (matches /metrics job label)
        interval_seconds  — poll interval; if None, source is not scheduled
                           automatically and must be triggered some other way
    """

    name: ClassVar[str] = ""
    interval_seconds: ClassVar[int | None] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise TypeError(
                f"MonitoringSource subclass {cls.__name__} must set `name`"
            )
        if cls.name in _REGISTRY:
            existing = _REGISTRY[cls.name].__name__
            raise ValueError(
                f"MonitoringSource name collision: {cls.__name__} vs {existing} "
                f"both claim '{cls.name}'"
            )
        _REGISTRY[cls.name] = cls

    @abstractmethod
    async def poll(self) -> None:
        """Run one iteration of the monitoring source. Must be idempotent."""


def get_registry() -> dict[str, type[MonitoringSource]]:
    """Return the live registry — used by the scheduler to enumerate sources."""
    return dict(_REGISTRY)


def clear_registry() -> None:
    """Test-only: drop all registered sources."""
    _REGISTRY.clear()


async def run_source(source_cls: type[MonitoringSource]) -> None:
    """Instantiate and run a single source, with metric instrumentation.

    Wrapped in `@instrument_job` at call time so the metric label uses the
    source's `name`. Failures are logged and re-raised so the instrument_job
    decorator records them as failures.
    """
    from services.metrics import instrument_job

    @instrument_job(source_cls.name)
    async def _runner():
        try:
            await source_cls().poll()
        except Exception:
            logger.exception("MonitoringSource %s.poll() failed", source_cls.name)
            raise

    await _runner()

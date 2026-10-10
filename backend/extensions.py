"""Extension points of the core.

Plugins (today: the enterprise package under ``ee/``) do not patch core
internals. They receive the :data:`registry` below from ``ee_loader`` and add
to it; the core reads it at the few places where it can be extended:

* ``main.py`` mounts the registered **routers**;
* ``scheduler.start_scheduler`` calls the registered **scheduler hooks** (to
  add jobs) and hands the scheduler to the **scheduler coordinator** if one
  is registered (HA leader election) — otherwise it runs single-instance;
* incident resolution (manual and automatic) calls the **incident-resolved
  hooks** after the transaction has committed;
* ``GET /api/v2/features`` reports the **edition** and **feature flags** so
  the UI shows only what this installation can do.

With no plugin loaded every list is empty, every flag keeps its community
default and every call site behaves exactly as the plain core.

This module must stay free of imports from ``ee/`` (see ``ee/README.md``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger("nodeglow.extensions")

EDITION_COMMUNITY = "community"
EDITION_ENTERPRISE = "enterprise"

# Every flag the UI may ask about, with its community value. Plugins switch
# flags on; they may also add new ones. Keys are stable API: the frontend
# reads them from GET /api/v2/features.
DEFAULT_FEATURES: dict[str, bool] = {
    # Scheduler leader election across workers/replicas (Redis lease).
    "ha_scheduler": False,
    # Glow, the AI assistant chat.
    "ai_assistant": False,
    # AI postmortem drafts for resolved incidents.
    "ai_postmortem": False,
    # The AI daily summary sent to notification channels.
    "ai_daily_summary": False,
}


class SchedulerCoordinator(Protocol):
    """Decides when the scheduler of this process may run its jobs."""

    def wants_control(self) -> bool:
        """True if this coordinator should start the scheduler (else core does)."""

    async def start(self, scheduler: Any) -> None:
        """Start ``scheduler`` (possibly paused) and keep it in the right state."""

    def stop(self) -> None:
        """Stop coordinating; called before the scheduler shuts down."""


IncidentResolvedHook = Callable[[int], Awaitable[None]]
SchedulerHook = Callable[[Any], Awaitable[None]]


@dataclass
class Registry:
    """What plugins have contributed. One process-wide instance: :data:`registry`."""

    plugins: list[str] = field(default_factory=list)
    routers: list[Any] = field(default_factory=list)
    scheduler_hooks: list[SchedulerHook] = field(default_factory=list)
    incident_resolved_hooks: list[IncidentResolvedHook] = field(default_factory=list)
    scheduler_coordinator: SchedulerCoordinator | None = None
    features: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_FEATURES))
    edition: str = EDITION_COMMUNITY

    # ── registration API (used by plugins) ──────────────────────────────────

    def add_router(self, router: Any) -> None:
        """Mount a FastAPI ``APIRouter`` on the app (routes keep their own paths)."""
        self.routers.append(router)

    def add_scheduler_hook(self, hook: SchedulerHook) -> None:
        """``await hook(scheduler)`` runs in ``start_scheduler`` to add jobs."""
        self.scheduler_hooks.append(hook)

    def on_incident_resolved(self, hook: IncidentResolvedHook) -> None:
        """``hook(incident_id)`` runs as a background task after a resolve commits."""
        self.incident_resolved_hooks.append(hook)

    def set_scheduler_coordinator(self, coordinator: SchedulerCoordinator) -> None:
        if self.scheduler_coordinator is not None:
            raise RuntimeError("A scheduler coordinator is already registered")
        self.scheduler_coordinator = coordinator

    def enable_feature(self, name: str) -> None:
        self.features[name] = True

    # ── read API (used by the core) ─────────────────────────────────────────

    def feature_payload(self) -> dict:
        return {"edition": self.edition, "features": dict(sorted(self.features.items()))}

    def reset(self) -> None:
        """Back to the plain core (test helper)."""
        fresh = Registry()
        self.__dict__.update(fresh.__dict__)


registry = Registry()


def fire_incident_resolved(incident_ids: list[int] | tuple[int, ...]) -> None:
    """Spawn every incident-resolved hook for each id. Never raises.

    Call it only after the resolve has been committed: the hooks open their
    own sessions and must see the resolved incident.
    """
    for incident_id in incident_ids:
        for hook in registry.incident_resolved_hooks:
            try:
                asyncio.create_task(hook(incident_id))
            except Exception:
                log.warning("incident-resolved hook %r failed to start", hook, exc_info=True)

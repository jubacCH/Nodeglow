"""Scheduler leader election across workers / replicas (enterprise).

Without a leader lock every uvicorn worker / replica would start its own
scheduler and run correlation, ping, etc. concurrently → duplicate incidents
and duplicate work. When ``REDIS_URL`` is set, all instances contend for a
single Redis lease and only the holder runs jobs. Without Redis this
coordinator steps aside (``wants_control() is False``) and the core starts the
scheduler in single-instance mode, exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from nodeglow_ee.ha import leader_lock

logger = logging.getLogger("nodeglow.ee.ha")

SCHEDULER_LEADER_LOCK = "nodeglow:scheduler:leader"
LEADER_TTL_SECONDS = 30
LEADER_RENEW_SECONDS = 10


class LeaderElectionCoordinator:
    """Registered as ``extensions.registry.scheduler_coordinator``."""

    def __init__(self, instance_id: str | None = None):
        self.instance_id = instance_id or f"{socket.gethostname()}:{os.getpid()}"
        self.is_leader = False
        self._scheduler = None
        self._task: asyncio.Task | None = None

    def wants_control(self) -> bool:
        from services import shared_state
        return bool(shared_state.redis_url())

    async def start(self, scheduler) -> None:
        # Start paused; the loop resumes jobs once this instance wins the lease
        # and pauses them again if it loses it.
        self._scheduler = scheduler
        self.is_leader = False
        scheduler.start(paused=True)
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started in leader-election mode (instance=%s)", self.instance_id)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        # The Redis lease is left to expire via its TTL, which hands leadership
        # to a standby within LEADER_TTL_SECONDS.

    def apply_leadership(self, leader: bool) -> None:
        """Resume or pause the scheduler on a leadership transition (idempotent)."""
        if leader and not self.is_leader:
            self._scheduler.resume()
            self.is_leader = True
            logger.info("Acquired scheduler leadership — jobs running (instance=%s)", self.instance_id)
        elif not leader and self.is_leader:
            self._scheduler.pause()
            self.is_leader = False
            logger.warning("Lost scheduler leadership — jobs paused (instance=%s)", self.instance_id)

    async def _loop(self) -> None:
        """Periodically acquire/renew the lease; resume jobs only while leader."""
        while True:
            try:
                leader = await leader_lock.try_acquire_leader(
                    SCHEDULER_LEADER_LOCK, self.instance_id, LEADER_TTL_SECONDS
                )
                self.apply_leadership(leader)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Preserve current leadership state on transient errors: never
                # grant leadership to everyone (split-brain) nor stop a healthy
                # leader. A genuinely dead leader frees the lease via its TTL.
                logger.error("Scheduler leadership loop error: %s", e)
            await asyncio.sleep(LEADER_RENEW_SECONDS)

"""One write path for integration results.

Every collection result — today the core scheduler's, with probe-side
integrations also a probe's (``docs/specs/2026-10-11-probe-side-integrations.md``)
— is stored through :func:`ingest`: the snapshot, the cluster-group
primary/standby decision, the bandwidth series and the ``on_snapshot`` hook.

Rule 6 of the spec: a result is accepted only if the ``config_rev`` and the
collector it was **started** with are still the config's current ones. The
caller captures a :class:`CollectStamp` when it starts a collect; ingest
re-reads the config (the caller holds :func:`snapshot_write_lock`) and drops
the result — no snapshot, neither ok nor failed — when the config was edited,
moved to another collector or deleted meanwhile.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from models.integration import COLLECTOR_CORE, COLLECTOR_PROBE, IntegrationConfig
from services import snapshot as snap_svc

logger = logging.getLogger(__name__)

# Placed in the snapshot row's ``error`` field of a cluster-group standby.
# Visible in the API/UI ("standby for cluster X"); not a real error.
STANDBY_MARKER = "[standby:duplicate-of-primary]"


@dataclass(frozen=True)
class CollectStamp:
    """What a collect was started with: the config's revision and collector."""

    config_rev: int
    collector: str = COLLECTOR_CORE
    probe_id: int | None = None

    @classmethod
    def of(cls, cfg: IntegrationConfig) -> "CollectStamp":
        return cls(
            config_rev=cfg.config_rev if cfg.config_rev is not None else 1,
            collector=cfg.collector or COLLECTOR_CORE,
            probe_id=cfg.probe_id,
        )


class Outcome(str, Enum):
    STORED = "stored"            # ok snapshot (primary), bandwidth, hook
    FAILED = "failed"            # failed snapshot with the collector's error
    STANDBY = "standby"          # health-only snapshot of a cluster-group standby
    DELETED = "deleted"          # config gone meanwhile; nothing written
    STALE_CONFIG_REV = "stale_config_rev"   # config edited meanwhile; nothing written
    NOT_ASSIGNED = "not_assigned"           # collector / probe changed; nothing written

    @property
    def written(self) -> bool:
        return self in (Outcome.STORED, Outcome.FAILED, Outcome.STANDBY)


def check_stamp(cfg: IntegrationConfig | None, stamp: CollectStamp) -> Outcome | None:
    """Why a result started with ``stamp`` must be dropped, or None.

    The revision must match **exactly**: older means the result was collected
    with an old address or old credentials, newer is a rev never issued."""
    if cfg is None:
        return Outcome.DELETED
    current = CollectStamp.of(cfg)
    if current.config_rev != stamp.config_rev:
        return Outcome.STALE_CONFIG_REV
    if current.collector != stamp.collector or current.probe_id != stamp.probe_id:
        return Outcome.NOT_ASSIGNED
    if stamp.collector == COLLECTOR_PROBE and stamp.probe_id is None:
        return Outcome.NOT_ASSIGNED  # "needs a probe": nobody may deliver for it
    return None


# Created per event loop: asyncio primitives bind to the loop they first wait
# on, and the test suite runs every test on a fresh loop.
_loop_locks: dict[int, asyncio.Lock] = {}


def snapshot_write_lock() -> asyncio.Lock:
    """Serialises result writes: they are cheap, some (on_snapshot
    auto-import) touch shared host rows, cluster-group ownership is decided
    in order, and the stamp check must see the config as it is when writing."""
    key = id(asyncio.get_running_loop())
    lock = _loop_locks.get(key)
    if lock is None:
        _loop_locks.clear()  # at most one live loop in production
        lock = _loop_locks[key] = asyncio.Lock()
    return lock


async def _extract_bandwidth(integration_type: str, cfg: IntegrationConfig, data: Any,
                             timestamp: datetime | None) -> None:
    from services.bandwidth import extract_proxmox_bandwidth, extract_unifi_bandwidth

    if integration_type == "proxmox":
        await extract_proxmox_bandwidth(
            cfg.id, data, tenant_id=cfg.tenant_id, source_name=cfg.name, timestamp=timestamp,
        )
    elif integration_type == "unifi":
        await extract_unifi_bandwidth(
            cfg.id, data, tenant_id=cfg.tenant_id, source_name=cfg.name, timestamp=timestamp,
        )


async def ingest(
    db,
    *,
    integration_type: str,
    cfg_id: int,
    stamp: CollectStamp,
    result=None,
    exc: BaseException | None = None,
    instance=None,
    integration_cls=None,
    config: dict | None = None,
    cluster_owners: set[tuple[str, str]] | None = None,
    collected_at: datetime | None = None,
) -> Outcome:
    """Store one collection result. The caller holds :func:`snapshot_write_lock`
    and commits ``db``.

    ``result`` is the collector's ``CollectorResult`` (or ``exc`` the exception
    the collect raised). ``instance`` / ``integration_cls`` + ``config`` give
    the hook its integration object. ``cluster_owners`` carries the
    cluster-group primaries already decided in this pass. ``collected_at`` is
    when the result was collected (a probe's clock, corrected); None = now.
    """
    cfg = await db.get(IntegrationConfig, cfg_id, populate_existing=True)
    rejected = check_stamp(cfg, stamp)
    if rejected is not None:
        logger.info(
            "Integration result dropped [%s/%s]: %s (started with rev %s, %s%s)",
            integration_type, cfg.name if cfg is not None else cfg_id, rejected.value,
            stamp.config_rev, stamp.collector,
            f" {stamp.probe_id}" if stamp.probe_id is not None else "",
        )
        return rejected

    if cluster_owners is None:
        cluster_owners = set()
    collected_by = stamp.probe_id if stamp.collector == COLLECTOR_PROBE else None
    stamp_kw = {"timestamp": collected_at, "collected_by": collected_by,
                "collected_at": collected_at}

    try:
        if exc is not None:
            raise exc
        if not result.success:
            await snap_svc.save(db, integration_type, cfg.id, ok=False, error=result.error,
                                **stamp_kw)
            return Outcome.FAILED

        # Auto-populate cluster_group from cluster_name for Proxmox
        # integrations on the first successful poll.
        if (
            integration_type == "proxmox"
            and not cfg.cluster_group
            and isinstance(result.data, dict)
            and result.data.get("cluster_name")
        ):
            cfg.cluster_group = str(result.data["cluster_name"])
            logger.info(
                "Auto-grouped %s/%s under cluster_group=%s",
                integration_type, cfg.name, cfg.cluster_group,
            )

        # Primary vs standby for this cluster_group.
        group_key = (integration_type, cfg.cluster_group) if cfg.cluster_group else None
        if group_key is not None and group_key in cluster_owners:
            # Standby: a health-only snapshot, so the health check passes;
            # no data, bandwidth, hooks or alerts.
            await snap_svc.save(db, integration_type, cfg.id, ok=True, error=STANDBY_MARKER,
                                **stamp_kw)
            logger.debug(
                "Standby skip [%s/%s] — primary already wrote group=%s",
                integration_type, cfg.name, cfg.cluster_group,
            )
            return Outcome.STANDBY
        if group_key is not None:
            cluster_owners.add(group_key)

        await snap_svc.save(db, integration_type, cfg.id, ok=True, data=result.data, **stamp_kw)
        try:
            await _extract_bandwidth(integration_type, cfg, result.data, collected_at)
        except Exception as bw_exc:
            logger.warning(
                "Bandwidth extraction failed [%s/%s]: %s", integration_type, cfg.name, bw_exc,
            )
        # Post-snapshot hook (e.g. auto-import hosts).
        try:
            if instance is None and integration_cls is not None:
                instance = integration_cls(config=config or {})
            if instance is not None:
                instance.config_id = cfg.id
                await instance.on_snapshot(result.data, config or {}, db)
        except Exception as hook_exc:
            logger.warning(
                "on_snapshot hook failed [%s/%s]: %s", integration_type, cfg.name, hook_exc,
            )
        return Outcome.STORED
    except Exception as exc2:
        logger.error("Integration collect [%s/%s]: %s", integration_type, cfg.name, exc2)
        await snap_svc.save(db, integration_type, cfg.id, ok=False, error=str(exc2), **stamp_kw)
        return Outcome.FAILED

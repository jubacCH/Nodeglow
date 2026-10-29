"""Incidents from integration data.

Two kinds, both raised from the ingest path (:mod:`services.integration_ingest`):

- ``integration_alert``: what an integration's ``parse_alerts()`` finds in a
  successful snapshot — a failed disk, a degraded RAID, an expiring
  certificate. One incident per (source, alert title, entity). It opens once
  the alert was seen in ``ALERT_MIN_CYCLES`` consecutive snapshots, so a single
  CPU spike does not page anyone, and resolves with the first snapshot that no
  longer reports it.
- ``integration_failed``: the integration itself cannot collect. Opens after
  ``FAILED_MIN_SNAPSHOTS`` failed snapshots in a row, resolves on the next
  successful (or standby) one.

The source of an alert is the config, or its cluster group when it has one:
after a failover the new primary reports the same alerts and must continue the
open incidents rather than open duplicates.

``resolve_orphans`` (run by the correlation cycle) closes incidents whose
config was deleted or disabled, since no snapshot will ever resolve them.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy import select

from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig, Snapshot

log = logging.getLogger(__name__)

ALERT_RULE = "integration_alert"
FAILED_RULE = "integration_failed"
RULES = (ALERT_RULE, FAILED_RULE)

ALERT_MIN_CYCLES = 2
FAILED_MIN_SNAPSHOTS = 3

# "info" alerts (an update is available) are not worth an incident.
_INCIDENT_SEVERITIES = ("critical", "warning")

# (tenant_id, alert key) -> consecutive snapshots that reported it. In-process:
# a restart costs at most one extra cycle before an incident opens.
_streaks: dict[tuple[int | None, str], int] = {}


def _source(cfg: IntegrationConfig) -> str:
    """Stable id of where alerts come from: the cluster group or the config."""
    if cfg.cluster_group:
        digest = hashlib.sha256(f"{cfg.type}/{cfg.cluster_group}".encode()).hexdigest()[:12]
        return f"g{digest}"
    return str(cfg.id)


def alert_key(cfg: IntegrationConfig, alert) -> str:
    """Dedup key (``Incident.host_ids_hash``) of one alert of one source."""
    digest = hashlib.sha256(f"{alert.title}|{alert.entity}".encode()).hexdigest()[:16]
    return f"ia:{_source(cfg)}:{digest}"


def failed_key(cfg: IntegrationConfig) -> str:
    return f"if:{cfg.id}"


def _alert_title(cfg: IntegrationConfig, alert) -> str:
    entity = f" – {alert.entity}" if alert.entity else ""
    return f"{cfg.name}: {alert.title}{entity}"[:256]


async def _notify(title: str, summary: str, severity: str, incident_id: int) -> None:
    try:
        from notifications import notify
        await notify(title, summary, severity=severity, link_path=f"/incidents/{incident_id}")
    except Exception as exc:
        log.warning("Failed to send incident notification: %s", exc)


async def _resolve(db, incident: Incident, summary: str) -> None:
    incident.status = "resolved"
    incident.resolved_at = datetime.utcnow()
    db.add(IncidentEvent(incident_id=incident.id, event_type="resolved", summary=summary))
    await _notify(f"✅ Resolved: {incident.title}", summary, "info", incident.id)


async def _open(db, rule: str, prefix: str) -> list[Incident]:
    return list((await db.execute(
        select(Incident).where(
            Incident.rule == rule,
            Incident.host_ids_hash.like(f"{prefix}%"),
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalars().all())


async def evaluate_alerts(db, cfg: IntegrationConfig, instance, data) -> None:
    """Open, update and resolve the alert incidents of one successful snapshot."""
    from services.correlation import _find_or_create_incident

    if instance is None or not isinstance(data, dict):
        return
    alerts = [a for a in instance.parse_alerts(data) if a.severity in _INCIDENT_SEVERITIES]
    tenant = cfg.tenant_id
    prefix = f"ia:{_source(cfg)}:"

    current: dict[str, object] = {}
    for alert in alerts:
        current.setdefault(alert_key(cfg, alert), alert)

    # Streaks of alerts that are gone end here.
    for k in [k for k in _streaks if k[0] == tenant and k[1].startswith(prefix)]:
        if k[1] not in current:
            del _streaks[k]

    open_keys = {i.host_ids_hash: i for i in await _open(db, ALERT_RULE, prefix)}
    for key, alert in current.items():
        streak = _streaks[(tenant, key)] = _streaks.get((tenant, key), 0) + 1
        if key not in open_keys and streak < ALERT_MIN_CYCLES:
            continue
        await _find_or_create_incident(
            db, rule=ALERT_RULE, title=_alert_title(cfg, alert), severity=alert.severity,
            host_ids=[], event_type="integration_alert",
            summary=alert.detail or alert.title, key_hash=key,
        )

    for key, incident in open_keys.items():
        if key not in current:
            await _resolve(db, incident, f"Resolved: {cfg.name} no longer reports this")


async def record_failure(db, cfg: IntegrationConfig, error: str | None) -> None:
    """Open (or extend) the failure incident once enough snapshots in a row failed.

    Called after the failed snapshot was added to ``db``."""
    from services.correlation import _find_or_create_incident

    await db.flush()
    recent = (await db.execute(
        select(Snapshot.ok).where(Snapshot.entity_type == cfg.type, Snapshot.entity_id == cfg.id)
        .order_by(Snapshot.id.desc()).limit(FAILED_MIN_SNAPSHOTS)
    )).scalars().all()
    if len(recent) < FAILED_MIN_SNAPSHOTS or any(recent):
        return
    await _find_or_create_incident(
        db, rule=FAILED_RULE, title=f"{cfg.name}: integration cannot collect"[:256],
        severity="warning", host_ids=[], event_type="integration_error",
        summary=f"{cfg.type} integration '{cfg.name}' failed: {error or 'unknown error'}",
        key_hash=failed_key(cfg),
    )


async def record_success(db, cfg: IntegrationConfig) -> None:
    """Resolve the failure incident of a config that collects again."""
    for incident in await _open(db, FAILED_RULE, failed_key(cfg)):
        if incident.host_ids_hash == failed_key(cfg):
            await _resolve(db, incident, f"Resolved: {cfg.name} collects again")


async def resolve_orphans(db) -> int:
    """Resolve integration incidents whose config was deleted or disabled."""
    configs = (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)  # noqa: E712
    )).scalars().all()
    live = {f"ia:{_source(c)}:" for c in configs} | {failed_key(c) for c in configs}

    resolved = 0
    for rule in RULES:
        for incident in await _open(db, rule, ""):
            key = incident.host_ids_hash or ""
            prefix = key if rule == FAILED_RULE else key.rsplit(":", 1)[0] + ":"
            if prefix not in live:
                await _resolve(db, incident, "Resolved: the integration was removed or disabled")
                resolved += 1
    return resolved

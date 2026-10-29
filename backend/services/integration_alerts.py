"""Incidents from integration data.

Two kinds, both raised from the ingest path (:mod:`services.integration_ingest`):

- ``integration_alert``: what an integration's ``parse_alerts()`` finds in a
  successful snapshot — a failed disk, a degraded RAID, an expiring
  certificate. One incident per (source, alert title, entity). It opens once
  the alert was seen in ``ALERT_MIN_CYCLES`` consecutive snapshots, so a single
  CPU spike does not page anyone, and resolves with the first snapshot that no
  longer reports it.
- ``integration_failed``: the integration itself cannot collect. Opens once
  ``FAILED_MIN_SNAPSHOTS`` collections in a row failed *and* the last success
  is ``FAILED_MIN_MINUTES`` old, so a 60-second integration alarms after about
  ten minutes and a three-hourly speedtest after two missed runs. Resolves on
  the next successful (or standby) collection.

The source of an alert is the config, or its cluster group when it has one:
after a failover the new primary reports the same alerts and must continue the
open incidents rather than open duplicates.

Incidents name the ping hosts they concern: the device an alert names
(``Alert.host``), else the integration's own endpoint. A host in maintenance
opens no new incident; open ones still resolve.

Notifications are queued on the session and sent by the caller after its
commit (:func:`take_notifications`, :func:`send_notifications`), outside the
snapshot write lock: a slow webhook must not hold up every integration, and a
rolled-back incident must not have been announced.

``resolve_orphans`` (run by the correlation cycle) closes incidents whose
config was deleted or disabled, since no snapshot will ever resolve them.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig, Snapshot

log = logging.getLogger(__name__)

ALERT_RULE = "integration_alert"
FAILED_RULE = "integration_failed"
RULES = (ALERT_RULE, FAILED_RULE)

ALERT_MIN_CYCLES = 2
FAILED_MIN_SNAPSHOTS = 2
FAILED_MIN_MINUTES = 10

# "info" alerts (an update is available) are not worth an incident.
_INCIDENT_SEVERITIES = ("critical", "warning")

# Config keys that point an integration at its device (as in the UI form).
_ENDPOINT_KEYS = ("host", "hostname", "url", "base_url", "api_url", "endpoint", "server", "address")

_PENDING = "integration_alert_notifications"

# (tenant_id, alert key) -> consecutive snapshots that reported it. In-process:
# a restart costs at most one extra cycle before an incident opens.
_streaks: dict[tuple[int | None, str], int] = {}


@dataclass(frozen=True)
class Notification:
    title: str
    summary: str
    severity: str
    incident_id: int


# ── Keys ─────────────────────────────────────────────────────────────────────

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


def alert_prefix(cfg: IntegrationConfig) -> str:
    return f"ia:{_source(cfg)}:"


def failed_key(cfg: IntegrationConfig) -> str:
    return f"if:{cfg.id}"


def _alert_title(cfg: IntegrationConfig, alert) -> str:
    entity = f" – {alert.entity}" if alert.entity else ""
    return f"{cfg.name}: {alert.title}{entity}"[:256]


# ── Hosts and maintenance ────────────────────────────────────────────────────

def _bare(value: str) -> str:
    """Host part of a hostname, IP or URL, lower-case."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "//" + value
    try:
        return (urlsplit(value).hostname or "").strip(".")
    except ValueError:
        return ""


def endpoint_of(config: dict | None) -> str:
    """The device an integration talks to, from its config."""
    for key in _ENDPOINT_KEYS:
        value = (config or {}).get(key)
        if isinstance(value, str) and _bare(value):
            return _bare(value)
    return ""


class HostIndex:
    """Ping hosts by hostname, IP, name and unambiguous short name."""

    def __init__(self, hosts):
        self._by: dict[str, object] = {}
        short: dict[str, list] = {}
        for h in hosts:
            for value in (h.hostname, h.ip_address, h.name):
                key = _bare(value or "")
                if key:
                    self._by.setdefault(key, h)
            label = _bare(h.hostname or "").split(".")[0]
            if label:
                short.setdefault(label, []).append(h)
        for label, found in short.items():
            if len(found) == 1:
                self._by.setdefault(label, found[0])

    def find(self, value: str):
        key = _bare(value)
        if not key:
            return None
        return self._by.get(key) or self._by.get(key.split(".")[0])


async def _host_index(db) -> HostIndex:
    from models.ping import PingHost

    return HostIndex((await db.execute(select(PingHost))).scalars().all())


async def _in_maintenance(db, hosts) -> bool:
    from services.maintenance import is_in_maintenance, load_windows

    hosts = [h for h in hosts if h is not None]
    if not hosts:
        return False
    windows = await load_windows(db)
    now = datetime.utcnow()
    return any(is_in_maintenance(h, now, windows) for h in hosts)


# ── Notifications ────────────────────────────────────────────────────────────

def _queue(db, note: Notification) -> None:
    db.info.setdefault(_PENDING, []).append(note)


def pending_count(db) -> int:
    return len(db.info.get(_PENDING, []))


def drop_since(db, count: int) -> None:
    """Forget notifications queued after ``count`` (their savepoint rolled back)."""
    del db.info.setdefault(_PENDING, [])[count:]


def take_notifications(db) -> list[Notification]:
    """Pop what this session queued; call after the commit."""
    return db.info.pop(_PENDING, [])


async def send_notifications(notes: list[Notification]) -> None:
    for note in notes:
        try:
            from notifications import notify
            await notify(note.title, note.summary, severity=note.severity,
                         link_path=f"/incidents/{note.incident_id}")
        except Exception as exc:
            log.warning("Failed to send incident notification: %s", exc)


# ── Incident steps ───────────────────────────────────────────────────────────

async def _open_or_extend(db, *, rule, title, severity, event_type, summary, key, host_ids) -> None:
    from services.correlation import _find_or_create_incident

    existed = await _open(db, rule, key, exact=True)
    incident = await _find_or_create_incident(
        db, rule=rule, title=title, severity=severity, host_ids=[], event_type=event_type,
        summary=summary, key_hash=key, send_notification=False, affected_host_ids=host_ids,
    )
    if not existed:
        await db.flush()
        _queue(db, Notification(f"🔴 Incident: {title}", summary, severity, incident.id))


async def _resolve(db, incident: Incident, summary: str) -> None:
    incident.status = "resolved"
    incident.resolved_at = datetime.utcnow()
    db.add(IncidentEvent(incident_id=incident.id, event_type="resolved", summary=summary))
    _queue(db, Notification(f"✅ Resolved: {incident.title}", summary, "info", incident.id))


async def _open(db, rule: str, key: str, exact: bool = False) -> list[Incident]:
    match = Incident.host_ids_hash == key if exact else Incident.host_ids_hash.like(f"{key}%")
    return list((await db.execute(
        select(Incident).where(
            Incident.rule == rule, match, Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalars().all())


async def evaluate_alerts(db, cfg: IntegrationConfig, instance, data) -> None:
    """Open, update and resolve the alert incidents of one successful snapshot."""
    from services import integration_thresholds

    if instance is None or not isinstance(data, dict):
        return
    instance.threshold_overrides = await integration_thresholds.overrides_for(db, cfg.type)
    alerts = [a for a in instance.parse_alerts(data) if a.severity in _INCIDENT_SEVERITIES]
    tenant = cfg.tenant_id
    prefix = alert_prefix(cfg)

    current: dict[str, object] = {}
    for alert in alerts:
        current.setdefault(alert_key(cfg, alert), alert)

    # Streaks of alerts that are gone end here.
    for k in [k for k in _streaks if k[0] == tenant and k[1].startswith(prefix)]:
        if k[1] not in current:
            del _streaks[k]

    open_keys = {i.host_ids_hash: i for i in await _open(db, ALERT_RULE, prefix)}
    index = None
    own_host = None
    for key, alert in current.items():
        streak = _streaks[(tenant, key)] = _streaks.get((tenant, key), 0) + 1
        if key not in open_keys and streak < ALERT_MIN_CYCLES:
            continue
        if index is None:
            index = await _host_index(db)
            own_host = index.find(endpoint_of(getattr(instance, "config", None)))
        device = index.find(alert.host) if alert.host else None
        hosts = [h for h in (device or own_host,) if h is not None]
        if key not in open_keys and await _in_maintenance(db, [device, own_host]):
            continue
        await _open_or_extend(
            db, rule=ALERT_RULE, title=_alert_title(cfg, alert), severity=alert.severity,
            event_type="integration_alert", summary=alert.detail or alert.title, key=key,
            host_ids=[h.id for h in hosts],
        )

    for key, incident in open_keys.items():
        if key not in current:
            await _resolve(db, incident, f"Resolved: {cfg.name} no longer reports this")


async def record_failure(db, cfg: IntegrationConfig, error: str | None, config: dict | None = None) -> None:
    """Open (or extend) the failure incident once the integration has been
    failing long enough. Called after the failed snapshot was added to ``db``."""
    await db.flush()
    recent = (await db.execute(
        select(Snapshot.ok, Snapshot.timestamp)
        .where(Snapshot.entity_type == cfg.type, Snapshot.entity_id == cfg.id)
        .order_by(Snapshot.id.desc()).limit(FAILED_MIN_SNAPSHOTS)
    )).all()
    if len(recent) < FAILED_MIN_SNAPSHOTS or any(ok for ok, _ in recent):
        return
    last_ok = (await db.execute(
        select(Snapshot.timestamp)
        .where(Snapshot.entity_type == cfg.type, Snapshot.entity_id == cfg.id, Snapshot.ok == True)  # noqa: E712
        .order_by(Snapshot.id.desc()).limit(1)
    )).scalar_one_or_none()
    if last_ok is not None and datetime.utcnow() - last_ok < timedelta(minutes=FAILED_MIN_MINUTES):
        return

    key = failed_key(cfg)
    host = (await _host_index(db)).find(endpoint_of(config))
    if not await _open(db, FAILED_RULE, key, exact=True) and await _in_maintenance(db, [host]):
        return
    await _open_or_extend(
        db, rule=FAILED_RULE, title=f"{cfg.name}: integration cannot collect"[:256],
        severity="warning", event_type="integration_error",
        summary=f"{cfg.type} integration '{cfg.name}' failed: {error or 'unknown error'}",
        key=key, host_ids=[host.id] if host is not None else [],
    )


async def record_success(db, cfg: IntegrationConfig) -> None:
    """Resolve the failure incident of a config that collects again."""
    for incident in await _open(db, FAILED_RULE, failed_key(cfg), exact=True):
        await _resolve(db, incident, f"Resolved: {cfg.name} collects again")


async def open_incidents_for(db, cfg: IntegrationConfig) -> list[Incident]:
    """The open alert and failure incidents of one config (for its page)."""
    return (await _open(db, ALERT_RULE, alert_prefix(cfg))
            + await _open(db, FAILED_RULE, failed_key(cfg), exact=True))


def _belongs(cfg: IntegrationConfig, incident: Incident) -> bool:
    key = incident.host_ids_hash or ""
    if incident.rule == FAILED_RULE:
        return key == failed_key(cfg)
    return incident.rule == ALERT_RULE and key.startswith(alert_prefix(cfg))


async def open_counts(db, configs) -> dict[int, int]:
    """Open integration incidents per config id, with one query."""
    incidents = (await db.execute(
        select(Incident).where(Incident.rule.in_(RULES), Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()
    return {c.id: sum(1 for i in incidents if _belongs(c, i)) for c in configs}


async def resolve_orphans(db) -> int:
    """Resolve integration incidents whose config was deleted or disabled."""
    configs = (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)  # noqa: E712
    )).scalars().all()
    live = {alert_prefix(c) for c in configs} | {failed_key(c) for c in configs}

    resolved = 0
    for rule in RULES:
        for incident in await _open(db, rule, ""):
            key = incident.host_ids_hash or ""
            prefix = key if rule == FAILED_RULE else key.rsplit(":", 1)[0] + ":"
            if prefix not in live:
                await _resolve(db, incident, "Resolved: the integration was removed or disabled")
                resolved += 1
    return resolved

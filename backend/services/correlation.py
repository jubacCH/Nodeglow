"""
Correlation engine – runs periodically (60s) to detect and group related issues
into Incidents. Supports deduplication and auto-resolve.

Rules:
1. host_down_syslog  – Host offline + syslog errors from same host (5min window)
2. multi_host_down   – 3+ hosts offline simultaneously → network problem
3. integration_host  – Integration unreachable + associated host offline
4. syslog_spike      – Syslog error rate 5x above baseline
5. log_anomaly       – Per-host log volume > baseline + 3σ
6. port_error        – Host online (ICMP OK) but service check (HTTP/HTTPS/TCP) failed
7. fleet_wide_issue  – Same template on 3+ hosts simultaneously
8. severity_trend    – Error template with rising frequency trend
9. content_anomaly   – New templates on stable host / severity upgrade
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from models.base import AsyncSessionLocal
from models.ping import PingHost
from services import clickhouse_client as _ch
from services.clickhouse_client import query as ch_query
from services.clickhouse_client import query_scalar as ch_scalar
from services.maintenance import without_maintenance
from models.integration import IntegrationConfig
from models.incident import Incident, IncidentEvent
from models.log_template import HostBaseline, LogTemplate, PrecursorPattern
from services.predictor_config import (
    get_blacklist_regexes,
    get_min_confidence,
    get_min_occurrences,
    is_template_blacklisted,
)
from services.topology import build_topology, filter_upstream_failures

log = logging.getLogger("nodeglow.correlation")

# ── Consecutive-failure state (in-memory, resets on restart) ────────────────
# Key = (rule_name, host_ids_hash), value = consecutive cycle count
_rule_hit_counts: dict[tuple[str, str], int] = {}
# Tracks which keys were seen in the current correlation cycle
_current_cycle_hits: set[tuple[str, str]] = set()


def _host_ids_hash(host_ids: list[int]) -> str:
    """Deterministic hash of sorted host IDs for dedup."""
    return hashlib.sha256(",".join(str(i) for i in sorted(host_ids)).encode()).hexdigest()[:16]


def _track_rule_hit(rule: str, host_ids: list[int], min_cycles: int) -> bool:
    """Track consecutive correlation cycles where a rule matches.

    Returns True if the rule has matched for >= min_cycles consecutive runs.
    Counters for combos not seen in a cycle are reset automatically via
    _prune_stale_hits() at end of each correlation run.
    """
    key = (rule, _host_ids_hash(host_ids))
    _current_cycle_hits.add(key)
    _rule_hit_counts[key] = _rule_hit_counts.get(key, 0) + 1
    return _rule_hit_counts[key] >= min_cycles


def _prune_stale_hits():
    """Remove hit counters for combos that didn't match this cycle."""
    stale = [k for k in _rule_hit_counts if k not in _current_cycle_hits]
    for k in stale:
        del _rule_hit_counts[k]
    _current_cycle_hits.clear()


async def cleanup_incident_events(db, retention_days: int) -> int:
    """Delete incident events older than retention_days.

    Open incidents accumulate one event per correlation cycle, so this table
    grows without bound. The newest meaningful (non-ack/resolve) event per
    incident is always kept — it backs the summary shown in the incident list.
    A retention of 0 disables pruning. Returns the number of deleted rows.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    keep_latest = (
        select(func.max(IncidentEvent.id))
        .where(IncidentEvent.event_type.notin_(["acknowledged", "resolved"]))
        .group_by(IncidentEvent.incident_id)
        .scalar_subquery()
    )
    result = await db.execute(
        delete(IncidentEvent).where(
            IncidentEvent.timestamp < cutoff,
            IncidentEvent.id.notin_(keep_latest),
        )
    )
    return result.rowcount or 0


async def _find_or_create_incident(
    db, rule: str, title: str, severity: str,
    host_ids: list[int], event_type: str, summary: str, detail: str = None,
) -> Incident:
    """Find existing open incident for this rule+hosts combo, or create new one."""
    h = _host_ids_hash(host_ids)

    existing = (await db.execute(
        select(Incident).where(
            Incident.rule == rule,
            Incident.host_ids_hash == h,
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalar_one_or_none()

    if existing:
        # Append event to existing incident
        existing.updated_at = datetime.utcnow()
        db.add(IncidentEvent(
            incident_id=existing.id,
            event_type=event_type,
            summary=summary,
            detail=detail,
        ))
        return existing

    # Create new incident
    incident = Incident(
        rule=rule,
        title=title,
        severity=severity,
        host_ids_hash=h,
    )
    db.add(incident)
    await db.flush()

    db.add(IncidentEvent(
        incident_id=incident.id,
        event_type="created",
        summary=summary,
        detail=detail,
    ))

    # Send notification for new incidents
    try:
        from notifications import notify
        await notify(
            f"🔴 Incident: {title}",
            summary,
            severity=severity,
        )
    except Exception as exc:
        log.warning("Failed to send incident notification: %s", exc)

    return incident


async def _get_offline_hosts(db, min_failures: int = 3) -> list[PingHost]:
    """Get hosts that are offline: last *min_failures* consecutive pings all failed.

    Single ClickHouse query — no per-host N+1.
    """
    from services.clickhouse_client import get_offline_hosts_since

    hosts_q = await db.execute(
        select(PingHost).where(PingHost.enabled == True)
    )
    hosts = await without_maintenance(db, list(hosts_q.scalars().all()))
    if not hosts:
        return []

    offline_ids = set(await get_offline_hosts_since(
        [h.id for h in hosts], min_failures=min_failures,
    ))
    return [h for h in hosts if h.id in offline_ids]


# ── Topology cache (refreshed once per correlation run) ────────────────────

_topo_cache: dict[int, int | None] = {}
_topo_cache_ts: datetime | None = None


async def _get_topology(db) -> dict[int, int | None]:
    """Get topology with 60s cache."""
    global _topo_cache, _topo_cache_ts
    now = datetime.utcnow()
    if _topo_cache_ts and (now - _topo_cache_ts).total_seconds() < 60:
        return _topo_cache
    try:
        _topo_cache = await build_topology(db)
        _topo_cache_ts = now
    except Exception:
        log.debug("Failed to build topology", exc_info=True)
    return _topo_cache


# ── Rule 1: Host Down + Syslog Errors ───────────────────────────────────────

async def _rule_host_down_syslog(
    db, min_failures: int = 3, min_cycles: int = 2,
    offline_hosts: list[PingHost] | None = None,
):
    """Host offline AND syslog severity <= 3 from same host in 5min window.
    Skips hosts whose upstream parent is also offline (topology cascading)."""
    if offline_hosts is None:
        offline_hosts = await _get_offline_hosts(db, min_failures)
    if not offline_hosts:
        return

    # Filter out cascaded failures
    topology = await _get_topology(db)
    offline_ids = {h.id for h in offline_hosts}
    primary_ids, cascaded_ids = filter_upstream_failures(offline_ids, topology)

    window = datetime.utcnow() - timedelta(minutes=5)

    # Error-level syslog counts for every candidate in one grouped query
    # instead of one count() per offline host.
    candidates = [h for h in offline_hosts if h.id not in cascaded_ids]
    error_counts = await _ch.count_syslog_by_host(
        [h.id for h in candidates], window, max_severity=3,
    ) if candidates else {}

    for host in candidates:
        syslog_count = error_counts.get(host.id, 0)

        if syslog_count > 0:
            if not _track_rule_hit("host_down_syslog", [host.id], min_cycles):
                continue
            await _find_or_create_incident(
                db,
                rule="host_down_syslog",
                title=f"{host.name} offline with syslog errors",
                severity="critical",
                host_ids=[host.id],
                event_type="host_down",
                summary=f"{host.name} ({host.hostname}) is offline with {syslog_count} syslog errors in the last 5min",
            )

    # Create a single upstream-failure incident if cascaded hosts exist
    if cascaded_ids:
        # Find the upstream root causes
        cascade_hosts = [h for h in offline_hosts if h.id in cascaded_ids]
        parent_names = set()
        for hid in cascaded_ids:
            from services.topology import get_ancestors
            ancestors = get_ancestors(topology, hid)
            for a in ancestors:
                if a in primary_ids:
                    ph = next((h for h in offline_hosts if h.id == a), None)
                    if ph:
                        parent_names.add(ph.name)
                    break

        names = ", ".join(h.name for h in cascade_hosts[:5])
        if len(cascade_hosts) > 5:
            names += f" (+{len(cascade_hosts) - 5} more)"
        upstream_label = ", ".join(parent_names) if parent_names else "upstream device"

        if not _track_rule_hit("upstream_failure", list(cascaded_ids), min_cycles):
            return
        await _find_or_create_incident(
            db,
            rule="upstream_failure",
            title=f"Upstream failure: {len(cascaded_ids)} hosts affected",
            severity="warning",
            host_ids=list(cascaded_ids),
            event_type="host_down",
            summary=f"{len(cascaded_ids)} hosts offline due to upstream failure ({upstream_label}): {names}",
        )


# ── Rule 2: Multi-Host Down ─────────────────────────────────────────────────

async def _rule_multi_host_down(
    db, min_failures: int = 3, min_cycles: int = 2,
    offline_hosts: list[PingHost] | None = None,
):
    """3+ hosts offline simultaneously → likely network problem.
    Excludes hosts already explained by upstream failure."""
    if offline_hosts is None:
        offline_hosts = await _get_offline_hosts(db, min_failures)
    if len(offline_hosts) < 3:
        return

    # Filter out cascaded failures (already handled by rule 1)
    topology = await _get_topology(db)
    offline_ids = {h.id for h in offline_hosts}
    primary_ids, _ = filter_upstream_failures(offline_ids, topology)
    primary_hosts = [h for h in offline_hosts if h.id in primary_ids]

    if len(primary_hosts) < 3:
        return

    # Group by /24 subnet (simple heuristic)
    subnets: dict[str, list[PingHost]] = {}
    for host in primary_hosts:
        hostname = host.hostname.strip()
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            subnet = ".".join(parts[:3]) + ".0/24"
        else:
            subnet = "unknown"
        subnets.setdefault(subnet, []).append(host)

    for subnet, hosts in subnets.items():
        if len(hosts) >= 3:
            host_ids = [h.id for h in hosts]
            if not _track_rule_hit("multi_host_down", host_ids, min_cycles):
                continue
            names = ", ".join(h.name for h in hosts[:5])
            if len(hosts) > 5:
                names += f" (+{len(hosts) - 5} more)"
            await _find_or_create_incident(
                db,
                rule="multi_host_down",
                title=f"Network issue: {len(hosts)} hosts down in {subnet}",
                severity="critical",
                host_ids=host_ids,
                event_type="host_down",
                summary=f"{len(hosts)} hosts offline in {subnet}: {names}",
            )

    # Also trigger if 3+ hosts down across all subnets (no single subnet has 3+)
    already_covered = sum(len(h) for h in subnets.values() if len(h) >= 3)
    remaining = len(primary_hosts) - already_covered
    if remaining >= 3:
        uncovered = [h for s, hosts in subnets.items() if len(hosts) < 3 for h in hosts]
        host_ids = [h.id for h in uncovered]
        if not _track_rule_hit("multi_host_down", host_ids, min_cycles):
            return
        names = ", ".join(h.name for h in uncovered[:5])
        if len(uncovered) > 5:
            names += f" (+{len(uncovered) - 5} more)"
        await _find_or_create_incident(
            db,
            rule="multi_host_down",
            title=f"Multiple hosts down ({len(uncovered)} across subnets)",
            severity="warning",
            host_ids=host_ids,
            event_type="host_down",
            summary=f"{len(uncovered)} hosts offline across multiple subnets: {names}",
        )


# ── Rule 3: Integration + Host ──────────────────────────────────────────────

async def _rule_integration_host(
    db, min_failures: int = 3, min_cycles: int = 2,
    offline_hosts: list[PingHost] | None = None,
):
    """Integration unreachable AND the host running it is also offline."""
    from services import snapshot as snap_svc

    if offline_hosts is None:
        offline_hosts = await _get_offline_hosts(db, min_failures)
    if not offline_hosts:
        return

    offline_hostnames = {h.hostname.lower().strip() for h in offline_hosts}
    offline_by_hostname: dict[str, PingHost] = {h.hostname.lower().strip(): h for h in offline_hosts}

    # Get all integration configs
    configs = (await db.execute(select(IntegrationConfig).where(IntegrationConfig.enabled == True))).scalars().all()
    all_snaps = await snap_svc.get_latest_batch_all(db)

    for cfg in configs:
        snap = all_snaps.get(cfg.type, {}).get(cfg.id)
        if not snap or snap.ok:
            continue

        # Try to extract host from config
        try:
            from services.integration import decrypt_config
            config_dict = decrypt_config(cfg.config_json)
            cfg_host = (config_dict.get("host") or "").lower().strip()
            # Strip protocol and port
            cfg_host = cfg_host.replace("https://", "").replace("http://", "").split(":")[0].split("/")[0]
        except Exception:
            continue

        if cfg_host and cfg_host in offline_hostnames:
            ping_host = offline_by_hostname[cfg_host]
            if not _track_rule_hit("integration_host", [ping_host.id], min_cycles):
                continue
            await _find_or_create_incident(
                db,
                rule="integration_host",
                title=f"{cfg.name} unreachable – host {ping_host.name} offline",
                severity="warning",
                host_ids=[ping_host.id],
                event_type="integration_error",
                summary=f"Integration '{cfg.name}' ({cfg.type}) is unreachable and its host {ping_host.name} ({cfg_host}) is also offline",
            )


# ── Rule 6: Port Error ─────────────────────────────────────────────────────

async def _rule_port_error(db, min_cycles: int = 2):
    """Host is online (ICMP OK) but a service check (HTTP/HTTPS/TCP) failed."""
    results = await db.execute(
        select(PingHost).where(
            PingHost.enabled == True,
            PingHost.port_error == True,
        )
    )
    hosts = await without_maintenance(db, list(results.scalars().all()))
    if not hosts:
        return

    for host in hosts:
        # Parse check_detail to find which checks failed
        failed_checks = []
        if host.check_detail:
            try:
                detail = json.loads(host.check_detail)
                failed_checks = [k.upper() for k, v in detail.items() if not v]
            except Exception:
                pass

        reasons: dict = {}
        if getattr(host, "check_errors", None):
            try:
                reasons = {k.upper(): v for k, v in json.loads(host.check_errors).items()}
            except Exception:
                reasons = {}
        failed_label = ", ".join(failed_checks) if failed_checks else "service check"
        why = "; ".join(f"{k}: {reasons[k]}" for k in failed_checks if reasons.get(k))
        if not _track_rule_hit("port_error", [host.id], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="port_error",
            title=f"{host.name}: {failed_label} failed",
            severity="warning",
            host_ids=[host.id],
            event_type="port_error",
            summary=(
                f"{host.name} ({host.hostname}) is online but {failed_label} failed ({why})"
                if why else
                f"{host.name} ({host.hostname}) is online but {failed_label} is unreachable"
            ),
        )


# ── Rule 4: Syslog Spike ────────────────────────────────────────────────────

async def _rule_syslog_spike(db, min_cycles: int = 2):
    """Syslog error rate 5x above 1h baseline."""
    now = datetime.utcnow()
    window_5m = now - timedelta(minutes=5)
    window_1h = now - timedelta(hours=1)

    # Count errors (severity <= 3) in last 5min
    recent_errors = int(await ch_scalar(
        "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
        {"t": window_5m},
    ) or 0)

    if recent_errors < 10:  # minimum threshold
        return

    # Count errors in last hour (baseline)
    hourly_errors = int(await ch_scalar(
        "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
        {"t": window_1h},
    ) or 0)

    # Expected 5min rate, computed over the prior 55min (the 11 five-minute
    # buckets EXCLUDING the recent 5min). Including the recent window would let
    # the current spike inflate its own baseline and suppress detection.
    prior_errors = max(0, hourly_errors - recent_errors)
    baseline_5m = max(1, prior_errors / 11)

    if recent_errors >= baseline_5m * 5:
        if not _track_rule_hit("syslog_spike", [0], min_cycles):
            return
        await _find_or_create_incident(
            db,
            rule="syslog_spike",
            title=f"Syslog error spike: {recent_errors} errors in 5min",
            severity="warning",
            host_ids=[0],  # no specific host
            event_type="syslog_error",
            summary=f"{recent_errors} syslog errors in last 5min (baseline: ~{int(baseline_5m)}/5min)",
        )


# ── Rule 5: Log Anomaly ────────────────────────────────────────────────

async def _rule_log_anomaly(db, min_cycles: int = 2):
    """Detect per-host log volume anomalies vs. learned baselines.

    Baselines come from load_effective_baselines: the weekday slot once it
    has enough samples, the hour-of-day slot until then. Current volume is
    counted for all candidates in one grouped query per key type, not one
    query per baseline row.
    """
    from services.log_intelligence import load_effective_baselines

    now = datetime.utcnow()
    baselines = [
        b for b in (await load_effective_baselines(db, now)).values()
        if (b.avg_rate or 0) > 0
    ]
    if not baselines:
        return

    window_10m = now - timedelta(minutes=10)

    by_source: dict[str, HostBaseline] = {}
    by_host_id: dict[int, HostBaseline] = {}
    for bl in baselines:
        if bl.host_key.startswith("host:"):
            part = bl.host_key.split(":", 1)[1]
            if part.isdigit():
                by_host_id[int(part)] = bl
        else:
            by_source[bl.host_key] = bl

    source_counts = await _ch.count_syslog_received_by_source(
        window_10m, list(by_source),
    ) if by_source else {}
    host_counts = await _ch.count_syslog_received_by_host(
        window_10m, list(by_host_id),
    ) if by_host_id else {}

    candidates: list[tuple[HostBaseline, int, int | None]] = [
        (bl, source_counts.get(ip, 0), None) for ip, bl in by_source.items()
    ] + [
        (bl, host_counts.get(hid, 0), hid) for hid, bl in by_host_id.items()
    ]

    hosts_by_id: dict[int, PingHost] = {}
    if by_host_id:
        hosts_by_id = {h.id: h for h in (await db.execute(
            select(PingHost).where(PingHost.id.in_(list(by_host_id)))
        )).scalars().all()}

    for bl, count, host_id in candidates:
        current_rate = count * 6  # extrapolate 10min → 1hr
        threshold = bl.avg_rate + 3 * max(bl.std_rate or 0.0, bl.avg_rate * 0.3)
        if not (current_rate > threshold and count >= 20):
            continue

        # Per-source discriminator: every source-keyed baseline maps to host 0,
        # so a shared key let two anomalous sources reach min_cycles within a
        # single cycle.
        if not _track_rule_hit(f"log_anomaly_{bl.host_key}", [host_id or 0], min_cycles):
            continue

        host = hosts_by_id.get(host_id) if host_id is not None else None
        host_label = host.name if host else bl.host_key
        host_ids = [host.id] if host else [0]

        await _find_or_create_incident(
            db,
            rule="log_anomaly",
            title=f"Log volume anomaly: {host_label}",
            severity="warning",
            host_ids=host_ids,
            event_type="syslog_error",
            summary=f"{host_label}: {current_rate}/hr (baseline: {int(bl.avg_rate)}/hr ± {int(bl.std_rate or 0)})",
            detail=f"{count} messages in last 10min, expected ~{int(bl.avg_rate / 6)}",
        )


# ── Rule 7: Fleet-Wide Issue ───────────────────────────────────────────────

async def _rule_fleet_wide(db, min_cycles: int = 2):
    """Detect same template appearing on 3+ hosts simultaneously."""
    from services.log_intelligence import detect_fleet_patterns
    from models.log_template import LogTemplate

    fleet_issues = await detect_fleet_patterns(db)
    if not fleet_issues:
        return

    for issue in fleet_issues:
        th = issue["template_hash"]
        host_count = issue["host_count"]

        # Get template text for the incident title
        tpl = (await db.execute(
            select(LogTemplate.template).where(LogTemplate.template_hash == th)
        )).scalar()
        tpl_text = (tpl or th)[:80]

        severity = "critical" if host_count > 5 else "warning"
        # Per-template discriminator so each template tracks its own consecutive
        # count — sharing one key would let min_cycles be reached within a single
        # cycle when multiple templates match.
        if not _track_rule_hit(f"fleet_wide_issue_{th[:8]}", [0], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="fleet_wide_issue",
            title=f"Fleet-wide: {tpl_text} ({host_count} hosts)",
            severity=severity,
            host_ids=[0],
            event_type="fleet_pattern",
            summary=f"Template detected on {host_count} hosts simultaneously: {tpl_text}",
            detail=json.dumps({"hosts": issue.get("hosts", [])[:10]}),
        )


# ── Rule 8: Severity Trend ────────────────────────────────────────────────

async def _rule_severity_trend(db, min_cycles: int = 2):
    """Rising error templates create warning incidents."""
    from models.log_template import LogTemplate

    rising = (await db.execute(
        select(LogTemplate).where(
            LogTemplate.trend_direction == "rising",
            LogTemplate.trend_score > 0.1,
            LogTemplate.severity_mode.isnot(None),
            LogTemplate.severity_mode <= 3,
        )
    )).scalars().all()

    for tpl in rising:
        # Per-template discriminator so each rising template tracks its own
        # consecutive count rather than sharing one key.
        if not _track_rule_hit(f"severity_trend_{tpl.template_hash[:8]}", [0], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="severity_trend",
            title=f"Rising error trend: {tpl.template[:60]}",
            severity="warning",
            host_ids=[0],
            event_type="syslog_trend",
            summary=f"Template is trending up (+{round(tpl.trend_score * 100)}%/hr): {tpl.template[:100]}",
        )


# ── Rule 9: Content Anomaly ──────────────────────────────────────────────

async def _rule_content_anomaly(db, min_cycles: int = 2):
    """Detect new templates on stable hosts and severity upgrades."""
    from services.log_intelligence import detect_content_anomalies

    anomalies = await detect_content_anomalies(db)
    for anomaly in anomalies:
        if anomaly["type"] == "template_diversity_spike":
            # Per-source discriminator so each source_ip tracks its own
            # consecutive count rather than collapsing into one shared key.
            if not _track_rule_hit(
                f"content_anomaly_diversity_{anomaly['source_ip']}", [0], min_cycles
            ):
                continue
            await _find_or_create_incident(
                db,
                rule="content_anomaly",
                title=f"Template diversity spike: {anomaly['source_ip']}",
                severity="warning",
                host_ids=[0],
                event_type="content_anomaly",
                summary=f"{anomaly['source_ip']}: {anomaly['current']} distinct templates "
                        f"(baseline: {anomaly['baseline']})",
            )
        elif anomaly["type"] == "severity_upgrade":
            # Per-template discriminator so each upgraded template tracks its own
            # consecutive count rather than collapsing into one shared key.
            _sev_th = anomaly.get("template_hash", "")[:8]
            if not _track_rule_hit(
                f"content_anomaly_severity_{_sev_th}", [0], min_cycles
            ):
                continue
            await _find_or_create_incident(
                db,
                rule="content_anomaly",
                title=f"Severity upgrade: {anomaly.get('template', '')[:60]}",
                severity="warning",
                host_ids=[0],
                event_type="severity_upgrade",
                summary=f"Template normally at severity {anomaly.get('normal_severity')} "
                        f"now at severity {anomaly.get('current_severity')} "
                        f"({anomaly.get('count')} occurrences)",
            )


# ── Rule 10: Learned Precursor Pattern Observed (predictive) ────────────────

async def _rule_precursor_observed(
    db,
    min_confidence: float | None = None,
    min_occurrences: int | None = None,
    min_cycles: int = 2,
):
    """Predict incidents from learned precursor patterns.

    Thresholds default to the user-configurable settings
    ``predictor_min_confidence`` / ``predictor_min_occurrences`` when the
    caller does not override them.  Templates matching
    ``predictor_template_blacklist`` never fire.
    """
    if min_confidence is None:
        min_confidence = await get_min_confidence(db)
    if min_occurrences is None:
        min_occurrences = await get_min_occurrences(db)
    blacklist = await get_blacklist_regexes(db)

    # Hashes of templates seen in the last 2 minutes with their associated host_id
    window = datetime.utcnow() - timedelta(minutes=2)
    try:
        rows = await ch_query(
            "SELECT DISTINCT template_hash, host_id "
            "FROM syslog_messages "
            "WHERE timestamp >= {since:DateTime64(3)} "
            "  AND template_hash != '' "
            "LIMIT 1000",
            {"since": window},
        )
    except Exception as exc:
        log.debug("Precursor rule: CH query failed: %s", exc)
        return

    if not rows:
        return

    # Pull all high-confidence precursors in one query so we can join in Python
    precursor_q = (
        select(PrecursorPattern, LogTemplate.template_hash, LogTemplate.template)
        .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
        .where(
            PrecursorPattern.confidence >= min_confidence,
            PrecursorPattern.occurrence_count >= min_occurrences,
        )
    )
    precursor_rows = (await db.execute(precursor_q)).all()
    if not precursor_rows:
        return

    # Index by template_hash for fast lookup; skip blacklisted templates here
    by_hash: dict[str, list[tuple]] = {}
    for pp, tpl_hash, tpl_text in precursor_rows:
        if is_template_blacklisted(tpl_text or "", blacklist):
            continue  # defense-in-depth – ignore stale blacklisted rows
        by_hash.setdefault(tpl_hash, []).append((pp, tpl_text))

    # Match observed templates against the index
    for row in rows:
        observed_hash = row.get("template_hash")
        if not observed_hash:
            continue
        host_id = row.get("host_id")

        for pp, tpl_text in by_hash.get(observed_hash, []):
            # De-dup: don't fire repeatedly for the same template+host
            dedup_id = host_id if host_id is not None else 0
            if not _track_rule_hit(
                f"precursor_{pp.precedes_event}_{observed_hash[:8]}",
                [dedup_id],
                min_cycles,
            ):
                continue

            confidence_pct = round(pp.confidence * 100)
            lead_min = round((pp.avg_lead_time_sec or 0) / 60, 1) if pp.avg_lead_time_sec else None
            lead_str = f"~{lead_min}min lead time" if lead_min else "unknown lead time"
            event_label = pp.precedes_event.replace("_", " ").title()
            tpl_preview = (tpl_text or "")[:80]

            # When there is no host, host_ids=[] hashes to a constant value, so
            # distinct predicted-event types would collapse into ONE incident.
            # Make the rule name distinct per precedes_event to keep them apart.
            # _auto_resolve matches rule names with startswith("learned_precursor").
            incident_rule = (
                "learned_precursor"
                if dedup_id
                else f"learned_precursor_{pp.precedes_event}"
            )
            incident = await _find_or_create_incident(
                db,
                rule=incident_rule,
                title=f"Predicted: {event_label} ({confidence_pct}% confidence)",
                severity="warning",
                host_ids=[dedup_id] if dedup_id else [],
                event_type="predicted_incident",
                summary=(
                    f"Learned precursor template fired: \"{tpl_preview}\" — "
                    f"{event_label} typically follows in {lead_str} "
                    f"({pp.occurrence_count} historical observations, "
                    f"{confidence_pct}% confidence)."
                ),
            )
            # Persist the matched template text so 'noise' feedback can map back
            # to it and blacklist the noisy pattern.
            if incident is not None and tpl_text and not incident.precursor_template:
                incident.precursor_template = tpl_text


# ── Auto-Resolve ────────────────────────────────────────────────────────────

async def _auto_resolve(db, offline_hosts: list[PingHost] | None = None) -> list[int]:
    """Auto-resolve incidents where all affected hosts are back online.

    Returns the ids of incidents that were resolved and should get a
    post-mortem. The caller spawns the post-mortem tasks AFTER the surrounding
    transaction commits — spawning them here would let the background task read
    the incident before commit (or after a rollback) from a fresh session.
    """
    postmortem_ids: list[int] = []
    open_incidents = (await db.execute(
        select(Incident).where(Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()

    if not open_incidents:
        return postmortem_ids

    # Get current offline host IDs (computed once per cycle by the caller)
    if offline_hosts is None:
        offline_hosts = await _get_offline_hosts(db)
    offline_ids = {h.id for h in offline_hosts}

    # Looked up lazily, once per call, and only if an incident needs them —
    # these used to be one query per incident (and per offline host).
    syslog_error_counts: dict[int, int] | None = None
    port_error_hashes: set[str] | None = None

    for incident in open_incidents:
        # Skip syslog/fleet/trend/content/precursor rules – auto-resolve after timeout
        if incident.rule in ("syslog_spike", "log_anomaly", "fleet_wide_issue",
                             "severity_trend", "content_anomaly") \
                or incident.rule.startswith("learned_precursor") \
                or incident.rule.startswith("alert_rule_"):
            # Resolve if last update was > 10min ago (no new activity)
            if incident.updated_at < datetime.utcnow() - timedelta(minutes=10):
                incident.status = "resolved"
                incident.resolved_at = datetime.utcnow()
                db.add(IncidentEvent(
                    incident_id=incident.id,
                    event_type="resolved",
                    summary="Auto-resolved: error rate returned to normal",
                ))
                try:
                    from notifications import notify
                    await notify(
                        f"✅ Resolved: {incident.title}",
                        "Auto-resolved: error rate returned to normal",
                        severity="info",
                    )
                except Exception as exc:
                    log.warning("Failed to send resolve notification: %s", exc)
                postmortem_ids.append(incident.id)
            continue

        # Self-check incidents describe Nodeglow itself and carry no hosts, so
        # the "are the hosts back?" logic below trivially resolves them. Only
        # run_self_check knows whether the condition still holds; it clears them
        # itself. Without this they were recreated every 5 minutes and resolved
        # 60 seconds later, flapping indefinitely.
        if incident.rule == "self_check":
            continue

        if not incident.host_ids_hash:
            continue

        # Check if ALL hosts from the original hash are back online
        # We find incidents by their hash, so we need to check current offline hosts
        # against what created this incident. Since we can't reverse the hash,
        # we check: if no offline hosts match this rule anymore, resolve it.
        should_resolve = True

        if incident.rule == "host_down_syslog":
            # If any offline host still has syslog errors, keep open
            if syslog_error_counts is None:
                window = datetime.utcnow() - timedelta(minutes=5)
                syslog_error_counts = await _ch.count_syslog_by_host(
                    [h.id for h in offline_hosts], window, max_severity=3,
                ) if offline_hosts else {}
            for host in offline_hosts:
                h = _host_ids_hash([host.id])
                if h == incident.host_ids_hash:
                    if syslog_error_counts.get(host.id, 0) > 0:
                        should_resolve = False
                        break

        elif incident.rule == "multi_host_down":
            # Can't easily reverse the hash, so check if enough hosts recovered
            # Resolve if < 3 hosts offline now
            if len(offline_hosts) >= 3:
                should_resolve = False

        elif incident.rule == "integration_host":
            # If the host is still offline, keep open
            for host in offline_hosts:
                if _host_ids_hash([host.id]) == incident.host_ids_hash:
                    should_resolve = False
                    break

        elif incident.rule == "port_error":
            # Check if the host still has port_error
            if port_error_hashes is None:
                port_error_hashes = {
                    _host_ids_hash([hid]) for (hid,) in (await db.execute(
                        select(PingHost.id).where(
                            PingHost.enabled == True,
                            PingHost.port_error == True,
                        )
                    )).all()
                }
            if incident.host_ids_hash in port_error_hashes:
                should_resolve = False

        if should_resolve:
            incident.status = "resolved"
            incident.resolved_at = datetime.utcnow()
            db.add(IncidentEvent(
                incident_id=incident.id,
                event_type="resolved",
                summary="Auto-resolved: affected hosts are back online",
            ))
            try:
                from notifications import notify
                await notify(
                    f"✅ Resolved: {incident.title}",
                    "Auto-resolved: affected hosts are back online",
                    severity="info",
                )
            except Exception as exc:
                log.warning("Failed to send resolve notification: %s", exc)
            postmortem_ids.append(incident.id)

    return postmortem_ids


# ── Main entry point ────────────────────────────────────────────────────────

async def _load_correlation_settings(db) -> tuple[int, int]:
    from models.settings import get_setting
    try:
        min_failures = max(1, int(await get_setting(db, "correlation_min_failures", "3")))
    except (ValueError, TypeError):
        min_failures = 3
    try:
        min_cycles = max(1, int(await get_setting(db, "correlation_min_cycles", "2")))
    except (ValueError, TypeError):
        min_cycles = 2
    return min_failures, min_cycles


async def _run_rule(name: str, fn) -> bool:
    """Run one rule in its own session and transaction.

    A failing rule rolls back only its own work; the others still commit.
    Previously all rules shared one transaction, so a single bad query threw
    away every incident the cycle had found.
    """
    async with AsyncSessionLocal() as db:
        try:
            await fn(db)
            await db.commit()
            return True
        except Exception as e:
            log.error("Correlation rule %s failed: %s", name, e, exc_info=True)
            try:
                await db.rollback()
            except Exception:
                log.debug("Rollback after failed rule %s also failed", name, exc_info=True)
            return False


async def run_correlation():
    """Run all correlation rules. Called every 60s by scheduler."""
    try:
        async with AsyncSessionLocal() as db:
            min_failures, min_cycles = await _load_correlation_settings(db)
    except Exception as e:
        log.error("Correlation engine error: %s", e, exc_info=True)
        _current_cycle_hits.clear()
        return

    # Offline hosts once per cycle, shared by every rule that needs them. They
    # used to be recomputed — a full ClickHouse pass each time — by four
    # separate callers per cycle. The ORM rows are only read afterwards, so
    # they are safe to use across the per-rule sessions.
    offline_hosts: list[PingHost] | None
    try:
        async with AsyncSessionLocal() as db:
            offline_hosts = await _get_offline_hosts(db, min_failures)
    except Exception as e:
        log.error("Correlation engine: offline-host lookup failed: %s", e, exc_info=True)
        offline_hosts = None

    rules = [
        # (name, callable, needs offline hosts)
        ("host_down_syslog", lambda db: _rule_host_down_syslog(
            db, min_failures, min_cycles, offline_hosts=offline_hosts), True),
        ("multi_host_down", lambda db: _rule_multi_host_down(
            db, min_failures, min_cycles, offline_hosts=offline_hosts), True),
        ("integration_host", lambda db: _rule_integration_host(
            db, min_failures, min_cycles, offline_hosts=offline_hosts), True),
        ("port_error", lambda db: _rule_port_error(db, min_cycles), False),
        ("syslog_spike", lambda db: _rule_syslog_spike(db, min_cycles), False),
        ("log_anomaly", lambda db: _rule_log_anomaly(db, min_cycles), False),
        ("fleet_wide", lambda db: _rule_fleet_wide(db, min_cycles), False),
        ("severity_trend", lambda db: _rule_severity_trend(db, min_cycles), False),
        ("content_anomaly", lambda db: _rule_content_anomaly(db, min_cycles), False),
        ("precursor_observed", lambda db: _rule_precursor_observed(
            db, min_cycles=min_cycles), False),
    ]

    all_ok = offline_hosts is not None
    for name, fn, needs_offline in rules:
        if needs_offline and offline_hosts is None:
            continue
        if not await _run_rule(name, fn):
            all_ok = False

    # Auto-resolve with an unknown offline set would resolve every host-down
    # incident as "back online"; skip it rather than guess.
    postmortem_ids: list[int] = []
    if offline_hosts is not None:
        async with AsyncSessionLocal() as db:
            try:
                postmortem_ids = await _auto_resolve(db, offline_hosts=offline_hosts)
                await db.commit()
            except Exception as e:
                log.error("Correlation auto-resolve failed: %s", e, exc_info=True)
                await db.rollback()
                postmortem_ids = []
                all_ok = False

    if all_ok:
        _prune_stale_hits()
    else:
        # A partial cycle cannot tell "stopped matching" from "did not run";
        # keep the streak counters and let the next complete cycle prune.
        _current_cycle_hits.clear()

    # Spawn post-mortem tasks only after the transaction has committed, so the
    # background task (fresh session) reads incidents that are durably persisted.
    if postmortem_ids:
        try:
            from services.postmortem import generate_postmortem
            for _incident_id in postmortem_ids:
                asyncio.create_task(generate_postmortem(_incident_id))
        except Exception:
            log.warning("Failed to spawn post-mortem tasks", exc_info=True)

"""One answer to "what state is this host in" — for every list, badge and card.

Before this module the host list, the dashboard counters and the sidebar each
read the newest ``ping_checks`` row and called the host green when it said
``success``. Only the topology went through ``services.probes`` and knew that a
host behind a silent probe is not being observed. The result was the false
green the probe module was written to prevent: nine hosts behind a dead probe
rendered "up" everywhere except one page.

The rules, in order (first match wins):

``disabled``     monitoring is switched off for the host.
``maintenance``  manual flag or an active maintenance window
                 (``services.maintenance``) — wins over every check result.
``unknown``      nobody is currently observing the host:
                   * its probe is stale (``services.probes``), or
                   * there is no check result at all, or
                   * the newest result is older than N× the check interval
                     (probe: ``probes.result_window``; core: ``CORE_GRACE_FACTOR``
                     × ``ping_interval``, at least ``MIN_CORE_WINDOW_SECONDS``).
                 Never "up": no light = no data.
``down``         the newest check failed.
``warning``      the host answers, but a service check does not: ``port_error``,
                 ``check_errors`` from the last cycle, or an open incident of a
                 service rule (``agent_service``, ``port_error``) naming the host.
``degraded``     latency above the host's (or the global) threshold, or the
                 host is named in another open incident.
``up``           a fresh, successful check and nothing of the above.

``observed_at`` is the time of the newest real check (None if there is none).
``state_reason`` is a short sentence for a tooltip; ``reason_group`` is the
same fact without host-specific numbers, so the dashboard can say "7 hosts:
behind SW-ZH-CORE-02" instead of listing seven reasons.

:func:`evaluate` is pure; :func:`host_states` batches the inputs for a list of
hosts (one ClickHouse query, three small Postgres queries).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from services import probes as probe_svc
from services import maintenance as maint_svc

STATE_UP = "up"
STATE_DEGRADED = "degraded"
STATE_WARNING = "warning"
STATE_DOWN = "down"
STATE_UNKNOWN = "unknown"
STATE_MAINTENANCE = "maintenance"
STATE_DISABLED = "disabled"

# Severity order, worst first. Used for "worst state" of a group and sorting.
STATES = (
    STATE_DOWN, STATE_WARNING, STATE_DEGRADED, STATE_UNKNOWN,
    STATE_MAINTENANCE, STATE_UP, STATE_DISABLED,
)
_RANK = {s: i for i, s in enumerate(STATES)}

# States that rest on a fresh, real measurement.
OBSERVED_STATES = frozenset({STATE_UP, STATE_DEGRADED, STATE_WARNING, STATE_DOWN})

# Incident rules whose open incident means "a service on the host is failing".
SERVICE_RULES = frozenset({"agent_service", "port_error"})
# Rules that never mark a host degraded: predictions are not observations, and
# self-check incidents describe Nodeglow itself (a silent probe already turns
# its hosts unknown).
_NON_DEGRADING_PREFIXES = ("learned_precursor", "self_check")

CORE_GRACE_FACTOR = 3.0
MIN_CORE_WINDOW_SECONDS = 180
DEFAULT_PING_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class HostState:
    state: str
    reason: str
    observed_at: datetime | None = None
    reason_group: str | None = None

    def fields(self) -> dict:
        """The API fields every host endpoint adds."""
        return {
            "state": self.state,
            "state_reason": self.reason,
            "observed_at": iso(self.observed_at),
        }


def iso(value: datetime | None) -> str | None:
    """Naive UTC → ISO-8601 with Z, the format the rest of the API uses for UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None) - (value.utcoffset() or timedelta())
    return value.isoformat() + "Z"


def worst(states: Iterable[str]) -> str | None:
    """The most severe state of the given ones (None for an empty input)."""
    ranked = sorted(states, key=lambda s: _RANK.get(s, len(STATES)))
    return ranked[0] if ranked else None


def legacy_status(state: str) -> str:
    """The pre-existing ``status`` vocabulary (online/offline/…) for a state.

    Kept so existing consumers keep working; it is derived from the unified
    state, so it is probe-aware too — a host behind a silent probe now reads
    ``unknown`` here instead of ``online``.
    """
    if state in (STATE_UP, STATE_DEGRADED, STATE_WARNING):
        return "online"
    if state == STATE_DOWN:
        return "offline"
    return state  # unknown | maintenance | disabled


def core_window_seconds(ping_interval: int | None) -> float:
    interval = ping_interval or DEFAULT_PING_INTERVAL_SECONDS
    return max(MIN_CORE_WINDOW_SECONDS, interval * CORE_GRACE_FACTOR)


def _fmt_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _naive(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is not None:
        return ts.replace(tzinfo=None) - (ts.utcoffset() or timedelta())
    return ts


def _json_dict(raw) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def evaluate(
    host,
    latest: dict | None,
    now: datetime,
    *,
    maintenance: dict | None = None,
    probe: probe_svc.ProbeState | None = None,
    probe_missing: bool = False,
    ping_interval: int | None = None,
    global_latency_threshold: float | None = None,
    incidents: list[dict] | None = None,
    upstream_down: str | None = None,
) -> HostState:
    """The state of one host. Pure: every input is passed in.

    ``latest`` is the newest ping_checks row ({success, latency_ms, timestamp}).
    ``maintenance`` is :func:`services.maintenance.api_fields` for the host.
    ``probe`` is the probe the host is assigned to (None for core-checked);
    ``probe_missing`` marks a host whose probe_id points at no agent.
    ``incidents`` are the open incidents naming the host ({id, rule, title}).
    ``upstream_down`` names a down ancestor in the topology, for the reason.
    """
    latest = latest or {}
    observed_at = _naive(latest.get("timestamp"))
    success = latest.get("success")
    success = None if success is None else bool(success)
    now_ts = now.timestamp() if now.tzinfo else (now - datetime(1970, 1, 1)).total_seconds()
    age = (now - observed_at).total_seconds() if observed_at else None

    if not getattr(host, "enabled", True):
        return HostState(STATE_DISABLED, "Monitoring disabled", observed_at, "monitoring disabled")

    maint = maintenance or {}
    if maint.get("maintenance"):
        window = maint.get("maintenance_window")
        if window:
            ends = window.get("ends_at")
            until = f" until {ends[11:16]} UTC" if ends else ""
            name = window.get("name") or "maintenance window"
            return HostState(STATE_MAINTENANCE, f"Maintenance window '{name}'{until}",
                             observed_at, f"window {name}")
        until = getattr(host, "maintenance_until", None)
        text = f"Maintenance (manual) until {until.strftime('%Y-%m-%d %H:%M')} UTC" if until else "Maintenance (manual)"
        return HostState(STATE_MAINTENANCE, text, observed_at, "manual maintenance")

    # ── Who observes this host, and is that observation current? ────────────
    if getattr(host, "probe_id", None) is not None:
        if probe is None or probe_missing:
            return HostState(STATE_UNKNOWN, "Assigned probe no longer exists",
                             observed_at, "probe missing")
        if probe_svc.is_stale(probe, now_ts):
            window = probe_svc.staleness_window(probe.interval_seconds)
            if probe.last_report is None:
                return HostState(STATE_UNKNOWN, f"Probe {probe.name} has never reported",
                                 observed_at, f"probe {probe.name} never reported")
            silent = now_ts - probe.last_report
            return HostState(
                STATE_UNKNOWN,
                f"Probe {probe.name} silent {_fmt_age(silent)} (threshold {_fmt_age(window)})",
                observed_at, f"probe {probe.name} silent {_fmt_age(silent)}",
            )
        window = probe_svc.result_window(probe.interval_seconds)
        checker = f"probe {probe.name}"
    else:
        window = core_window_seconds(ping_interval)
        checker = "core"

    if success is None or observed_at is None:
        return HostState(STATE_UNKNOWN, "No check result yet", observed_at, "no check result yet")
    if age is not None and age > window:
        return HostState(
            STATE_UNKNOWN,
            f"No check result for {_fmt_age(age)} ({checker}, threshold {_fmt_age(window)})",
            observed_at, "no recent check result",
        )

    # ── Fresh result ─────────────────────────────────────────────────────────
    if not success:
        if upstream_down:
            return HostState(STATE_DOWN, f"Not responding, behind {upstream_down} (down)",
                             observed_at, f"behind {upstream_down}")
        return HostState(STATE_DOWN, "Last check failed", observed_at, "check failed")

    incidents = incidents or []
    errors = _json_dict(getattr(host, "check_errors", None))
    if errors:
        label, why = next(iter(sorted(errors.items())))
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return HostState(STATE_WARNING, f"{str(label).upper()} check: {why}{more}",
                         observed_at, f"{str(label).upper()} check failing")
    if getattr(host, "port_error", False):
        return HostState(STATE_WARNING, "Service check failed", observed_at, "service check failing")
    service_inc = next((i for i in incidents if i.get("rule") in SERVICE_RULES), None)
    if service_inc:
        return HostState(STATE_WARNING, service_inc.get("title") or "Service failing",
                         observed_at, "service failing")

    latency = latest.get("latency_ms")
    threshold = getattr(host, "latency_threshold_ms", None)
    if threshold is None:
        threshold = global_latency_threshold
    if latency is not None and threshold is not None and float(latency) > float(threshold):
        return HostState(STATE_DEGRADED, f"Latency {float(latency):.1f} ms > {float(threshold):g} ms",
                         observed_at, "latency over threshold")
    degrading = next(
        (i for i in incidents
         if i.get("rule") not in SERVICE_RULES
         and not str(i.get("rule") or "").startswith(_NON_DEGRADING_PREFIXES)),
        None,
    )
    if degrading:
        return HostState(STATE_DEGRADED, f"In incident #{degrading['id']}: {degrading.get('title') or ''}".strip(),
                         observed_at, f"incident #{degrading['id']}")

    lat = f" · {float(latency):.1f} ms" if latency is not None else ""
    return HostState(STATE_UP, f"Check OK{lat}", observed_at, None)


# ── Batch evaluation ─────────────────────────────────────────────────────────


def parse_host_ids(raw) -> list[int] | None:
    """Incident.host_ids (JSON text) → list of ints; None = unknown."""
    if raw is None or raw == "":
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    out = []
    for v in value:
        if isinstance(v, bool):
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv > 0:
            out.append(iv)
    return out


async def open_incidents_by_host(db) -> dict[int, list[dict]]:
    """{host_id: [{id, rule, title, severity}]} for open/acknowledged incidents
    that recorded their hosts. One query; incidents without host_ids are left out
    (unknown, not "no hosts")."""
    from sqlalchemy import select

    from models.incident import Incident

    rows = (await db.execute(
        select(Incident.id, Incident.rule, Incident.title, Incident.severity, Incident.host_ids)
        .where(Incident.status.in_(["open", "acknowledged"]), Incident.host_ids.isnot(None))
    )).all()
    out: dict[int, list[dict]] = {}
    for inc_id, rule, title, severity, raw in rows:
        for hid in parse_host_ids(raw) or []:
            out.setdefault(hid, []).append(
                {"id": inc_id, "rule": rule, "title": title, "severity": severity}
            )
    return out


async def load_probes(db, hosts) -> dict[int, probe_svc.ProbeState]:
    from sqlalchemy import select

    from models.agent import Agent

    probe_ids = {h.probe_id for h in hosts if getattr(h, "probe_id", None)}
    if not probe_ids:
        return {}
    rows = (await db.execute(select(Agent).where(Agent.id.in_(probe_ids)))).scalars().all()
    return {a.id: probe_state_of(a) for a in rows}


def probe_state_of(agent) -> probe_svc.ProbeState:
    return probe_svc.ProbeState(
        probe_id=agent.id,
        name=agent.name or agent.hostname or f"agent-{agent.id}",
        interval_seconds=agent.probe_interval_seconds,
        last_report=(agent.last_seen - datetime(1970, 1, 1)).total_seconds() if agent.last_seen else None,
    )


async def thresholds(db) -> tuple[int, float | None]:
    """(ping_interval seconds, global latency threshold ms or None)."""
    from models.settings import get_setting

    try:
        interval = int(await get_setting(db, "ping_interval", "60") or 60)
    except (TypeError, ValueError):
        interval = DEFAULT_PING_INTERVAL_SECONDS
    raw = (await get_setting(db, "latency_threshold_ms", "") or "").strip()
    try:
        latency = float(raw) if raw else None
    except ValueError:
        latency = None
    return interval, latency


def _upstream_down(host_id: int, topology: dict | None, states_down: set[int], names: dict[int, str]) -> str | None:
    if not topology:
        return None
    from services.topology import get_ancestors

    for anc in get_ancestors(topology, host_id):
        if anc in states_down:
            return names.get(anc, f"host {anc}")
    return None


async def host_states(
    db,
    hosts: list,
    now: datetime | None = None,
    *,
    latest: dict[int, dict] | None = None,
    windows: list | None = None,
    topology: dict[int, int | None] | str | None = "auto",
    incidents_by_host: dict[int, list[dict]] | None = None,
) -> dict[int, HostState]:
    """State per host id for the given hosts, batched.

    Pass ``latest`` / ``windows`` / ``topology`` when the caller already has
    them; otherwise they are loaded here (one ClickHouse query for latest).
    ``topology="auto"`` loads the (cached) topology only when some host is
    down, to name a down upstream device in its reason; ``None`` skips that.
    """
    from services import clickhouse_client as ch

    if not hosts:
        return {}
    now = now or datetime.utcnow()
    if latest is None:
        latest = await ch.get_latest_ping_per_host([h.id for h in hosts])
    if windows is None:
        windows = await maint_svc.load_windows(db)
    if incidents_by_host is None:
        incidents_by_host = await open_incidents_by_host(db)
    probes = await load_probes(db, hosts)
    ping_interval, global_latency = await thresholds(db)

    # Down is decided first (it needs no topology), so the second pass can name
    # a down ancestor in a down host's reason.
    names = {h.id: h.name for h in hosts}
    first: dict[int, HostState] = {}
    for h in hosts:
        probe_id = getattr(h, "probe_id", None)
        first[h.id] = evaluate(
            h, latest.get(h.id), now,
            maintenance=maint_svc.api_fields(h, now, windows),
            probe=probes.get(probe_id) if probe_id else None,
            probe_missing=bool(probe_id) and probe_id not in probes,
            ping_interval=ping_interval,
            global_latency_threshold=global_latency,
            incidents=incidents_by_host.get(h.id),
        )
    if not topology:
        return first
    down = {hid for hid, st in first.items() if st.state == STATE_DOWN}
    if not down:
        return first
    if topology == "auto":
        from services.topology import cached_topology

        try:
            topology = (await cached_topology(db))[0]
        except Exception:  # noqa: BLE001 — the reason is a nicety, never a failure
            return first
    out = dict(first)
    for hid in down:
        parent_name = _upstream_down(hid, topology, down, names)
        if parent_name:
            st = first[hid]
            out[hid] = HostState(STATE_DOWN, f"Not responding, behind {parent_name} (down)",
                                 st.observed_at, f"behind {parent_name}")
    return out


def counts(states: Iterable[HostState]) -> dict[str, int]:
    """Hosts per state, every state present (0 when none)."""
    c = Counter(s.state for s in states)
    return {s: c.get(s, 0) for s in STATES}


def reasons_by_state(states: Iterable[HostState], top: int = 3) -> dict[str, list[dict]]:
    """Per state, the most common reason groups: [{text, count}]."""
    groups: dict[str, Counter] = {}
    for s in states:
        if s.reason_group:
            groups.setdefault(s.state, Counter())[s.reason_group] += 1
    return {
        state: [{"text": text, "count": n} for text, n in counter.most_common(top)]
        for state, counter in groups.items()
    }

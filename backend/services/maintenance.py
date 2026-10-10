"""Is a host in maintenance right now? — the one place that answers it.

A host is in maintenance when
  * its manual flag is set (``PingHost.maintenance``) and, if it has an end
    (``maintenance_until``), that end has not passed yet; or
  * an enabled maintenance window that covers it is active.

Every consumer — the ping job, probe assignments, correlation/incidents, port
discovery, dashboard counts and health score, the API's ``maintenance`` field —
goes through :func:`is_in_maintenance`, so a window behaves exactly like the
manual flag.

Windows are evaluated, never written back: a window does not set the host's
flag, so ending or deleting a window ends its effect immediately and the
manual flag keeps meaning only what a person set.

Time handling: ``now`` is naive UTC like every timestamp in the schema.
Weekly windows are defined in a wall-clock time zone; their start is
resolved per day with zoneinfo, so "Sun 02:00 Europe/Zurich" stays at 02:00
local across DST changes. The duration is absolute (a 2 h window lasts 2 real
hours even across a DST switch). A start time that does not exist on a
spring-forward day (02:30 when clocks jump 02:00→03:00) begins at the
equivalent instant after the jump; an ambiguous one on the fall-back day
uses its first occurrence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

MAX_DURATION_MINUTES = 7 * 24 * 60
MAX_HOST_IDS = 5000
KINDS = ("weekly", "once")


@dataclass(frozen=True)
class WindowSpec:
    """A maintenance window detached from the ORM (cheap to pass around)."""
    id: int
    name: str
    kind: str
    enabled: bool = True
    weekdays: frozenset[int] = frozenset()
    start_time: time | None = None
    duration_minutes: int = 0
    starts_at: datetime | None = None   # naive UTC
    ends_at: datetime | None = None     # naive UTC
    tz: str = "UTC"
    all_hosts: bool = False
    host_ids: frozenset[int] = frozenset()

    def covers(self, host_id: int) -> bool:
        return self.all_hosts or host_id in self.host_ids


# ── Parsing (shared by the API and by loading rows) ───────────────────────────

def parse_weekdays(value) -> frozenset[int]:
    if value is None or value == "":
        return frozenset()
    items = value.split(",") if isinstance(value, str) else list(value)
    out = set()
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if not s.isdigit() or not (0 <= int(s) <= 6):
            raise ValueError("weekdays must be numbers 0 (Monday) to 6 (Sunday)")
        out.add(int(s))
    return frozenset(out)


def parse_start_time(value) -> time:
    s = str(value or "").strip()
    try:
        hh, mm = s.split(":")
        if len(hh) not in (1, 2) or len(mm) != 2:
            raise ValueError
        return time(int(hh), int(mm))
    except ValueError:
        raise ValueError("start_time must be HH:MM (24h)") from None


def parse_tz(value) -> str:
    name = str(value or "").strip() or "UTC"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone '{name}'") from None
    return name


def parse_host_ids(value) -> frozenset[int]:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("host_ids must be a list of host ids") from None
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("host_ids must be a list of host ids")
    out = set()
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).strip().isdigit():
            raise ValueError("host_ids must be a list of host ids")
        out.add(int(v))
    if len(out) > MAX_HOST_IDS:
        raise ValueError(f"at most {MAX_HOST_IDS} hosts per window")
    return frozenset(out)


def to_naive_utc(value, tz_name: str) -> datetime:
    """ISO string/datetime → naive UTC. A value without offset is wall-clock in tz_name."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"'{value}' is not an ISO date/time") from None
    if not isinstance(value, datetime):
        raise ValueError("expected an ISO date/time")
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(tz_name))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def spec_from_row(row) -> WindowSpec:
    """WindowSpec from a MaintenanceWindow row. A malformed row is inert (never active)."""
    try:
        return WindowSpec(
            id=row.id, name=row.name or "", kind=row.kind or "weekly",
            enabled=bool(row.enabled),
            weekdays=parse_weekdays(row.weekdays),
            start_time=parse_start_time(row.start_time) if row.start_time else None,
            duration_minutes=int(row.duration_minutes or 0),
            starts_at=row.starts_at, ends_at=row.ends_at,
            tz=parse_tz(row.timezone),
            all_hosts=bool(row.all_hosts),
            host_ids=parse_host_ids(row.host_ids),
        )
    except (ValueError, TypeError, AttributeError):
        return WindowSpec(id=getattr(row, "id", 0), name=str(getattr(row, "name", "") or ""),
                          kind="invalid", enabled=False)


# ── Evaluation ───────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def occurrence_at(w: WindowSpec, now: datetime) -> tuple[datetime, datetime] | None:
    """(start, end) in naive UTC of the occurrence active at ``now``, else None."""
    if not w.enabled:
        return None
    if w.kind == "once":
        if w.starts_at and w.ends_at and w.starts_at <= now < w.ends_at:
            return w.starts_at, w.ends_at
        return None
    if w.kind != "weekly" or not w.weekdays or w.start_time is None or w.duration_minutes <= 0:
        return None
    zone = ZoneInfo(w.tz)
    now_aware = now.replace(tzinfo=timezone.utc)
    local_today = now_aware.astimezone(zone).date()
    duration = timedelta(minutes=w.duration_minutes)
    # An occurrence that started up to `duration` ago may still be running —
    # look back far enough to catch overnight and multi-day windows.
    lookback = w.duration_minutes // (24 * 60) + 1
    for back in range(lookback + 1):
        day = local_today - timedelta(days=back)
        if day.weekday() not in w.weekdays:
            continue
        start = datetime.combine(day, w.start_time, tzinfo=zone).astimezone(timezone.utc)
        end = start + duration
        if start <= now_aware < end:
            return start.replace(tzinfo=None), end.replace(tzinfo=None)
    return None


def next_occurrence(w: WindowSpec, now: datetime) -> tuple[datetime, datetime] | None:
    """The next occurrence starting after ``now`` (naive UTC), for display."""
    if not w.enabled:
        return None
    if w.kind == "once":
        if w.starts_at and w.ends_at and w.starts_at > now:
            return w.starts_at, w.ends_at
        return None
    if w.kind != "weekly" or not w.weekdays or w.start_time is None or w.duration_minutes <= 0:
        return None
    zone = ZoneInfo(w.tz)
    now_aware = now.replace(tzinfo=timezone.utc)
    local_today = now_aware.astimezone(zone).date()
    for ahead in range(0, 8):
        day = local_today + timedelta(days=ahead)
        if day.weekday() not in w.weekdays:
            continue
        start = datetime.combine(day, w.start_time, tzinfo=zone).astimezone(timezone.utc)
        if start > now_aware:
            end = start + timedelta(minutes=w.duration_minutes)
            return start.replace(tzinfo=None), end.replace(tzinfo=None)
    return None


def is_window_active(w: WindowSpec, now: datetime | None = None) -> bool:
    return occurrence_at(w, now or _utc_now()) is not None


def manual_maintenance(host, now: datetime | None = None) -> bool:
    """The per-host flag, honouring maintenance_until even before the job clears it."""
    if not getattr(host, "maintenance", False):
        return False
    until = getattr(host, "maintenance_until", None)
    return until is None or until > (now or _utc_now())


def active_window_for(host, now: datetime | None = None,
                      windows: Iterable[WindowSpec] = ()) -> WindowSpec | None:
    now = now or _utc_now()
    host_id = getattr(host, "id", None)
    for w in windows:
        if host_id is not None and w.covers(host_id) and occurrence_at(w, now):
            return w
    return None


def is_in_maintenance(host, now: datetime | None = None,
                      windows: Iterable[WindowSpec] = ()) -> bool:
    """True if the host is in maintenance at ``now`` (naive UTC).

    ``windows`` comes from :func:`load_windows`; without it only the manual
    flag counts.
    """
    now = now or _utc_now()
    return manual_maintenance(host, now) or active_window_for(host, now, windows) is not None


def api_fields(host, now: datetime | None = None, windows: Iterable[WindowSpec] = ()) -> dict:
    """The API's view of a host's maintenance state.

    ``maintenance`` is the effective state (manual flag or an active window) —
    what every consumer must act on. ``maintenance_manual`` is the flag alone,
    the part the toggle endpoints change, and ``maintenance_window`` names the
    window currently covering the host.
    """
    now = now or _utc_now()
    manual = manual_maintenance(host, now)
    window = active_window_for(host, now, windows)
    occ = occurrence_at(window, now) if window else None
    return {
        "maintenance": manual or window is not None,
        "maintenance_manual": manual,
        "maintenance_window": {
            "id": window.id, "name": window.name,
            "ends_at": occ[1].isoformat() + "Z" if occ else None,
        } if window else None,
    }


# ── Loading ──────────────────────────────────────────────────────────────────

async def load_windows(db) -> list[WindowSpec]:
    """Every enabled window. One small query; callers load once per request/cycle."""
    from models.maintenance import MaintenanceWindow

    rows = (await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.enabled == True)  # noqa: E712
    )).scalars().all()
    return [spec_from_row(r) for r in rows]


async def maintenance_ids(db, hosts: Iterable, now: datetime | None = None) -> set[int]:
    """Ids of the given hosts that are in maintenance at ``now``."""
    now = now or _utc_now()
    windows = await load_windows(db)
    return {h.id for h in hosts if is_in_maintenance(h, now, windows)}


async def without_maintenance(db, hosts: list, now: datetime | None = None) -> list:
    """The given hosts minus those in maintenance at ``now``."""
    if not hosts:
        return []
    skip = await maintenance_ids(db, hosts, now)
    return [h for h in hosts if h.id not in skip]

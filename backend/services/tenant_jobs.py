"""Per-tenant scheduling (multi-tenancy phase 5).

The scheduler stays one leader-elected APScheduler with one job per kind of
work. What changes with several tenants is *inside* a job run:

* **Tenant loops.** ``per_tenant_job`` (analysis jobs: correlation, log
  intelligence, alert rules, cleanup, …) runs the job once per active tenant,
  one after another, each in its own ``tenant_scope``. ``fleet_job``
  (data-plane jobs: ping, integrations, SNMP, …) runs the due tenants
  concurrently, at most ``parallel`` at a time. Both rotate the start tenant
  per run, isolate a tenant's exception, and give each tenant a **time
  budget**: an iteration still running after it is left to finish in the
  background and that tenant is skipped until it has — a stuck tenant no
  longer holds the job (``max_instances=1``) for everybody else.
* **Per-tenant cadence.** A data-plane job with a cadence setting
  (``ping_interval``, ``proxmox_interval``) ticks at the fastest tenant's
  interval and runs a tenant only when *its* interval has elapsed
  (:class:`TenantCadence`), never faster than the installation floor.
* **Fair share.** :class:`FairShareLimiter` replaces the plain semaphores in
  front of the expensive per-item work (integration collections, ping
  checks): free slots go round-robin to the tenants that are waiting, with an
  optional per-tenant cap, so 2 000 hosts of one tenant cannot push another
  tenant's 20 past their interval.
* **Per-tenant job metrics** (:data:`stats`, Prometheus with a bounded
  ``tenant`` label), shown in ``/api/system/status``.

With exactly one tenant every path degenerates to the plain call: no task,
no budget, no cadence query, FIFO slots — the single-tenant timings are the
old ones (``tests/test_tenant_scheduling.py`` measures that).
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Awaitable, Callable

log = logging.getLogger("nodeglow.tenant_jobs")

# Injectable clocks (tests use a fake clock).
monotonic: Callable[[], float] = time.monotonic
wallclock: Callable[[], float] = time.time


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Default time budget of one tenant's iteration of an analysis job.
TENANT_JOB_BUDGET = _env_float("NODEGLOW_TENANT_JOB_BUDGET", 300.0)
# Data-plane jobs: tenants processed at once.
FLEET_PARALLEL_TENANTS = max(1, int(_env_float("NODEGLOW_FLEET_PARALLEL_TENANTS", 8)))
# Per-tenant cap on fair-share slots (0 = no cap beyond the total).
TENANT_CONCURRENCY_CAP = max(0, int(_env_float("NODEGLOW_TENANT_CONCURRENCY_CAP", 0)))
# Distinct tenant ids that get their own Prometheus label; the rest are "other".
TENANT_METRIC_LABELS = max(1, int(_env_float("NODEGLOW_TENANT_METRIC_LABELS", 100)))


# ── Job name (set by services.metrics.instrument_job) ───────────────────────

current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nodeglow_current_job", default=None,
)


def _job_name(fn) -> str:
    return current_job.get() or getattr(fn, "metric_name", None) or fn.__name__


# ── Per-tenant job statistics ────────────────────────────────────────────────

@dataclass
class TenantJobStat:
    runs: int = 0
    failures: int = 0
    skipped_inflight: int = 0
    over_budget: int = 0
    last_duration: float | None = None
    last_run: float | None = None
    last_success: float | None = None
    last_error: str | None = None
    in_flight: bool = False

    def as_dict(self, *, with_error: bool = True) -> dict:
        d = {
            "runs": self.runs, "failures": self.failures,
            "skipped_inflight": self.skipped_inflight, "over_budget": self.over_budget,
            "last_duration_s": round(self.last_duration, 3) if self.last_duration is not None else None,
            "last_run": _iso(self.last_run), "last_success": _iso(self.last_success),
            "in_flight": self.in_flight,
        }
        if with_error:
            d["last_error"] = self.last_error
        return d


def _iso(ts: float | None) -> str | None:
    return datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else None


@dataclass
class TenantJobStats:
    """In-process counters per (job, tenant). Bounded by jobs × tenants."""

    _data: dict[tuple[str, int], TenantJobStat] = field(default_factory=dict)
    _labelled: set[int] = field(default_factory=set)

    def get(self, job: str, tenant_id: int) -> TenantJobStat:
        key = (job, tenant_id)
        st = self._data.get(key)
        if st is None:
            st = self._data[key] = TenantJobStat()
        return st

    def record(self, job: str, tenant_id: int, duration: float, error: BaseException | None) -> None:
        st = self.get(job, tenant_id)
        now = wallclock()
        st.runs += 1
        st.last_duration = duration
        st.last_run = now
        status = "success"
        if error is not None:
            st.failures += 1
            st.last_error = f"{type(error).__name__}: {error}"[:300]
            status = "failure"
        else:
            st.last_success = now
        self._prometheus(job, tenant_id, duration, status, now)

    def skipped(self, job: str, tenant_id: int) -> None:
        self.get(job, tenant_id).skipped_inflight += 1

    def over(self, job: str, tenant_id: int) -> None:
        self.get(job, tenant_id).over_budget += 1

    def label_for(self, tenant_id: int) -> str:
        if tenant_id in self._labelled:
            return str(tenant_id)
        if len(self._labelled) < TENANT_METRIC_LABELS:
            self._labelled.add(tenant_id)
            return str(tenant_id)
        return "other"

    def _prometheus(self, job: str, tenant_id: int, duration: float, status: str, now: float) -> None:
        try:
            from services.metrics import (
                TENANT_JOB_LAST_DURATION, TENANT_JOB_LAST_SUCCESS, TENANT_JOB_RUNS,
            )
        except Exception:  # pragma: no cover — metrics are best effort
            return
        label = self.label_for(tenant_id)
        TENANT_JOB_RUNS.labels(job=job, tenant=label, status=status).inc()
        TENANT_JOB_LAST_DURATION.labels(job=job, tenant=label).set(duration)
        if status == "success":
            TENANT_JOB_LAST_SUCCESS.labels(job=job, tenant=label).set(now)

    def for_tenant(self, tenant_id: int) -> dict[str, dict]:
        return {job: st.as_dict() for (job, tid), st in sorted(self._data.items()) if tid == tenant_id}

    def by_tenant(self) -> dict[int, dict[str, dict]]:
        """Installation view (operators): counters only, no error texts —
        those can name a tenant's hosts."""
        out: dict[int, dict[str, dict]] = {}
        for (job, tid), st in sorted(self._data.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            out.setdefault(tid, {})[job] = st.as_dict(with_error=False)
        return out

    def reset(self) -> None:
        self._data.clear()
        self._labelled.clear()


stats = TenantJobStats()


# ── Tenant iterations: rotation, budget, in-flight tracking ─────────────────

_rotation: dict[Any, int] = {}  # keyed by the job function
# (job, tenant) -> background task of an iteration that outlived its budget.
_inflight: dict[tuple[str, int], asyncio.Task] = {}


def rotated(key: Any, tenant_ids: list[int]) -> list[int]:
    """The tenants in round-robin order: each run starts one tenant later,
    so the tenant at the end of the list is not always the one that waits."""
    if len(tenant_ids) < 2:
        return list(tenant_ids)
    offset = _rotation.get(key, 0) % len(tenant_ids)
    _rotation[key] = offset + 1
    return tenant_ids[offset:] + tenant_ids[:offset]


async def _iteration(fn, tenant_id: int, job: str, args, kwargs):
    """One tenant's run, in its scope, timed and recorded. Raises its error."""
    from services.tenancy import tenant_scope

    start = monotonic()
    error: BaseException | None = None
    try:
        with tenant_scope(tenant_id):
            return await fn(*args, **kwargs)
    except Exception as exc:
        error = exc
        raise
    finally:
        stats.record(job, tenant_id, monotonic() - start, error)


def _leave_in_background(job: str, tenant_id: int, task: asyncio.Task, budget: float,
                         level: int) -> None:
    stats.over(job, tenant_id)
    stats.get(job, tenant_id).in_flight = True
    _inflight[(job, tenant_id)] = task
    log.log(level, "%s for tenant %s still running after its %.0fs budget; it continues in the "
            "background and the tenant is skipped until it is done", job, tenant_id, budget)

    def _done(t: asyncio.Task, key=(job, tenant_id)) -> None:
        if _inflight.get(key) is t:
            del _inflight[key]
        stats.get(*key).in_flight = False
        if not t.cancelled() and t.exception() is not None:
            log.error("%s failed for tenant %s (after its budget)", key[0], key[1],
                      exc_info=t.exception())

    task.add_done_callback(_done)


def _is_inflight(job: str, tenant_id: int) -> bool:
    task = _inflight.get((job, tenant_id))
    if task is None:
        return False
    if task.done():
        _inflight.pop((job, tenant_id), None)
        return False
    return True


async def run_for_tenants(fn, tenant_ids: list[int], args=(), kwargs=None, *,
                          job: str, parallel: int = 1, budget: float | None = None,
                          straggler_level: int = logging.WARNING) -> None:
    """Run ``fn`` once per tenant (several tenants: see the module docstring).

    ``parallel`` tenants at a time; each iteration waits at most ``budget``
    seconds, then continues in the background (logged at ``straggler_level``:
    expected for data-plane passes, whose budget is one tick). The first exception of an
    iteration that finished within its budget is re-raised after all tenants
    had their turn (so the job's instrumentation records a failure).
    """
    kwargs = kwargs or {}
    order = rotated(fn, tenant_ids)
    first_error: BaseException | None = None
    # Parallel runs share a gate of `parallel` slots for this run. Sequential
    # runs need none — and must not have one: an iteration left in the
    # background would hold it and block every tenant after it.
    gate = asyncio.Semaphore(parallel) if parallel > 1 else None

    async def guarded(tid: int):
        if gate is None:
            return await _iteration(fn, tid, job, args, kwargs)
        async with gate:
            return await _iteration(fn, tid, job, args, kwargs)

    started: dict[asyncio.Task, int] = {}
    try:
        for tid in order:
            if _is_inflight(job, tid):
                stats.skipped(job, tid)
                log.log(straggler_level, "%s: tenant %s skipped, its previous run is still "
                        "in progress", job, tid)
                continue
            task = asyncio.create_task(guarded(tid))
            started[task] = tid
            if parallel <= 1:
                # Sequential: wait for this tenant (up to its budget) first.
                done, _ = await asyncio.wait({task}, timeout=budget)
                if not done:
                    _leave_in_background(job, tid, task, budget or 0, straggler_level)
                    del started[task]
                continue
        if parallel > 1 and started:
            # The budget covers the whole pass (queueing for a slot included).
            _done, pending = await asyncio.wait(set(started), timeout=budget)
            for task in pending:
                _leave_in_background(job, started.pop(task), task, budget or 0, straggler_level)
    except asyncio.CancelledError:
        for task in started:
            task.cancel()
        raise
    for task, tid in started.items():
        if task.done() and not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            log.error("%s failed for tenant %s", job, tid, exc_info=exc)
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


async def run_single(fn, tenant_id: int, args=(), kwargs=None, *, job: str):
    """The one-tenant case: the plain call in its scope (no task, no budget)."""
    return await _iteration(fn, tenant_id, job, args, kwargs or {})


def reset_state() -> None:
    """Test helper: forget rotation, in-flight iterations, cadence and stats."""
    _rotation.clear()
    _inflight.clear()
    cadence.reset()
    stats.reset()
    _limiters.clear()


# ── Per-tenant cadence ───────────────────────────────────────────────────────

class TenantCadence:
    """When each tenant last ran a cadence job, and whether it is due again.

    A job ticks at the fastest tenant's interval (``tick``); a tenant is due
    once its own interval has elapsed, with half a tick of tolerance so a
    tenant whose interval equals the tick runs on every tick despite jitter.
    """

    def __init__(self) -> None:
        self._last: dict[tuple[str, int], float] = {}
        self.ticks: dict[str, float] = {}

    def due(self, job: str, tenant_id: int, interval: float, tick: float | None = None) -> bool:
        last = self._last.get((job, tenant_id))
        if last is None:
            return True
        tick = tick if tick is not None else self.ticks.get(job, interval)
        return monotonic() - last >= interval - tick / 2

    def mark(self, job: str, tenant_id: int) -> None:
        self._last[(job, tenant_id)] = monotonic()

    def reset(self) -> None:
        self._last.clear()
        self.ticks.clear()


cadence = TenantCadence()


@dataclass(frozen=True)
class CadenceSpec:
    """A tenant setting that sets a data-plane job's cadence, and the
    installation key holding its floor."""

    key: str
    floor_key: str
    default: int = 60
    ceiling: int = 3600


PING_CADENCE = CadenceSpec("ping_interval", "ping_interval_min")
INTEGRATION_CADENCE = CadenceSpec("proxmox_interval", "proxmox_interval_min")


async def interval_floor(db, spec: CadenceSpec) -> int:
    from models.settings import get_setting

    try:
        return max(1, int(await get_setting(db, spec.floor_key)))
    except (TypeError, ValueError):
        return 10


def clamp_interval(value: Any, floor: int, spec: CadenceSpec) -> int:
    """A tenant's interval within [floor, ceiling]; unparsable → default."""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        v = spec.default
    return max(floor, min(spec.ceiling, v))


async def tenant_intervals(spec: CadenceSpec, tenant_ids: list[int]) -> dict[int, int]:
    """Every tenant's effective interval, in one query (system scope)."""
    from sqlalchemy import select

    from models import base as _base
    from models.tenant import TenantSetting
    from services.settings_registry import lookup
    from services.tenancy import system_scope

    with system_scope(f"cadence: read every tenant's {spec.key}", audit=False):
        async with _base.AsyncSessionLocal() as db:
            floor = await interval_floor(db, spec)
            rows = dict((await db.execute(
                select(TenantSetting.tenant_id, TenantSetting.value)
                .where(TenantSetting.key == spec.key, TenantSetting.tenant_id.in_(tenant_ids))
            )).all())
    default = lookup(spec.key).default or spec.default
    return {t: clamp_interval(rows.get(t, default), floor, spec) for t in tenant_ids}


async def job_tick(spec: CadenceSpec) -> int:
    """The interval a cadence job is scheduled at: the fastest tenant's."""
    from services.tenancy import backend, system_scope

    try:
        with system_scope(f"cadence: list tenants for {spec.key}", audit=False):
            tenant_ids = await backend().active_tenant_ids()
        intervals = await tenant_intervals(spec, tenant_ids) if tenant_ids else {}
    except Exception:
        # The scheduler must start even when the settings cannot be read.
        log.warning("Could not read the tenants' %s; using %ds", spec.key, spec.default,
                    exc_info=True)
        return spec.default
    return min(intervals.values(), default=spec.default)


async def retention_days(db, key: str, default: int) -> int:
    """A tenant's retention setting, capped by the installation's ``<key>_max``
    (empty: no cap). ``0`` ("keep forever", where a key allows it) becomes
    the cap when there is one."""
    from models.settings import get_setting

    try:
        value = int(await get_setting(db, key, str(default)))
    except (TypeError, ValueError):
        value = default
    raw_cap = await get_setting(db, f"{key}_max")
    try:
        cap = int(raw_cap) if raw_cap not in (None, "") else None
    except (TypeError, ValueError):
        cap = None
    if cap is None or cap <= 0:
        return value
    return cap if value <= 0 else min(value, cap)


# ── Decorators ───────────────────────────────────────────────────────────────

def _already_in_tenant() -> bool:
    from services.tenancy import current

    ctx = current()
    return ctx is not None and not ctx.system and ctx.tenant_id is not None


async def _active_tenants(fn) -> list[int]:
    from services.tenancy import backend, system_scope

    with system_scope(f"{fn.__name__}: list tenants"):
        return await backend().active_tenant_ids()


def per_tenant_job(fn: Callable[..., Awaitable[Any]] = None, *, budget: float | None = None):
    """Decorator: run the async function once per active tenant, each in its
    own ``tenant_scope``, one tenant after another (analysis jobs).

    One tenant's exception does not stop the others: it is logged with the
    tenant id and re-raised after the loop (the first one), so the job's
    instrumentation still records the failure. An iteration that outlives
    its budget (``NODEGLOW_TENANT_JOB_BUDGET``, default 300 s) continues in
    the background and the loop moves on. With one tenant this is exactly
    the undecorated call. A call that already runs inside a tenant scope (a
    test, or a route triggering the job for "its" tenant) runs for that
    tenant only.
    """
    if fn is None:
        return functools.partial(per_tenant_job, budget=budget)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if _already_in_tenant():
            return await fn(*args, **kwargs)
        job = _job_name(fn)
        tenant_ids = await _active_tenants(fn)
        if len(tenant_ids) == 1:  # the core case: exactly the plain call
            return await run_single(fn, tenant_ids[0], args, kwargs, job=job)
        await run_for_tenants(fn, tenant_ids, args, kwargs, job=job, parallel=1,
                              budget=budget if budget is not None else TENANT_JOB_BUDGET)
        return None

    wrapper.__tenancy__ = "per_tenant"
    return wrapper


def fleet_job(fn: Callable[..., Awaitable[Any]] = None, *, cadence_spec: CadenceSpec | None = None,
              parallel: int | None = None, budget: float | None = None):
    """Decorator for data-plane jobs: one fleet-wide pass that runs the due
    tenants concurrently (``parallel``, default ``NODEGLOW_FLEET_PARALLEL_TENANTS``),
    each in its own ``tenant_scope``.

    With ``cadence_spec`` a tenant runs only when its own interval (clamped
    to the installation floor) has elapsed; the job itself ticks at the
    fastest tenant's interval. The budget defaults to 80 % of the tick (at
    least 10 s); an iteration that outlives it continues in the background
    and that tenant is skipped until it is done. One tenant: the plain call.
    """
    if fn is None:
        return functools.partial(fleet_job, cadence_spec=cadence_spec, parallel=parallel,
                                 budget=budget)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if _already_in_tenant():
            return await fn(*args, **kwargs)
        job = _job_name(fn)
        tenant_ids = await _active_tenants(fn)
        if len(tenant_ids) == 1:
            # The tick is this tenant's own interval: always due.
            if cadence_spec is not None:
                cadence.mark(job, tenant_ids[0])
            return await run_single(fn, tenant_ids[0], args, kwargs, job=job)
        tick = cadence.ticks.get(job)
        if cadence_spec is not None:
            intervals = await tenant_intervals(cadence_spec, tenant_ids)
            tick = tick or min(intervals.values(), default=cadence_spec.default)
            due = [t for t in tenant_ids if cadence.due(job, t, intervals[t], tick)]
            for t in due:
                cadence.mark(job, t)
        else:
            due = list(tenant_ids)
        if not due:
            return None
        b = budget if budget is not None else max(10.0, 0.8 * tick) if tick else TENANT_JOB_BUDGET
        await run_for_tenants(fn, due, args, kwargs, job=job,
                              parallel=parallel or FLEET_PARALLEL_TENANTS, budget=b,
                              straggler_level=logging.INFO)
        return None

    wrapper.__tenancy__ = "per_tenant"
    wrapper.__fleet__ = True
    return wrapper


# ── Fair-share slots ─────────────────────────────────────────────────────────

class FairShareLimiter:
    """A semaphore whose free slots go round-robin to the waiting tenants.

    ``total`` slots overall; at most ``cap_for(tenant)`` held by one tenant
    (default: no cap beyond ``total``). With one tenant it behaves like an
    ``asyncio.Semaphore(total)`` (FIFO). Bound to the running event loop.
    """

    def __init__(self, total: int, cap_for: Callable[[int], int] | None = None):
        self.total = max(1, int(total))
        self._cap_for = cap_for
        self._in_use = 0
        self._held: dict[int, int] = {}
        self._waiters: dict[int, deque[asyncio.Future]] = {}
        self._order: deque[int] = deque()  # tenants with waiters, round-robin

    def cap(self, tenant_id: int) -> int:
        cap = self._cap_for(tenant_id) if self._cap_for else 0
        return min(self.total, cap) if cap and cap > 0 else self.total

    @property
    def in_use(self) -> int:
        return self._in_use

    def held(self, tenant_id: int) -> int:
        return self._held.get(tenant_id, 0)

    def _dispatch(self) -> None:
        while self._in_use < self.total and self._order:
            for _ in range(len(self._order)):
                tid = self._order[0]
                queue = self._waiters.get(tid)
                while queue and queue[0].done():  # cancelled waiters
                    queue.popleft()
                if not queue:
                    self._order.popleft()
                    self._waiters.pop(tid, None)
                    continue
                if self._held.get(tid, 0) >= self.cap(tid):
                    self._order.rotate(-1)
                    continue
                fut = queue.popleft()
                self._in_use += 1
                self._held[tid] = self._held.get(tid, 0) + 1
                fut.set_result(None)
                self._order.popleft()
                if queue:
                    self._order.append(tid)  # back of the line
                else:
                    self._waiters.pop(tid, None)
                break
            else:
                return  # every waiting tenant is at its cap

    async def acquire(self, tenant_id: int) -> None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        queue = self._waiters.get(tenant_id)
        if queue is None:
            queue = self._waiters[tenant_id] = deque()
            self._order.append(tenant_id)
        queue.append(fut)
        self._dispatch()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release(tenant_id)  # granted, then cancelled
            raise

    def release(self, tenant_id: int) -> None:
        self._in_use -= 1
        held = self._held.get(tenant_id, 0) - 1
        if held > 0:
            self._held[tenant_id] = held
        else:
            self._held.pop(tenant_id, None)
        self._dispatch()

    @asynccontextmanager
    async def slot(self, tenant_id: int | None = None) -> AsyncIterator[None]:
        """Hold one slot for ``tenant_id`` (default: the current tenant;
        outside a tenant scope the slot is booked to tenant 0)."""
        if tenant_id is None:
            from services.tenancy import current

            ctx = current()
            tenant_id = ctx.tenant_id if ctx is not None and ctx.tenant_id is not None else 0
        await self.acquire(tenant_id)
        try:
            yield
        finally:
            self.release(tenant_id)


def _default_cap(_tenant_id: int) -> int:
    return TENANT_CONCURRENCY_CAP


# One limiter per (name, event loop): futures bind to the loop that created
# them, and the test suite runs each test on a fresh loop.
_limiters: dict[tuple[str, int], FairShareLimiter] = {}


def limiter(name: str, total: int) -> FairShareLimiter:
    """The shared fair-share limiter ``name`` of the running loop."""
    key = (name, id(asyncio.get_running_loop()))
    lim = _limiters.get(key)
    if lim is None or lim.total != max(1, int(total)):
        for k in [k for k in _limiters if k[0] == name]:
            del _limiters[k]  # at most one live loop in production
        lim = _limiters[key] = FairShareLimiter(total, _default_cap)
    return lim


# ── Tenant-local schedules (digest, daily summary) ──────────────────────────

def tenant_zone(name: str | None):
    """The tenant's time zone; UTC when unset or unknown."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo((name or "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def last_slot(now_utc: datetime, zone, hour: int, weekday: int | None = None) -> datetime:
    """The most recent ``hour:00`` (on ``weekday``, Monday = 0, if given) in
    ``zone`` at or before ``now_utc`` (naive UTC), returned as naive UTC."""
    from datetime import timezone as _tz

    local = now_utc.replace(tzinfo=_tz.utc).astimezone(zone)
    slot = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=1)
    if weekday is not None:
        slot -= timedelta(days=(slot.weekday() - weekday) % 7)
    # Re-attach the zone after the day arithmetic (DST changes the offset).
    slot = slot.replace(tzinfo=None).replace(tzinfo=zone)
    return slot.astimezone(_tz.utc).replace(tzinfo=None)


def slot_due(now_utc: datetime, zone, hour: int, *, weekday: int | None = None,
             grace_seconds: float = 3600, last_sent: datetime | None = None) -> bool:
    """Whether a tenant-local schedule fires now: its latest slot is less than
    ``grace_seconds`` old (what an APScheduler cron with that misfire grace
    did) and nothing was sent since that slot."""
    slot = last_slot(now_utc, zone, hour, weekday)
    if (now_utc - slot).total_seconds() >= grace_seconds:
        return False
    return last_sent is None or last_sent < slot

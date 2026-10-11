"""Tenant context and the session-level tenant filter (multi-tenancy phase 2).

Every database access to a tenant-owned table (a model with the
``TenantScoped`` mixin) happens on behalf of exactly one tenant, or explicitly
on behalf of none:

* :func:`tenant_scope` — "this code acts for tenant N". Set once per request by
  the middleware in ``main.py`` (from :meth:`TenancyBackend.resolve_request_tenant`),
  per agent request from the agent's own row, and per iteration by
  :func:`per_tenant_job`.
* :func:`system_scope` — "this code is genuinely cross-tenant": token lookups
  that happen *before* the tenant is known (agent tokens, API keys, install
  tokens), installation-level jobs, ingestion that serves every tenant. It
  must name a reason; there is no implicit system scope anywhere.

The filter, installed on SQLAlchemy's ``Session`` class (so it covers every
session, including those the test suite builds itself):

* ``do_orm_execute`` adds ``with_loader_criteria(TenantScoped, tenant_id ==
  <current>)`` to every ORM SELECT / UPDATE / DELETE — joins, relationship
  loads and ORM-enabled bulk statements included. In system scope nothing is
  added. With **no context at all**, a statement that touches a tenant-owned
  table raises :class:`TenantContextError` instead of reading every tenant.
* ``before_flush`` stamps ``tenant_id`` (and a default ``site_id``) on new
  tenant-owned objects, and raises when an object carries a different tenant
  than the context, or when ``tenant_id`` of an existing row is changed. In
  system scope a new row must carry its ``tenant_id`` explicitly.

Not covered (by design, see the spec's raw-SQL inventory): ``text()`` SQL and
Core statements built on ``Table`` objects (``Model.__table__``). Those are
what Postgres RLS (phase 7) is for; code that uses them must add the tenant
predicate itself.

Performance: with a context set, the cost per statement is one
``with_loader_criteria`` option whose lambda is cached by SQLAlchemy (the
tenant id becomes a bound parameter). The statement walk that decides whether
a context-less statement touches tenant tables only runs when there is no
context, which in normal operation means global-table lookups (sessions,
users, settings).
"""
from __future__ import annotations

import contextvars
import functools
import logging
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, TypeVar

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import util as sql_util

from models.tenant import DEFAULT_SITE_ID, DEFAULT_TENANT_ID, TenantScoped

log = logging.getLogger("nodeglow.tenancy")

T = TypeVar("T")

# Key in Session.info for a session bound to one tenant for its whole life
# (see bind_session). The ambient context, when set, takes precedence.
SESSION_TENANT_KEY = "nodeglow_tenant_id"


class TenantContextError(RuntimeError):
    """A tenant-owned table was accessed without a tenant (or system) context,
    or a row would cross from one tenant into another."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Who the current code acts for: one tenant, or the system (cross-tenant)."""

    tenant_id: int | None
    system: bool = False
    reason: str = ""


_current: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar(
    "nodeglow_tenant_context", default=None,
)


# ── Context API ──────────────────────────────────────────────────────────────

def current() -> TenantContext | None:
    """The active context, or None."""
    return _current.get()


def current_tenant_id() -> int:
    """The tenant the current code acts for. Raises outside a tenant scope.

    For code that needs the id itself — raw SQL, ClickHouse later, cache keys.
    In system scope there is no single tenant, which is an error here too.
    """
    ctx = _current.get()
    if ctx is None or ctx.tenant_id is None:
        if ctx is not None and ctx.system:
            raise TenantContextError(
                f"no single tenant in system scope ({ctx.reason}); pass the tenant explicitly"
            )
        raise TenantContextError("no tenant context")
    return ctx.tenant_id


def session_tenant_id(session: Any) -> int:
    """The tenant a session's statements run for: the ambient tenant context,
    else the session's binding (:func:`bind_session`). Raises otherwise.

    For code that builds statements the filter does not see (ORM-enabled
    ``insert(Model).values(...)``, Core statements on ``Model.__table__``).
    """
    ctx = _current.get()
    if ctx is not None and not ctx.system and ctx.tenant_id is not None:
        return ctx.tenant_id
    if ctx is None:
        bound = session.info.get(SESSION_TENANT_KEY)
        if bound is not None:
            return bound
    return current_tenant_id()  # raises with the right message


def in_system_scope() -> bool:
    ctx = _current.get()
    return ctx is not None and ctx.system


@contextmanager
def tenant_scope(tenant_id: int) -> Iterator[TenantContext]:
    """Run the enclosed code on behalf of ``tenant_id``."""
    if tenant_id is None:
        raise TenantContextError("tenant_scope() needs a tenant id")
    ctx = TenantContext(tenant_id=int(tenant_id))
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def system_scope(reason: str) -> Iterator[TenantContext]:
    """Run the enclosed code across tenants. ``reason`` is mandatory.

    Reads are not filtered; new tenant-owned rows must carry ``tenant_id``.
    Keep these blocks small — the typical shape is "find the row, then
    continue in ``tenant_scope(row.tenant_id)``".
    """
    if not reason or not reason.strip():
        raise ValueError("system_scope() needs a reason")
    ctx = TenantContext(tenant_id=None, system=True, reason=reason)
    token = _current.set(ctx)
    log.debug("system scope entered: %s", reason)
    try:
        yield ctx
    finally:
        _current.reset(token)


def set_context(ctx: TenantContext | None) -> contextvars.Token:
    """Low-level: set the context and return the token for :func:`reset_context`.

    For the request middleware, whose set and reset sit in different branches.
    Everything else uses the context managers.
    """
    return _current.set(ctx)


def reset_context(token: contextvars.Token) -> None:
    _current.reset(token)


def bind_session(session: Any, tenant_id: int) -> Any:
    """Bind a session to one tenant for its lifetime (fallback when no context
    is set). Returns the session. Accepts a sync or an async session."""
    info = getattr(session, "info", None)
    if info is None:  # pragma: no cover — every SQLAlchemy session has .info
        raise TypeError("not a SQLAlchemy session")
    info[SESSION_TENANT_KEY] = int(tenant_id)
    return session


# ── Tenancy backend (extension point) ────────────────────────────────────────

class SingleTenancy:
    """The core backend: exactly one tenant (id 1), one site (id 1).

    Every authenticated request belongs to tenant 1; every job iterates over
    tenant 1. ``ee`` replaces it via ``extensions.registry.set_tenancy_backend``
    with one that resolves the tenant from the header / session / membership.
    """

    async def resolve_request_tenant(self, request: Any, user: Any) -> int | None:
        return DEFAULT_TENANT_ID if user is not None else None

    async def list_tenants_for(self, user: Any) -> list[int]:
        return [DEFAULT_TENANT_ID] if user is not None else []

    async def active_tenant_ids(self) -> list[int]:
        return [DEFAULT_TENANT_ID]

    def effective_role(self, user: Any, tenant_id: int) -> str:
        # users.role stays the source of truth until the auth binding (phase 4).
        return getattr(user, "role", None) or "admin"

    def can_create_tenant(self) -> bool:
        return False

    def default_site_id(self, tenant_id: int) -> int | None:
        return DEFAULT_SITE_ID if tenant_id == DEFAULT_TENANT_ID else None


_SINGLE = SingleTenancy()


def backend():
    """The active tenancy backend: a registered plugin's, else :class:`SingleTenancy`."""
    import extensions

    return extensions.registry.tenancy_backend or _SINGLE


@asynccontextmanager
async def user_tenant_scope(request: Any, user: Any) -> AsyncIterator[int]:
    """Tenant scope for a route that authenticates the user itself.

    The middleware skips ``/api/auth/`` (login, logout), so those routes
    resolve the tenant once they know who the user is — the same way the
    middleware would.
    """
    tenant_id = await backend().resolve_request_tenant(request, user)
    if tenant_id is None:
        raise TenantContextError("user has no tenant")
    with tenant_scope(tenant_id):
        yield tenant_id


# ── Job helpers ──────────────────────────────────────────────────────────────

def system_job(reason: str):
    """Decorator: the async function always runs in ``system_scope(reason)``."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with system_scope(reason):
                return await fn(*args, **kwargs)

        wrapper.__tenancy__ = "system"
        return wrapper

    return decorator


def per_tenant_job(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[None]]:
    """Decorator: run the async function once per active tenant, each in its
    own ``tenant_scope``.

    One tenant's exception does not stop the others: it is logged with the
    tenant id and re-raised after the loop (the first one), so the job's
    instrumentation still records the failure. With one tenant this is
    exactly the undecorated behaviour. A call that already runs inside a
    tenant scope (a test, or a route triggering the job for "its" tenant)
    runs for that tenant only.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        ctx = _current.get()
        if ctx is not None and not ctx.system and ctx.tenant_id is not None:
            return await fn(*args, **kwargs)
        with system_scope(f"{fn.__name__}: list tenants"):
            tenant_ids = await backend().active_tenant_ids()
        if len(tenant_ids) == 1:  # the core case: exactly the plain call
            with tenant_scope(tenant_ids[0]):
                return await fn(*args, **kwargs)
        first_error: BaseException | None = None
        result = None
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                try:
                    await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 — isolate tenants
                    log.error("%s failed for tenant %s", fn.__name__, tenant_id, exc_info=True)
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error
        return result

    wrapper.__tenancy__ = "per_tenant"
    return wrapper


# ── Session filter ───────────────────────────────────────────────────────────

_tenant_tables_cache: frozenset | None = None


def tenant_tables() -> frozenset:
    """Table objects of every TenantScoped model (computed once)."""
    global _tenant_tables_cache
    if _tenant_tables_cache is None:
        from models.base import Base

        _tenant_tables_cache = frozenset(
            m.local_table for m in Base.registry.mappers if issubclass(m.class_, TenantScoped)
        )
    return _tenant_tables_cache


def _effective_context(session: Session) -> TenantContext | None:
    ctx = _current.get()
    if ctx is not None:
        return ctx
    bound = session.info.get(SESSION_TENANT_KEY)
    if bound is not None:
        return TenantContext(tenant_id=bound)
    return None


def _touched_tenant_tables(statement) -> list[str]:
    tables = tenant_tables()
    found = sql_util.find_tables(
        statement, check_columns=True, include_aliases=True, include_joins=True,
        include_selects=True, include_crud=True,
    )
    return sorted({t.name for t in found if t in tables})


def _on_orm_execute(state) -> None:
    if not state.is_orm_statement:
        return  # text() / Core: out of scope for this layer (RLS, phase 7)
    if state.is_column_load:
        return  # refreshing attributes of an object already loaded by identity
    if not (state.is_select or state.is_update or state.is_delete or state.is_insert):
        return
    ctx = _effective_context(state.session)
    if ctx is not None and ctx.system:
        return
    if state.is_insert:
        # ORM-enabled insert(Model).values(...) bypasses before_flush; the
        # caller has to put tenant_id into the rows (from current_tenant_id()).
        if ctx is None:
            names = _touched_tenant_tables(state.statement)
            if names:
                raise TenantContextError(
                    f"INSERT into tenant-owned table(s) {', '.join(names)} without tenant context"
                )
        return
    if ctx is None:
        names = _touched_tenant_tables(state.statement)
        if names:
            raise TenantContextError(
                f"query on tenant-owned table(s) {', '.join(names)} without tenant context "
                "— run it in tenant_scope(...) or an explicit system_scope(reason)"
            )
        return
    tenant_id = ctx.tenant_id
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


def _before_flush(session: Session, flush_context, instances) -> None:
    ctx = _effective_context(session)
    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        tid = obj.tenant_id
        if ctx is None:
            raise TenantContextError(
                f"new {type(obj).__name__} without tenant context"
            )
        if ctx.system:
            if tid is None and not type(obj).__tenant_nullable__:
                raise TenantContextError(
                    f"new {type(obj).__name__} in system scope ({ctx.reason}) "
                    "needs an explicit tenant_id"
                )
        elif tid is None:
            obj.tenant_id = tid = ctx.tenant_id
        elif tid != ctx.tenant_id:
            raise TenantContextError(
                f"new {type(obj).__name__} carries tenant {tid}, context is tenant {ctx.tenant_id}"
            )
        if tid is not None and "site_id" in obj.__class__.__table__.c and obj.site_id is None:
            obj.site_id = backend().default_site_id(tid)
    if ctx is not None and ctx.system:
        return
    for obj in session.dirty:
        if isinstance(obj, TenantScoped):
            hist = _tenant_history(obj)
            if hist is not None and hist.deleted and hist.deleted[0] is not None:
                raise TenantContextError(
                    f"{type(obj).__name__} cannot move between tenants outside system scope"
                )


def _tenant_history(obj):
    hist = sa_inspect(obj).attrs.tenant_id.history
    return hist if hist.has_changes() else None


_installed = False


def install() -> None:
    """Install the filter on every SQLAlchemy session. Idempotent."""
    global _installed
    if _installed:
        return
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Session, "before_flush", _before_flush)
    _installed = True

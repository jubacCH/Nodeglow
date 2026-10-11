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
    # System scopes audit their writes (see _audit_system_write); only the
    # bootstrap of the default tenant opts out.
    audit: bool = True


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
def system_scope(reason: str, *, audit: bool = True) -> Iterator[TenantContext]:
    """Run the enclosed code across tenants. ``reason`` is mandatory.

    Reads are not filtered; new tenant-owned rows must carry ``tenant_id``.
    Writes to tenant-owned rows are audited (``system_scope.write``, one
    entry per tenant and scope); ``audit=False`` is for the bootstrap of the
    default tenant only. Keep these blocks small — the typical shape is
    "find the row, then continue in ``tenant_scope(row.tenant_id)``".
    """
    if not reason or not reason.strip():
        raise ValueError("system_scope() needs a reason")
    ctx = TenantContext(tenant_id=None, system=True, reason=reason, audit=audit)
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

TENANT_HEADER = "x-nodeglow-tenant"
# Query parameter carrying the tenant on WebSocket connects (browsers cannot
# set headers on a WebSocket handshake).
TENANT_QUERY_PARAM = "tenant"

ACTING_VIA_MEMBERSHIP = "membership"
ACTING_VIA_PROVIDER = "provider_role"
ACTING_VIA_OPERATOR = "operator"
ACTING_VIA_API_KEY = "api_key"
ACTING_VIA_AGENT = "agent"
ACTING_VIA_SYSTEM = "system"
# Time-limited support access a tenant admin granted (ee, owner decision 8).
ACTING_VIA_SUPPORT = "support"


@dataclass(frozen=True, slots=True)
class TenantAccess:
    """A user's access to one tenant: the tenant, the effective role, and how
    the access was granted (``membership`` in the core; ``provider_role`` or
    ``operator`` from an ee backend)."""

    tenant_id: int
    role: str
    via: str = ACTING_VIA_MEMBERSHIP


_INVALID = -1  # a requested tenant that cannot be parsed matches no membership


def requested_tenant(request: Any) -> int | None:
    """The tenant the client asks for explicitly: the ``X-Nodeglow-Tenant``
    header (any request) or the ``tenant`` query parameter (WebSocket
    connects only). None if it asks for none; an unparsable value yields an
    id no membership has."""
    raw = None
    headers = getattr(request, "headers", None)
    if headers is not None:
        raw = headers.get(TENANT_HEADER)
    if raw is None and getattr(request, "scope", {}).get("type") == "websocket":
        raw = request.query_params.get(TENANT_QUERY_PARAM)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _INVALID
    return value if value > 0 else _INVALID


async def _user_memberships(request: Any, user: Any) -> dict[int, str]:
    """The user's active memberships, loaded once per request."""
    state = getattr(request, "state", None)
    cache_key = ("memberships", getattr(user, "id", None))
    cached = getattr(state, "_tenancy_memberships", None) if state is not None else None
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    from models import base as _base
    from services.memberships import memberships_for

    async with _base.AsyncSessionLocal() as db:
        found = await memberships_for(db, user.id)
    if state is not None:
        try:
            state._tenancy_memberships = (cache_key, found)
        except AttributeError:  # pragma: no cover — a state object without attributes
            pass
    return found


class SingleTenancy:
    """The core backend: memberships decide, and there is one tenant (id 1)
    and one site (id 1).

    Resolution for an authenticated request (spec "Selecting the tenant"):
    an explicit ``X-Nodeglow-Tenant`` header (WebSocket: ``?tenant=``) wins,
    else the session's ``active_tenant_id``, else the user's only
    membership. The resolved tenant must be one of the user's (active)
    memberships, otherwise there is no tenant — the middleware answers 404,
    so a tenant id cannot be probed for existence. The role is the
    membership's. ``ee`` replaces this backend via
    ``extensions.registry.set_tenancy_backend`` (provider roles, several
    tenants, operator access).
    """

    async def resolve_access(self, request: Any, user: Any) -> TenantAccess | None:
        if user is None or getattr(user, "id", None) is None:
            return None
        memberships = await _user_memberships(request, user)
        tenant_id = requested_tenant(request)
        if tenant_id is None:
            tenant_id = getattr(getattr(request, "state", None), "session_active_tenant_id", None)
        if tenant_id is None:
            if len(memberships) != 1:
                return None  # no membership, or several and none chosen
            tenant_id = next(iter(memberships))
        role = memberships.get(tenant_id)
        if role is None:
            return None
        return TenantAccess(tenant_id=tenant_id, role=role)

    async def resolve_request_tenant(self, request: Any, user: Any) -> int | None:
        access = await self.resolve_access(request, user)
        return access.tenant_id if access else None

    async def resolve_login_access(self, request: Any, user: Any) -> TenantAccess | None:
        """The tenant a new session starts in. The core: as for any request
        (header, else the only membership); a backend with several tenants
        may pick one for a user with several (ee: the last used)."""
        return await self.resolve_access(request, user)

    async def list_tenants_for(self, user: Any) -> list[int]:
        return [a.tenant_id for a in await self.list_access_for(user)]

    async def list_access_for(self, user: Any) -> list[TenantAccess]:
        """Every tenant the user may switch into, with the role there and how
        the access is granted, ordered by tenant id."""
        if user is None or getattr(user, "id", None) is None:
            return []
        from models import base as _base
        from services.memberships import memberships_for

        async with _base.AsyncSessionLocal() as db:
            found = await memberships_for(db, user.id)
        return [TenantAccess(tenant_id=t, role=r) for t, r in sorted(found.items())]

    async def active_tenant_ids(self) -> list[int]:
        return [DEFAULT_TENANT_ID]

    async def effective_role(self, user: Any, tenant_id: int) -> str | None:
        """The user's role in that tenant, None without access."""
        if user is None or getattr(user, "id", None) is None:
            return None
        from models import base as _base
        from services.memberships import memberships_for

        async with _base.AsyncSessionLocal() as db:
            return (await memberships_for(db, user.id)).get(tenant_id)

    def is_operator(self, user: Any, role: str | None = None,
                    tenant_id: int | None = None, via: str | None = None) -> bool:
        """Installation operator. In the core: the admin of the default
        tenant (``role`` is the user's role in the request's tenant
        ``tenant_id``) — exactly who administered the installation before
        the split, and nobody else: a demoted admin loses it with the role.
        ``users.is_operator`` is kept equal to that (services.memberships)
        for an ee backend, which decides by the flag.

        ``via`` is how the role was granted: a role that comes from support
        access (ee) never makes anyone an operator — a temporary grant on
        the default tenant must not turn into installation rights."""
        if via == ACTING_VIA_SUPPORT:
            return False
        return tenant_id == DEFAULT_TENANT_ID and role == "admin"

    def can_create_tenant(self) -> bool:
        return False

    def default_site_id(self, tenant_id: int) -> int | None:
        return DEFAULT_SITE_ID if tenant_id == DEFAULT_TENANT_ID else None


_SINGLE = SingleTenancy()


def backend():
    """The active tenancy backend: a registered plugin's, else :class:`SingleTenancy`."""
    import extensions

    return extensions.registry.tenancy_backend or _SINGLE


async def tenant_is_active(db: Any, tenant_id: int | None) -> bool:
    """Whether the tenant exists and is active. Credentials of a suspended
    (or deleting) tenant — API keys, agent and install tokens — stop working."""
    if tenant_id is None:
        return False
    from models.tenant import Tenant

    tenant = await db.get(Tenant, tenant_id)
    return tenant is not None and tenant.status == "active"


def request_role(request: Any) -> str | None:
    """The role of the request's user in the request's tenant (set by the
    middleware from the membership). None when the request has no tenant
    access — callers treat that as "no role", never as admin."""
    return getattr(getattr(request, "state", None), "tenant_role", None)


def request_is_admin(request: Any) -> bool:
    return request_role(request) == "admin"


def request_is_operator(request: Any) -> bool:
    """Whether the request's user may change installation-level settings."""
    state = getattr(request, "state", None)
    user = getattr(state, "current_user", None)
    if user is None:
        return False
    return backend().is_operator(user, request_role(request), getattr(state, "tenant_id", None),
                                 via=request_acting_via(request))


def request_acting_via(request: Any) -> str | None:
    """How the request's user has access to the request's tenant (see the
    ``ACTING_VIA_*`` constants), None when unknown."""
    return getattr(getattr(request, "state", None), "acting_via", None)


def request_via_support(request: Any) -> bool:
    """Whether the request works in its tenant through support access (ee)."""
    return request_acting_via(request) == ACTING_VIA_SUPPORT


def request_may_manage_access(request: Any) -> bool:
    """Whether the request may manage who has access to its tenant: create,
    delete or re-role users and memberships, reset passwords, issue API keys
    and agent install tokens. Tenant admins may; support access may not,
    whatever role the grant carries — a support session must not outlive
    its grant through an account or a key it made (owner decision 8)."""
    return request_is_admin(request) and not request_via_support(request)


SUPPORT_IAM_REFUSED = ("Support access cannot manage users, passwords, API keys or "
                       "install tokens of this tenant.")


# How often a long-lived connection (/ws/live, the syslog live tail) checks
# that its user still has access to its tenant with the same role.
STREAM_REVALIDATE_SECONDS = 30.0


async def revalidate_access(conn: Any, tenant_id: int) -> TenantAccess | None:
    """Re-run the authentication and tenant resolution of a long-lived
    connection: the access it has *now* to ``tenant_id``, or None (session
    gone, membership revoked, tenant suspended, or the session switched to
    another tenant). Nothing is cached from the first resolution."""
    import database  # looked up at call time (patched in tests)
    from models import base as _base

    state = getattr(conn, "state", None)
    if state is not None:
        for attr in ("_tenancy_memberships", "session_active_tenant_id"):
            if hasattr(state, attr):
                delattr(state, attr)
    async with _base.AsyncSessionLocal() as db:
        user = await database.get_current_user(conn, db)
    if user is None:
        return None
    access = await backend().resolve_access(conn, user)
    if access is None or access.tenant_id != tenant_id:
        return None
    return access


@asynccontextmanager
async def user_tenant_scope(request: Any, user: Any) -> AsyncIterator[TenantAccess]:
    """Tenant scope for a route that authenticates the user itself.

    The middleware skips ``/api/auth/`` (login, logout), so those routes
    resolve the tenant once they know who the user is — the same way the
    middleware would. Raises :class:`TenantContextError` when the user has
    no access to any tenant.
    """
    access = await backend().resolve_access(request, user)
    if access is None:
        raise TenantContextError("user has no tenant")
    with tenant_scope(access.tenant_id):
        yield access


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


# per_tenant_job lives with the rest of the per-tenant scheduling (phase 5):
# rotation, time budget, per-tenant metrics. Re-exported here, where every
# job imports it from.
from services.tenant_jobs import fleet_job, per_tenant_job  # noqa: E402,F401


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


# ── Audit of writes in system scope ──────────────────────────────────────────
#
# Every system_scope() that writes tenant-owned rows leaves an audit entry
# (spec "Audit": "every system_scope() entry is audited"): one per affected
# tenant per scope and session, action ``system_scope.write``, with the
# scope's reason and the tables written. Reads in system scope (token
# lookups, listing tenants) are not audited — they happen on every agent
# request and would drown the log. Raw SQL is not seen here (phase 7).

SYSTEM_WRITE_ACTION = "system_scope.write"
_AUDITED_KEY = "nodeglow_system_audited"


def _audit_system_write(session: Session, ctx: TenantContext, tables: set[str],
                        tenant_ids: set[int | None]) -> None:
    import json
    from datetime import datetime

    from models.audit import AuditLog

    if not ctx.audit:
        return
    audited: set = session.info.setdefault(_AUDITED_KEY, set())
    for tid in sorted(tenant_ids, key=lambda t: (t is None, t or 0)):
        key = (id(ctx), ctx.reason, tid)
        if key in audited:
            continue
        audited.add(key)
        session.add(AuditLog(
            timestamp=datetime.utcnow(), tenant_id=tid, acting_via="system",
            action=SYSTEM_WRITE_ACTION, target_type="system_scope",
            target_name=ctx.reason[:256],
            details=json.dumps({"reason": ctx.reason, "tables": sorted(tables)}),
        ))


def _on_orm_execute(state) -> None:
    if not state.is_orm_statement:
        return  # text() / Core: out of scope for this layer (RLS, phase 7)
    if state.is_column_load:
        return  # refreshing attributes of an object already loaded by identity
    ctx = _effective_context(state.session)
    if not (ctx is not None and ctx.system):
        if state.is_from_statement or _is_from_statement(state):
            # select(Model).from_statement(text(...)): the SQL is the caller's,
            # nothing can be added to it. Fail closed unless the caller states
            # that its SQL carries the tenant predicate itself.
            if _from_statement_tenant_entities(state.statement) and \
                    not state.execution_options.get("nodeglow_tenant_filtered"):
                raise TenantContextError(
                    "from_statement() on a tenant-owned model is not tenant-filtered — add the "
                    "tenant predicate to the SQL and pass execution_options(nodeglow_tenant_filtered=True)"
                )
            return
    if not (state.is_select or state.is_update or state.is_delete or state.is_insert):
        return
    if ctx is not None and ctx.system:
        if not state.is_select:
            names = [n for n in _touched_tenant_tables(state.statement) if n != "audit_logs"]
            if names:
                # Bulk statements name no rows: the entry is installation-level.
                _audit_system_write(state.session, ctx, set(names), {None})
        return
    if state.is_insert:
        # ORM-enabled insert(Model).values(...) bypasses before_flush.
        names = _touched_tenant_tables(state.statement)
        if not names:
            return
        if ctx is None:
            raise TenantContextError(
                f"INSERT into tenant-owned table(s) {', '.join(names)} without tenant context"
            )
        _check_orm_insert(state, ctx.tenant_id, names)
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
    if state.is_update:
        if _update_sets_tenant(state):
            raise TenantContextError(
                "an UPDATE cannot change tenant_id outside system scope (rows would move tenant)"
            )
        if state.is_executemany:
            # session.execute(update(Model), [{"id": ...}, ...]) — "bulk UPDATE
            # by primary key" — is not covered by with_loader_criteria: every
            # row named by id would be written, whichever tenant owns it.
            raise TenantContextError(
                "ORM bulk UPDATE by primary key is not tenant-filtered — use a Core update "
                "with an explicit tenant_id predicate"
            )
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


def _bind_value(v):
    return getattr(v, "value", v)


def _row_keys(row: dict) -> dict:
    return {getattr(k, "key", k): v for k, v in row.items()}


def _update_sets_tenant(state) -> bool:
    stmt = state.statement
    keys = {getattr(k, "key", k) for k in (getattr(stmt, "_values", None) or {})}
    keys |= {getattr(k, "key", k) for k, _ in (getattr(stmt, "_ordered_values", None) or ())}
    params = state.parameters
    rows = params if isinstance(params, list) else ([params] if isinstance(params, dict) else [])
    for row in rows:
        keys |= set(_row_keys(row))
    return "tenant_id" in keys


def _check_orm_insert(state, tenant_id: int, names: list[str]) -> None:
    """ORM INSERT in a tenant scope: every row must belong to the tenant.

    ``tenant_id`` missing in execute-time parameter rows is filled in; a
    different tenant anywhere is refused, as is ``insert().from_select()``
    (its rows are whatever the SELECT returns, unfiltered).
    """
    stmt = state.statement
    if getattr(stmt, "select", None) is not None:
        raise TenantContextError(
            f"INSERT ... FROM SELECT into {', '.join(names)} is not tenant-filtered outside system scope"
        )

    def check(rows: list[dict]) -> None:
        for row in rows:
            row = _row_keys(row)
            if "tenant_id" in row and _bind_value(row["tenant_id"]) != tenant_id:
                raise TenantContextError(
                    f"INSERT into {', '.join(names)} carries tenant {_bind_value(row['tenant_id'])!r}, "
                    f"context is tenant {tenant_id}"
                )

    embedded: list[dict] = []
    if getattr(stmt, "_values", None):
        embedded.append(dict(stmt._values))
    for group in getattr(stmt, "_multi_values", None) or ():
        embedded.extend(group)
    check(embedded)
    if embedded and any("tenant_id" not in _row_keys(r) for r in embedded):
        if getattr(stmt, "_values", None) and not getattr(stmt, "_multi_values", None):
            state.statement = stmt.values(tenant_id=tenant_id)
        else:
            raise TenantContextError(
                f"multi-row INSERT into {', '.join(names)} needs tenant_id in every row"
            )
    params = state.parameters
    rows = params if isinstance(params, list) else ([params] if isinstance(params, dict) else [])
    check(rows)
    for row in rows:
        if isinstance(row, dict) and "tenant_id" not in _row_keys(row) and not embedded:
            row["tenant_id"] = tenant_id


def _is_from_statement(state) -> bool:
    from sqlalchemy.orm.context import FromStatement

    return isinstance(state.statement, FromStatement)


def _from_statement_tenant_entities(stmt) -> bool:
    tables = tenant_tables()
    for col in getattr(stmt, "_raw_columns", None) or ():
        if col in tables or getattr(col, "table", None) in tables:
            return True
    return False


def _system_writes(session: Session) -> tuple[set[str], set[int | None]]:
    """Tenant-owned tables and tenants a system-scope flush writes to."""
    from models.audit import AuditLog

    tables: set[str] = set()
    tenants: set[int | None] = set()
    for obj in list(session.new) + list(session.deleted):
        if isinstance(obj, TenantScoped) and not isinstance(obj, AuditLog):
            tables.add(type(obj).__tablename__)
            tenants.add(obj.tenant_id)
    for obj in session.dirty:
        if isinstance(obj, TenantScoped) and not isinstance(obj, AuditLog) \
                and session.is_modified(obj, include_collections=False):
            tables.add(type(obj).__tablename__)
            tenants.add(obj.tenant_id)
    return tables, tenants


def _before_flush(session: Session, flush_context, instances) -> None:
    ctx = _effective_context(session)
    if ctx is not None and ctx.system:
        tables, tenants = _system_writes(session)
        if tables:
            _audit_system_write(session, ctx, tables, tenants)
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
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantScoped):
            continue
        insp = sa_inspect(obj)
        if insp.persistent and insp.attrs.tenant_id.history.has_changes():
            # Also when the old value was never loaded (expired/deferred):
            # outside system scope an existing row never changes tenant.
            raise TenantContextError(
                f"{type(obj).__name__} cannot move between tenants outside system scope"
            )
        if obj in session.dirty and not session.is_modified(obj, include_collections=False):
            continue
        if ctx is None:
            raise TenantContextError(
                f"changing a {type(obj).__name__} without tenant context"
            )
        tid = obj.tenant_id  # loads it if expired (flush runs in the session's greenlet)
        if tid is not None and tid != ctx.tenant_id:
            # Loaded in another scope (system scope, another tenant) and
            # changed or deleted here.
            raise TenantContextError(
                f"{type(obj).__name__} of tenant {tid} changed in the context of tenant {ctx.tenant_id}"
            )


def _tenant_history(obj):
    hist = sa_inspect(obj).attrs.tenant_id.history
    return hist if hist.has_changes() else None


_installed = False


def _on_pending(session: Session, obj) -> None:
    """Stamp the tenant when an object is added, not when it is flushed: a
    row created for tenant A and flushed while B's context is active must be
    refused (before_flush compares), not land in B."""
    if not isinstance(obj, TenantScoped) or obj.tenant_id is not None:
        return
    ctx = _effective_context(session)
    if ctx is not None and not ctx.system and ctx.tenant_id is not None:
        obj.tenant_id = ctx.tenant_id


def install() -> None:
    """Install the filter on every SQLAlchemy session. Idempotent."""
    global _installed
    if _installed:
        return
    event.listen(Session, "do_orm_execute", _on_orm_execute)
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "transient_to_pending", _on_pending)
    _installed = True

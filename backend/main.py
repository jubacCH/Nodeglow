import os
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from logging_config import configure_logging  # first: later imports log at import time
configure_logging()
# Before anything resolves hostnames: cache getaddrinfo process-wide.
from utils import dns_cache

dns_cache.install()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import AsyncSessionLocal, get_setting, init_db
from scheduler import start_scheduler, stop_scheduler
from routers import (
    auth, dashboard, ping, setup, settings, users,
    syslog as syslog_router,
    system,
    integrations as integrations_router,
    agents as agents_router,
    subnet_scanner,
    credentials,
    snmp as snmp_router,
    ssl_monitor,
    update,
    api_v1,
    api_v2,
    rules as rules_router,
    digest as digest_router,
    bandwidth as bandwidth_router,
    backups as backups_router,
    maintenance as maintenance_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise OpenTelemetry first so subsequent SQLAlchemy / scheduler /
    # ClickHouse activity is captured. Safe no-op if no OTLP endpoint is set.
    from services.tracing import init_tracing
    init_tracing()

    await init_db()
    from models import init_db as init_new_db
    await init_new_db()
    # AI became opt-in: installations that already had a Claude key keep AI on (once).
    try:
        from services.ai_config import migrate_ai_opt_in
        await migrate_ai_opt_in()
    except Exception:
        import logging as _logging
        _logging.getLogger("nodeglow.ai").exception("AI opt-in upgrade check failed")
    # Telegram/Discord/webhook secrets used to be stored in plaintext.
    try:
        from services.channel_secrets import encrypt_plaintext_channel_secrets
        await encrypt_plaintext_channel_secrets()
    except Exception:
        import logging as _logging
        _logging.getLogger("nodeglow").exception("Encrypting notification channel secrets failed")
    await start_scheduler()
    os.environ["NODEGLOW_START_TIME"] = str(time.time())
    from services.syslog import start_syslog_server, stop_syslog_server
    try:
        async with AsyncSessionLocal() as _db:
            syslog_port = int(await get_setting(_db, "syslog_port", ""))
    except (ValueError, TypeError):
        syslog_port = int(os.environ.get("SYSLOG_PORT", "1514"))
    await start_syslog_server(udp_port=syslog_port, tcp_port=syslog_port)
    yield
    await stop_syslog_server()
    stop_scheduler()
    from utils.ping import close_http_clients
    await close_http_clients()


_debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
from config import get_version
from request_id import install as install_request_id
app = FastAPI(
    title="NODEGLOW",
    version=get_version(),
    description="Network monitoring and incident correlation platform",
    docs_url="/api/docs" if _debug else None,
    redoc_url="/api/redoc" if _debug else None,
    openapi_url="/api/openapi.json" if _debug else None,
    lifespan=lifespan,
)
install_request_id(app)  # outermost layer, regardless of where this line sits

app.mount("/static", StaticFiles(directory="static"), name="static")

# Wire FastAPI auto-instrumentation onto the running app instance.
# No-op if init_tracing() did not configure the OTel SDK.
try:
    from services.tracing import instrument_app as _otel_instrument_app
    _otel_instrument_app(app)
except Exception:
    pass

# ── CORS (configurable via CORS_ORIGINS env var, defaults to none in production) ──
from starlette.middleware.cors import CORSMiddleware

_cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-CSRF-Token"],
    )


@app.get("/api/v2/nav-counts")
async def nav_counts_api():
    """Sidebar badge counts for the Next.js frontend."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        counts = await _get_nav_counts(db)
    return counts


@app.post("/api/tasks/scan-all")
async def scan_all_hosts():
    """Trigger port discovery on all enabled hosts."""
    from services.port_discovery import run_port_discovery
    await run_port_discovery()
    return {"ok": True}


_READY_CHECK_TIMEOUT = 3.0  # seconds per dependency; probes must answer fast


async def _check_postgres() -> bool:
    import asyncio
    from sqlalchemy import text as sa_text

    async def _ping():
        async with AsyncSessionLocal() as db:
            await db.execute(sa_text("SELECT 1"))

    try:
        await asyncio.wait_for(_ping(), timeout=_READY_CHECK_TIMEOUT)
        return True
    except (OSError, SQLAlchemyError, asyncio.TimeoutError):
        return False
    except Exception:
        return False


async def _check_clickhouse() -> bool:
    import asyncio
    from services import clickhouse_client as _ch
    try:
        # get_client() retries with back-off on a cold start; bound it so a
        # probe never hangs for the whole retry ladder.
        await asyncio.wait_for(_ch.query_scalar("SELECT 1"), timeout=_READY_CHECK_TIMEOUT)
        return True
    except Exception:
        return False


@app.get("/livez")
async def livez():
    """Liveness: the process is up and serving. No dependency checks — a
    database outage must not make an orchestrator restart a healthy process."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: Postgres and ClickHouse both answer. 503 otherwise."""
    from fastapi.responses import JSONResponse
    pg_ok = await _check_postgres()
    ch_ok = await _check_clickhouse()
    ok = pg_ok and ch_ok
    return JSONResponse(
        {
            "status": "ok" if ok else "error",
            "db": "connected" if pg_ok else "connection failed",
            "clickhouse": "connected" if ch_ok else "connection failed",
        },
        status_code=200 if ok else 503,
    )


@app.get("/health")
async def health():
    """Backwards-compatible health check (Postgres only, same body as before).
    Now answers 503 instead of 200 when the database is unreachable, so a
    monitor that only looks at the status code sees the outage."""
    from fastapi.responses import JSONResponse
    if await _check_postgres():
        return {"status": "ok", "db": "connected"}
    return JSONResponse({"status": "error", "db": "connection failed"}, status_code=503)


@app.get("/metrics")
async def metrics():
    """Prometheus-format self-monitoring metrics. Auth-bypassed (see middleware)."""
    from fastapi.responses import Response
    from services.metrics import render_metrics
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# ── Nav counts cache (60s TTL, single GROUP BY query) ────────────────────────

_nav_cache: dict = {"counts": {}, "ts": 0.0}
_settings_cache: dict = {"site_name": "NODEGLOW", "timezone": "UTC", "ts": 0.0}
_NAV_CACHE_TTL = 60


def invalidate_settings_cache():
    """Call after saving settings to force refresh on next request."""
    _settings_cache["ts"] = 0.0


def invalidate_nav_cache():
    """Call after adding/removing integrations to force refresh on next request."""
    _nav_cache["ts"] = 0.0

_NAV_KEYS = (
    "proxmox", "unifi", "unas", "pihole", "adguard", "portainer",
    "truenas", "synology", "firewall", "hass", "gitea", "phpipam",
    "speedtest", "ups", "redfish", "swisscom", "cloudflare", "npm",
    "technitium",
)


async def _get_nav_counts(db) -> dict:
    now = time.time()
    if now - _nav_cache["ts"] < _NAV_CACHE_TTL and _nav_cache["counts"]:
        return _nav_cache["counts"]

    from services.integration import count_all_by_type
    from sqlalchemy import func
    from models.discovered_port import DiscoveredPort
    raw = await count_all_by_type(db)
    counts = {k: raw.get(k, 0) for k in _NAV_KEYS}

    # Count pending tasks (new ports + new SSL certs)
    new_ports = (await db.execute(
        select(func.count()).select_from(DiscoveredPort)
        .where(DiscoveredPort.last_open == True, DiscoveredPort.status == "new")
    )).scalar() or 0
    new_ssl = (await db.execute(
        select(func.count()).select_from(DiscoveredPort)
        .where(DiscoveredPort.last_open == True, DiscoveredPort.has_ssl == True, DiscoveredPort.ssl_status == "new")
    )).scalar() or 0
    counts["tasks"] = new_ports + new_ssl

    _nav_cache["counts"] = counts
    _nav_cache["ts"] = now
    return counts


@app.get("/api/tasks")
async def tasks_api():
    """Aggregate all pending admin tasks."""
    from models.discovered_port import DiscoveredPort
    from models.ping import PingHost

    async with AsyncSessionLocal() as db:
        # Discovered ports needing attention (new ports + new SSL)
        port_rows = (await db.execute(
            select(DiscoveredPort, PingHost.name.label("host_name"), PingHost.hostname.label("host_hostname"))
            .join(PingHost, PingHost.id == DiscoveredPort.host_id)
            .where(DiscoveredPort.last_open == True)
            .order_by(PingHost.name, DiscoveredPort.port)
        )).all()

        port_tasks = []
        ssl_tasks = []
        for row in port_rows:
            dp = row[0]
            host_name = row.host_name
            host_hostname = row.host_hostname
            base = {
                "id": dp.id, "host_id": dp.host_id,
                "host_name": host_name, "host_hostname": host_hostname,
                "port": dp.port, "protocol": dp.protocol,
                "service": dp.service,
                "first_seen": str(dp.first_seen) if dp.first_seen else None,
                "last_seen": str(dp.last_seen) if dp.last_seen else None,
            }
            port_tasks.append({
                **base,
                "status": dp.status,
            })
            if dp.has_ssl:
                ssl_tasks.append({
                    **base,
                    "ssl_issuer": dp.ssl_issuer,
                    "ssl_subject": dp.ssl_subject,
                    "ssl_expiry_days": dp.ssl_expiry_days,
                    "ssl_expiry_date": dp.ssl_expiry_date,
                    "ssl_status": dp.ssl_status,
                })

        # Summary counts
        new_ports = sum(1 for p in port_tasks if p["status"] == "new")
        new_ssl = sum(1 for s in ssl_tasks if s["ssl_status"] == "new")

        return {
            "port_tasks": port_tasks,
            "ssl_tasks": ssl_tasks,
            "summary": {
                "new_ports": new_ports,
                "new_ssl": new_ssl,
                "total_pending": new_ports + new_ssl,
            },
        }


_MUTATING_METHODS = ("POST", "PUT", "DELETE", "PATCH")
SESSION_COOKIE = "nodeglow_session"

# Paths that skip the middleware's auth entirely. Each one authenticates by
# other means (agent/install tokens, the login itself) or is a probe.
_AUTH_SKIP_EXACT = ("/health", "/livez", "/readyz", "/metrics")
_AUTH_SKIP_PREFIXES = (
    "/static/", "/api/agent/", "/api/auth/",
    "/api/docs", "/api/redoc", "/api/openapi",
    "/ws/", "/install/", "/agents/download/",
)

# Mutations a read-only user may still perform: only on their own account.
# (Login/logout live under /api/auth/, which never reaches the role check.)
_READONLY_MUTATION_ALLOWLIST = frozenset({"/users/me/password"})


@app.middleware("http")
async def inject_globals(request: Request, call_next):
    path = request.url.path
    # Skip auth entirely for these paths
    if path in _AUTH_SKIP_EXACT or path.startswith(_AUTH_SKIP_PREFIXES):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    is_api = path.startswith("/api/")
    is_api_v1 = path.startswith("/api/v1/")
    is_mutating = request.method in _MUTATING_METHODS

    # Generate CSRF token for this request (sets cookie on first visit)
    from csrf import generate_csrf_token, set_csrf_cookie, validate_csrf, csrf_error_response
    generate_csrf_token(request)

    # A request carrying X-API-Key is authenticated by that key ONLY — the
    # session cookie is never consulted for it (see below). That is what makes
    # skipping CSRF for it safe: a forged cross-site request riding on the
    # victim's cookie gains nothing when the cookie is ignored.
    has_api_key = bool(request.headers.get("X-API-Key"))

    # CSRF protection for state-changing methods. Skipped only for requests
    # authenticated by X-API-Key (header-only — query-string keys are rejected
    # by the auth layer anyway). /api/v1/ is no exception: the frontend calls
    # it with the session cookie, and SameSite=Strict must not be the only
    # thing standing between a forged request and an admin session. A /api/v1
    # request without a session cookie carries no ambient authority, so it is
    # left to the auth layer (401) instead of failing CSRF.
    csrf_required = is_mutating and not has_api_key and (
        not is_api_v1 or SESSION_COOKIE in request.cookies
    )
    if csrf_required:
        content_type = request.headers.get("content-type", "")
        form_data = None
        if "form" in content_type:
            body = await request.body()
            parsed = parse_qs(body.decode("utf-8", errors="replace"))
            form_data = {k: v[0] for k, v in parsed.items()}
        if not validate_csrf(request, form_data):
            return csrf_error_response(request)

    is_public = path.startswith("/setup")

    if not is_public:
        from database import is_setup_complete as _is_setup, get_current_user, AsyncSessionLocal as _ASL
        from fastapi.responses import JSONResponse as _JSON
        async with _ASL() as check_db:
            if not await _is_setup(check_db):
                if is_api:
                    return _JSON({"error": "Setup not complete"}, status_code=503)
                from fastapi.responses import RedirectResponse as _RR
                return _RR(url="/setup", status_code=302)
        user = None
        if not has_api_key:
            async with _ASL() as auth_db:
                user = await get_current_user(request, auth_db)
        if user is None:
            if is_api_v1 and has_api_key:
                # The route's require_api_key dependency validates the key.
                request.state.current_user = None
                return await call_next(request)
            if is_api:
                # Includes /api/v1/* without a key: refused here even when a
                # route forgot its auth dependency (defence in depth).
                return _JSON({"error": "Unauthorized"}, status_code=401)
            # Non-API HTML routes: redirect unauthenticated users to login
            if path not in ("/login", "/favicon.ico"):
                from fastapi.responses import RedirectResponse as _RR
                return _RR(url="/login", status_code=302)
            request.state.current_user = None
            response = await call_next(request)
            return response
        request.state.current_user = user
        role = getattr(user, "role", "admin") or "admin"
        if is_mutating:
            if path.startswith("/api/users") and role != "admin":
                return _JSON({"error": "Admin access required"}, status_code=403)
            # Read-only means read-only on every path, not only under /api/:
            # the frontend also proxies /rules/*, /settings/*, /hosts/api/* …
            if role == "readonly" and path not in _READONLY_MUTATION_ALLOWLIST:
                return _JSON({"error": "Read-only access"}, status_code=403)
    else:
        request.state.current_user = None

    # CSP nonce for inline scripts
    import secrets as _secrets
    request.state.csp_nonce = _secrets.token_urlsafe(16)

    response = await call_next(request)

    # Set CSRF cookie if newly generated
    set_csrf_cookie(request, response)

    # Security headers
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    nonce = getattr(request.state, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self' data:; "
        "frame-ancestors 'none'"
    )

    # HSTS — only if the request came over HTTPS (or via HTTPS proxy)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


# ── Global WebSocket ─────────────────────────────────────────────────────────
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    # Authenticate via session cookie before accepting
    from database import AsyncSessionLocal as _ASL
    from models.settings import Session as _Sess, User as _User, _hash_token as _ht, _hash_token_legacy as _ht_legacy
    from sqlalchemy import select as _sel
    from datetime import datetime as _dt
    token = websocket.cookies.get("nodeglow_session")
    if not token:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    async with _ASL() as _db:
        session = (await _db.execute(
            _sel(_Sess).where(_Sess.token == _ht(token), _Sess.expires_at > _dt.utcnow())
        )).scalar_one_or_none()
        if not session:
            # Fall back to legacy plain SHA256
            session = (await _db.execute(
                _sel(_Sess).where(_Sess.token == _ht_legacy(token), _Sess.expires_at > _dt.utcnow())
            )).scalar_one_or_none()
            if session:
                # Migrate legacy hash to HMAC
                session.token = _ht(token)
                await _db.commit()
        if not session:
            await websocket.close(code=4401, reason="Unauthorized")
            return
        user = (await _db.execute(_sel(_User).where(_User.id == session.user_id))).scalar_one_or_none()
        role = getattr(user, "role", "admin") or "admin"

    from services.websocket import register, unregister
    await register(websocket, role=role)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        unregister(websocket)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(setup.router)
app.include_router(ping.router)
app.include_router(settings.router)
app.include_router(syslog_router.router)
app.include_router(users.router)
app.include_router(users.api_router)
app.include_router(system.router)
app.include_router(integrations_router.router)
app.include_router(agents_router.router)
app.include_router(subnet_scanner.router)
app.include_router(credentials.router)
app.include_router(snmp_router.router)
app.include_router(ssl_monitor.router)
app.include_router(update.router)
app.include_router(api_v1.router)
app.include_router(api_v2.router)
app.include_router(rules_router.router)
app.include_router(digest_router.router)
app.include_router(bandwidth_router.router)
app.include_router(backups_router.router)
app.include_router(maintenance_router.router)

# ── Plugins (enterprise features under ee/, when present) ─────────────────────
# The loader is the only core module that knows ee/ exists; without it (or with
# NODEGLOW_DISABLE_EE=1) nothing is registered and this is the community
# edition. Plugin routers are mounted after the core ones.
import ee_loader  # noqa: E402

ee_loader.load_plugins()
ee_loader.mount_routers(app)

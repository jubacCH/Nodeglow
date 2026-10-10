"""API v2 — read models for the redesigned UI (concept E3).

Endpoints here aggregate what a screen needs into one response, built on the
same definitions the v1 endpoints use (services.host_state for host states,
services.incident_view for "open" incidents), so a number on the dashboard,
in the sidebar badge and in a filtered list can never disagree.

Authentication: the session (the UI) — or an API key through the same
dependency as /api/v1, although the middleware currently admits only
session callers to /api/v2/*. Endpoints about "me" need a session user.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_key import ApiKey
from models.base import get_db
from routers.api_v1 import _csv_filter, _parse_iso_utc, require_api_key
from services.host_state import iso

router = APIRouter(prefix="/api/v2", tags=["API v2"], dependencies=[Depends(require_api_key)])


def _session_user_id(request: Request) -> int:
    user = getattr(request.state, "current_user", None)
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(400, "This endpoint needs a signed-in user (session), not an API key.")
    return int(user_id)


# ── Edition + feature flags ──────────────────────────────────────────────────


@router.get("/features", summary="Edition and the features this installation offers")
async def features():
    """``{edition: "community"|"enterprise", features: {name: bool}}``.

    The UI shows enterprise features only when their flag is true. A flag says
    the feature is *installed*; whether it is switched on (e.g. the AI opt-in)
    is reported by the feature's own status endpoint (/api/v1/ai/status).
    """
    from extensions import registry
    return registry.feature_payload()


# ── Dashboard + summary ──────────────────────────────────────────────────────


@router.get("/dashboard", summary="Everything the E3 dashboard shows, in one call")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    key: ApiKey = Depends(require_api_key),
    since: str = Query(None, description="Override 'since your last visit' (ISO-8601); "
                                         "default: the user's stored last visit"),
):
    from fastapi.responses import JSONResponse

    from services import dashboard_v2

    user = getattr(request.state, "current_user", None)
    data = await dashboard_v2.build_dashboard(
        db, user_id=getattr(user, "id", None), is_admin=key.role == "admin",
        since_override=_parse_iso_utc(since, "since"),
    )
    return JSONResponse(data, headers={"Cache-Control": "no-cache"})


@router.get("/summary", summary="Consistent counts for the rail and sidebar badges")
async def summary(db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse

    from services import dashboard_v2

    return JSONResponse(await dashboard_v2.build_summary(db), headers={"Cache-Control": "no-cache"})


# ── Me ───────────────────────────────────────────────────────────────────────


@router.get("/me/seen", summary="When the signed-in user last marked the dashboard as seen")
async def get_seen(request: Request, db: AsyncSession = Depends(get_db)):
    from services import user_prefs

    seen = await user_prefs.dashboard_seen_at(db, _session_user_id(request))
    return {"dashboard_seen_at": iso(seen)}


@router.post("/me/seen", summary="Mark the dashboard as seen now (last visit)")
async def mark_seen(request: Request, db: AsyncSession = Depends(get_db)):
    from services import user_prefs

    now, previous = await user_prefs.mark_dashboard_seen(db, _session_user_id(request))
    return {"dashboard_seen_at": iso(now), "previous_seen_at": iso(previous)}


# ── Changes ──────────────────────────────────────────────────────────────────


@router.get("/changes", summary="Change feed since a point in time")
async def changes(
    request: Request,
    db: AsyncSession = Depends(get_db),
    key: ApiKey = Depends(require_api_key),
    since: str = Query(..., description="ISO-8601; at most 31 days back (older is clamped)"),
    until: str = Query(None, description="ISO-8601, default now"),
    types: str = Query(None, description="Comma-separated subset of the change types"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
):
    from services import changes as changes_svc

    since_dt = _parse_iso_utc(since, "since")
    until_dt = _parse_iso_utc(until, "until") or datetime.utcnow()
    if since_dt >= until_dt:
        raise HTTPException(400, "since must be before until")
    wanted = _csv_filter(types, changes_svc.TYPES, "types")
    return await changes_svc.collect(
        db, since_dt, until_dt, limit=limit, offset=offset, types=wanted,
        is_admin=key.role == "admin",
    )

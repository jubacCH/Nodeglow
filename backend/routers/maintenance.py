"""Maintenance windows — CRUD under /api/v1/maintenance-windows.

Reading needs an identity (session or API key); changing a window needs the
editor role, like every other mutation of monitoring state. Whether a window
is active is computed on the fly (services/maintenance.py), so the list shows
the live state and the next occurrence for each window.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_key import ApiKey
from models.base import get_db
from models.maintenance import MaintenanceWindow
from models.ping import PingHost
from models.settings import get_setting
from routers.api_v1 import require_api_key, require_editor
from services import maintenance as maint
from services.audit import log_action

router = APIRouter(prefix="/api/v1/maintenance-windows", tags=["Maintenance windows"])

MAX_ONE_OFF_SPAN = timedelta(days=366)
_FIELDS = ("name", "enabled", "kind", "weekdays", "start_time", "duration_minutes",
           "starts_at", "ends_at", "timezone", "all_hosts", "host_ids")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _serialize(row: MaintenanceWindow, now: datetime) -> dict:
    spec = maint.spec_from_row(row)
    current = maint.occurrence_at(spec, now)
    upcoming = maint.next_occurrence(spec, now)
    return {
        "id": row.id,
        "name": row.name,
        "enabled": bool(row.enabled),
        "kind": row.kind,
        "weekdays": sorted(spec.weekdays),
        "start_time": row.start_time,
        "duration_minutes": row.duration_minutes,
        "starts_at": _iso(row.starts_at),
        "ends_at": _iso(row.ends_at),
        "timezone": row.timezone,
        "all_hosts": bool(row.all_hosts),
        "host_ids": sorted(spec.host_ids),
        "active": current is not None,
        "current": {"start": _iso(current[0]), "end": _iso(current[1])} if current else None,
        "next": {"start": _iso(upcoming[0]), "end": _iso(upcoming[1])} if upcoming else None,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HTTPException(400, f"{field} must be true or false")


async def _validate(db: AsyncSession, data: dict) -> dict:
    """Turn a (merged) request body into column values, or raise 400."""
    unknown = set(data) - set(_FIELDS)
    if unknown:
        raise HTTPException(400, f"unknown field(s): {', '.join(sorted(unknown))}")
    try:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        if len(name) > 128:
            raise ValueError("name is longer than 128 characters")

        kind = data.get("kind") or "weekly"
        if kind not in maint.KINDS:
            raise ValueError("kind must be 'weekly' or 'once'")

        tz_name = data.get("timezone")
        if not tz_name:
            tz_name = await get_setting(db, "timezone", "UTC") or "UTC"
        tz_name = maint.parse_tz(tz_name)

        out: dict = {
            "name": name, "kind": kind, "timezone": tz_name,
            "enabled": _bool(data.get("enabled", True), "enabled"),
            "all_hosts": _bool(data.get("all_hosts", False), "all_hosts"),
            "weekdays": None, "start_time": None, "duration_minutes": None,
            "starts_at": None, "ends_at": None,
        }

        if kind == "weekly":
            days = maint.parse_weekdays(data.get("weekdays"))
            if not days:
                raise ValueError("pick at least one weekday")
            start = maint.parse_start_time(data.get("start_time"))
            try:
                duration = int(data.get("duration_minutes"))
            except (TypeError, ValueError):
                raise ValueError("duration_minutes must be a whole number") from None
            if not (1 <= duration <= maint.MAX_DURATION_MINUTES):
                raise ValueError(f"duration_minutes must be between 1 and {maint.MAX_DURATION_MINUTES}")
            out.update(weekdays=",".join(str(d) for d in sorted(days)),
                       start_time=start.strftime("%H:%M"), duration_minutes=duration)
        else:
            if not data.get("starts_at") or not data.get("ends_at"):
                raise ValueError("starts_at and ends_at are required for a one-off window")
            starts = maint.to_naive_utc(data["starts_at"], tz_name)
            ends = maint.to_naive_utc(data["ends_at"], tz_name)
            if ends <= starts:
                raise ValueError("ends_at must be after starts_at")
            if ends - starts > MAX_ONE_OFF_SPAN:
                raise ValueError("a one-off window may last at most 366 days")
            out.update(starts_at=starts, ends_at=ends)

        ids = maint.parse_host_ids(data.get("host_ids"))
        if not out["all_hosts"]:
            if not ids:
                raise ValueError("pick at least one host, or apply the window to all hosts")
            known = set((await db.execute(
                select(PingHost.id).where(PingHost.id.in_(list(ids)))
            )).scalars().all())
            missing = sorted(ids - known)
            if missing:
                raise ValueError(f"unknown host id(s): {', '.join(map(str, missing[:10]))}")
        out["host_ids"] = json.dumps(sorted(ids)) if (ids and not out["all_hosts"]) else None
        return out
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _row_as_input(row: MaintenanceWindow) -> dict:
    """An existing row in request-body shape, for PATCH merging."""
    return {
        "name": row.name, "enabled": bool(row.enabled), "kind": row.kind,
        "weekdays": row.weekdays, "start_time": row.start_time,
        "duration_minutes": row.duration_minutes,
        # Stored as naive UTC; mark them so they are not re-read as local time.
        "starts_at": _iso(row.starts_at), "ends_at": _iso(row.ends_at),
        "timezone": row.timezone, "all_hosts": bool(row.all_hosts),
        "host_ids": row.host_ids,
    }


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON body") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    return body


@router.get("", summary="List maintenance windows")
async def list_windows(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    rows = (await db.execute(
        select(MaintenanceWindow).order_by(MaintenanceWindow.name)
    )).scalars().all()
    now = datetime.utcnow()
    return [_serialize(r, now) for r in rows]


@router.get("/{window_id}", summary="One maintenance window")
async def get_window(
    window_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    row = await db.get(MaintenanceWindow, window_id)
    if not row:
        raise HTTPException(404, "Maintenance window not found")
    return _serialize(row, datetime.utcnow())


@router.post("", summary="Create a maintenance window")
async def create_window(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    values = await _validate(db, await _body(request))
    row = MaintenanceWindow(**values)
    db.add(row)
    await db.flush()
    await log_action(db, request, "maintenance_window.create", "maintenance_window",
                     row.id, row.name)
    await db.commit()
    await db.refresh(row)
    return _serialize(row, datetime.utcnow())


@router.patch("/{window_id}", summary="Update a maintenance window")
async def update_window(
    window_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    row = await db.get(MaintenanceWindow, window_id)
    if not row:
        raise HTTPException(404, "Maintenance window not found")
    merged = {**_row_as_input(row), **(await _body(request))}
    values = await _validate(db, merged)
    for key, val in values.items():
        setattr(row, key, val)
    row.updated_at = datetime.utcnow()
    await log_action(db, request, "maintenance_window.update", "maintenance_window",
                     row.id, row.name)
    await db.commit()
    await db.refresh(row)
    return _serialize(row, datetime.utcnow())


@router.delete("/{window_id}", summary="Delete a maintenance window")
async def delete_window(
    window_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    row = await db.get(MaintenanceWindow, window_id)
    if not row:
        raise HTTPException(404, "Maintenance window not found")
    name = row.name
    await db.delete(row)
    await log_action(db, request, "maintenance_window.delete", "maintenance_window",
                     window_id, name)
    await db.commit()
    return {"ok": True}

"""Settings, System: the external monitor that notices when Nodeglow stops."""
import asyncio
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, set_setting
from ratelimit import rate_limit
from services import heartbeat
from services.audit import log_action

from ._helpers import require_operator

router = APIRouter()


async def _state(db: AsyncSession) -> dict:
    mode, _, source = await heartbeat.resolve(db)
    return heartbeat.status(time.time(), mode, source)


@router.get("/heartbeat")
async def heartbeat_get(request: Request, db: AsyncSession = Depends(get_db)):
    """The external monitor as configured, and how its requests fare. The
    URL itself is a credential and is never returned."""
    if err := require_operator(request):
        return err
    return JSONResponse(await _state(db))


@router.post("/heartbeat/save")
@rate_limit(max_requests=10, window_seconds=60)
async def heartbeat_save(request: Request, db: AsyncSession = Depends(get_db)):
    """Body: ``{"mode": "url", "url": "https://..."}``, ``{"mode": "off"}``
    or ``{"mode": "unset"}`` (back to the environment variable, if any).
    ``mode: url`` without a URL keeps the saved one."""
    if err := require_operator(request):
        return err
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    mode = str((body or {}).get("mode") or "").strip()
    url = str((body or {}).get("url") or "").strip()

    if mode == "url":
        if url:
            if problem := await asyncio.to_thread(heartbeat.url_problem, url):
                return JSONResponse({"error": problem, "code": "heartbeat_url"}, status_code=400)
            await set_setting(db, heartbeat.SETTING, url, encrypted=True)
        elif (await heartbeat.resolve(db))[0] != "set":
            return JSONResponse({"error": "Enter the check URL of the monitor",
                                 "code": "heartbeat_url"}, status_code=400)
    elif mode == "off":
        await set_setting(db, heartbeat.SETTING, "off")
    elif mode == "unset":
        await set_setting(db, heartbeat.SETTING, "")
    else:
        return JSONResponse({"error": "mode must be url, off or unset"}, status_code=400)

    # What was counted belongs to the previous target.
    heartbeat.reset()
    await log_action(db, request, "settings.update", "setting", target_name="heartbeat")
    await db.commit()
    return JSONResponse({"ok": True, **await _state(db)})


@router.post("/heartbeat/test")
@rate_limit(max_requests=10, window_seconds=60)
async def heartbeat_test(request: Request, db: AsyncSession = Depends(get_db)):
    """Request the configured monitor once, now."""
    if err := require_operator(request):
        return err
    mode, url, _ = await heartbeat.resolve(db)
    if mode != "set":
        return JSONResponse({"ok": False, "error": "No monitor URL is configured"}, status_code=400)
    error = await heartbeat.request(url)
    if error is None:
        heartbeat.record_ok(time.time())
    return JSONResponse({"ok": error is None, "error": error})

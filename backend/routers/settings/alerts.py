"""Integration alert thresholds (Settings -> Monitoring)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import integration_thresholds
from services.audit import log_action
from services.integration import count_all_by_type

from ._helpers import require_admin

router = APIRouter()


@router.get("/integration-thresholds")
async def integration_thresholds_list(request: Request, all: bool = False,
                                      db: AsyncSession = Depends(get_db)):
    """The limits of the configured integration types (``all=1``: every type)."""
    types = None if all else set(await count_all_by_type(db))
    return JSONResponse(await integration_thresholds.catalogue(db, types))


@router.post("/integration-thresholds/save")
async def integration_thresholds_save(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    body = await request.json()
    values = body.get("values") if isinstance(body, dict) else None
    if not isinstance(values, dict):
        return JSONResponse({"error": "values must be an object"}, status_code=400)
    refused = await integration_thresholds.save(db, values)
    if refused:
        return JSONResponse({"error": "Some values are out of range", "refused": refused}, status_code=400)
    await log_action(db, request, "settings.update", target_name="integration alert thresholds",
                     details={"values": values})
    return JSONResponse({"ok": True})

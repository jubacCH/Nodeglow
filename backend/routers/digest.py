"""Weekly Digest page — summary of the last 7 days."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.digest import build_weekly_digest
from templating import templates

router = APIRouter()


@router.get("/digest", response_class=HTMLResponse)
async def digest_page(request: Request, db: AsyncSession = Depends(get_db)):
    data = await build_weekly_digest(db)
    return templates.TemplateResponse("digest.html", {
        "request": request,
        "digest": data,
        "active_page": "digest",
    })


@router.get("/api/v1/digest")
async def digest_api(request: Request, db: AsyncSession = Depends(get_db)):
    data = await build_weekly_digest(db)
    # Serialize for JSON (convert datetimes, ORM objects)
    result = {
        "period_start": data["period_start"].isoformat(),
        "period_end": data["period_end"].isoformat(),
        "incidents": {
            "total": data["incidents"]["total"],
            "by_severity": data["incidents"]["by_severity"],
            "by_status": data["incidents"]["by_status"],
            "mttr_min": data["incidents"]["mttr_min"],
        },
        "hosts": {
            "total": data["hosts"]["total"],
            "avg_uptime": data["hosts"]["avg_uptime"],
            "worst": data["hosts"]["worst"],
        },
        "syslog": data["syslog"],
        "integrations": data["integrations"],
        "storage_predictions": data["storage_predictions"],
        "ssl_expiring": data["ssl_expiring"],
    }
    return JSONResponse(result)

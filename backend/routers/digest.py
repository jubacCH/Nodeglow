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
    # Serialize top incidents
    top_incidents = []
    for inc in data["incidents"].get("top", []):
        top_incidents.append({
            "id": inc.id,
            "title": inc.title,
            "severity": inc.severity,
        })
    # Serialize worst hosts
    worst_hosts = []
    for h in data["hosts"].get("worst", []):
        if isinstance(h, dict):
            worst_hosts.append({
                "id": h.get("id", 0),
                "name": h.get("name", ""),
                "uptime": h.get("uptime_pct", 0),
                "failures": h.get("failures", 0),
            })
        else:
            worst_hosts.append({
                "id": getattr(h, "id", 0),
                "name": getattr(h, "name", ""),
                "uptime": getattr(h, "uptime_pct", 0),
                "failures": getattr(h, "failures", 0),
            })
    result = {
        "period_start": data["period_start"].isoformat(),
        "period_end": data["period_end"].isoformat(),
        "incidents": {
            "total": data["incidents"]["total"],
            "by_severity": data["incidents"]["by_severity"],
            "by_status": data["incidents"].get("by_status", {}),
            "mttr_minutes": data["incidents"]["mttr_min"],
            "top": top_incidents,
        },
        "hosts": {
            "total": data["hosts"].get("total", 0),
            "avg_uptime": data["hosts"]["avg_uptime"],
            "worst": worst_hosts,
        },
        "syslog": {
            "total": data["syslog"].get("total", 0),
            "errors": data["syslog"].get("errors", 0),
            "top_errors": [
                {"template": t["template"], "count": t["count"], "noise_score": t.get("noise_score")}
                for t in data["syslog"].get("top_templates", [])
            ],
        },
        "integrations": [
            {
                "name": i["name"],
                "type": i["type"],
                "success_rate": i["success_rate"],
                "total_snapshots": i["total_snapshots"],
                "failures": i["failures"],
            }
            for i in data.get("integrations", [])
        ],
        "storage_predictions": [
            {
                "host": p.get("host", ""),
                "disk": p.get("disk", ""),
                "days_until_full": p.get("days_until_full"),
                "current_usage_pct": p.get("current_usage_pct"),
                "confidence": p.get("confidence"),
            }
            for p in data.get("storage_predictions", [])
        ],
        "ssl_expiring": data.get("ssl_expiring", []),
    }
    return JSONResponse(result)

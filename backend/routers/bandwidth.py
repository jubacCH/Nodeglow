"""
Bandwidth router — REST API for bandwidth/traffic monitoring data.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from services import bandwidth as bw_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/bandwidth")
async def bandwidth_summary():
    """Return bandwidth summary: top talkers, totals, per-source aggregates."""
    try:
        return await bw_svc.get_bandwidth_summary()
    except Exception as exc:
        logger.error("Bandwidth summary error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/bandwidth/history")
async def bandwidth_history(
    source_type: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    interface: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=168),
):
    """Return time-series bandwidth data for charting."""
    try:
        data = await bw_svc.get_bandwidth_history(
            source_type=source_type,
            source_id=source_id,
            interface_name=interface,
            hours=hours,
        )
        return {"samples": data, "count": len(data)}
    except Exception as exc:
        logger.error("Bandwidth history error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/bandwidth/interfaces")
async def bandwidth_interfaces():
    """List all known interfaces with latest rates."""
    try:
        interfaces = await bw_svc.get_bandwidth_interfaces()
        return {"interfaces": interfaces, "count": len(interfaces)}
    except Exception as exc:
        logger.error("Bandwidth interfaces error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

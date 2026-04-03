"""
Backup monitoring API router.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import get_db
from services import backup_monitor as bk_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
async def backup_summary(db: AsyncSession = Depends(get_db)):
    """Get summary of all backup jobs with status."""
    summary = await bk_svc.get_backup_summary(db)
    return JSONResponse(summary)


@router.get("/compliance")
async def backup_compliance(db: AsyncSession = Depends(get_db)):
    """Check all backup jobs for compliance issues (overdue, failed, never-run)."""
    issues = await bk_svc.check_backup_compliance(db)
    return JSONResponse({"issues": issues, "count": len(issues)})


@router.get("/{job_id}")
async def backup_detail(job_id: int, db: AsyncSession = Depends(get_db)):
    """Get detail for a single backup job."""
    detail = await bk_svc.get_job_detail(db, job_id)
    if not detail:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(detail)


@router.get("/{job_id}/history")
async def backup_history(
    job_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Get history entries for a backup job."""
    from models.backup import BackupJob
    job = await db.get(BackupJob, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    entries = await bk_svc.get_job_history(db, job_id, limit=limit)
    return JSONResponse({"job_id": job_id, "entries": entries})


@router.post("/{job_id}/settings")
async def update_job_settings(
    request: Request,
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Update backup job settings (expected_frequency, enabled)."""
    from models.backup import BackupJob

    job = await db.get(BackupJob, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    body = await request.json()

    if "expected_frequency_hours" in body:
        val = body["expected_frequency_hours"]
        if isinstance(val, int) and val > 0:
            job.expected_frequency_hours = val
        else:
            return JSONResponse(
                {"error": "expected_frequency_hours must be a positive integer"},
                status_code=400,
            )

    if "enabled" in body:
        job.enabled = bool(body["enabled"])

    await db.commit()
    return JSONResponse({"ok": True, "job_id": job_id})


@router.post("/sync")
async def trigger_sync(db: AsyncSession = Depends(get_db)):
    """Trigger manual backup sync from all integration sources."""
    try:
        results = await bk_svc.sync_all_sources(db)
        return JSONResponse({"ok": True, "results": results})
    except Exception as exc:
        logger.error("Manual backup sync failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

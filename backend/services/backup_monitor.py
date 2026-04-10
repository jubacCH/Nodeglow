"""
Backup monitoring service – sync backup data from integrations, check compliance.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.backup import BackupJob, BackupHistory

logger = logging.getLogger(__name__)


# ── Proxmox sync ────────────────────────────────────────────────────────────


async def sync_proxmox_backups(
    db: AsyncSession,
    config_id: int,
    proxmox_data: dict,
) -> dict[str, int]:
    """
    Parse backup data from a Proxmox snapshot and create/update BackupJob + BackupHistory.

    Expected keys in proxmox_data:
      - tasks: list of cluster tasks (already parsed by proxmox.py)
      - backups: dict of {storage_name: [backup_file_entries]} from storage content API
      - vms / containers: for name lookups
    """
    created = updated = history_added = 0

    # Build VMID → name map from VMs and containers
    vmid_names: dict[int, str] = {}
    for guest in proxmox_data.get("vms", []) + proxmox_data.get("containers", []):
        gid = guest.get("id")
        if gid:
            vmid_names[int(gid)] = guest.get("name", f"guest-{gid}")

    # ── 1. Process backup files from storage content ─────────────────────────
    backup_files: dict[str, list[dict]] = proxmox_data.get("backups", {})
    # Group backup files by (vmid, storage) to find the latest per guest
    # Each entry: {"volid": ..., "size": ..., "ctime": ..., "vmid": ..., "format": ...}
    guest_latest: dict[tuple[int, str], dict] = {}  # (vmid, storage) -> best file

    for storage_name, files in backup_files.items():
        for f in files:
            vmid = f.get("vmid")
            if vmid is None:
                continue
            vmid = int(vmid)
            ctime = f.get("ctime", 0)
            key = (vmid, storage_name)
            if key not in guest_latest or ctime > guest_latest[key].get("ctime", 0):
                guest_latest[key] = {**f, "_storage": storage_name}

    for (vmid, storage_name), info in guest_latest.items():
        guest_name = vmid_names.get(vmid, f"guest-{vmid}")
        job_name = f"vzdump-{vmid}-{guest_name}"

        job = await _get_or_create_job(
            db,
            source_type="proxmox",
            source_config_id=config_id,
            target_vmid=vmid,
            defaults={
                "name": job_name,
                "target_name": guest_name,
                "storage_name": storage_name,
            },
        )
        if job._is_new:
            created += 1
        else:
            updated += 1

        ctime = info.get("ctime", 0)
        size = info.get("size", 0)
        if ctime:
            run_dt = datetime.utcfromtimestamp(ctime)
            job.last_run_at = run_dt
            job.last_size_bytes = size
            job.last_status = "ok"
            job.last_error = None
            job.storage_name = storage_name

            # Add history if not already recorded
            exists = await db.execute(
                select(BackupHistory.id).where(
                    BackupHistory.job_id == job.id,
                    BackupHistory.timestamp == run_dt,
                ).limit(1)
            )
            if not exists.scalar_one_or_none():
                db.add(BackupHistory(
                    job_id=job.id,
                    timestamp=run_dt,
                    status="ok",
                    size_bytes=size,
                    details_json=json.dumps({"volid": info.get("volid", "")}),
                ))
                history_added += 1

    # ── 2. Process backup tasks from cluster tasks ───────────────────────────
    for task in proxmox_data.get("tasks", []):
        task_type = task.get("type", "")
        task_id = str(task.get("id", ""))

        # Filter for vzdump (backup) tasks
        if "vzdump" not in task_type and "backup" not in task_type.lower():
            continue

        # Parse VMID from task id (format: "VMID" or various forms)
        vmid = _parse_vmid_from_task(task_id, task_type)
        if vmid is None:
            continue

        guest_name = vmid_names.get(vmid, f"guest-{vmid}")
        job_name = f"vzdump-{vmid}-{guest_name}"

        task_status = task.get("status", "running")
        is_ok = task.get("ok")
        starttime_str = task.get("starttime")

        if not starttime_str:
            continue

        try:
            run_dt = datetime.strptime(starttime_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

        # Map task status
        if task_status == "running":
            mapped_status = "running"
        elif is_ok is True or task_status == "OK":
            mapped_status = "ok"
        elif is_ok is False or "error" in task_status.lower() or "WARNINGS" in task_status:
            mapped_status = "failed" if is_ok is False else "warning"
        else:
            mapped_status = "unknown"

        job = await _get_or_create_job(
            db,
            source_type="proxmox",
            source_config_id=config_id,
            target_vmid=vmid,
            defaults={
                "name": job_name,
                "target_name": guest_name,
            },
        )
        if job._is_new:
            created += 1

        # Only update last_run if this task is newer
        if job.last_run_at is None or run_dt >= job.last_run_at:
            job.last_run_at = run_dt
            job.last_status = mapped_status
            if mapped_status == "failed":
                job.last_error = task_status
            elif mapped_status in ("ok", "running"):
                job.last_error = None

        # Add history entry for completed tasks
        if mapped_status != "running":
            exists = await db.execute(
                select(BackupHistory.id).where(
                    BackupHistory.job_id == job.id,
                    BackupHistory.timestamp == run_dt,
                ).limit(1)
            )
            if not exists.scalar_one_or_none():
                db.add(BackupHistory(
                    job_id=job.id,
                    timestamp=run_dt,
                    status=mapped_status,
                    error=task_status if mapped_status == "failed" else None,
                    details_json=json.dumps({
                        "node": task.get("node", ""),
                        "user": task.get("user", ""),
                    }),
                ))
                history_added += 1

    await db.flush()
    return {"created": created, "updated": updated, "history_added": history_added}


def _parse_vmid_from_task(task_id: str, task_type: str) -> int | None:
    """Extract VMID from Proxmox task ID string."""
    # task_id is usually just the VMID number as a string
    try:
        return int(task_id)
    except (ValueError, TypeError):
        pass
    # Try extracting from patterns like "vzdump:100" or "100:disk-0"
    import re
    m = re.search(r"(\d+)", task_id)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


async def _get_or_create_job(
    db: AsyncSession,
    source_type: str,
    source_config_id: int,
    target_vmid: int | None = None,
    defaults: dict | None = None,
) -> BackupJob:
    """Find existing job by (source_type, source_config_id, target_vmid) or create new."""
    q = select(BackupJob).where(
        BackupJob.source_type == source_type,
        BackupJob.source_config_id == source_config_id,
    )
    if target_vmid is not None:
        q = q.where(BackupJob.target_vmid == target_vmid)
    else:
        q = q.where(BackupJob.target_name == (defaults or {}).get("target_name", ""))

    result = await db.execute(q.limit(1))
    job = result.scalar_one_or_none()

    if job:
        job._is_new = False  # type: ignore[attr-defined]
        # Update name if it changed
        if defaults and defaults.get("target_name"):
            job.target_name = defaults["target_name"]
        return job

    defs = defaults or {}
    job = BackupJob(
        name=defs.get("name", f"backup-{source_type}"),
        source_type=source_type,
        source_config_id=source_config_id,
        target_name=defs.get("target_name", "unknown"),
        target_vmid=target_vmid,
        storage_name=defs.get("storage_name"),
    )
    job._is_new = True  # type: ignore[attr-defined]
    db.add(job)
    await db.flush()
    return job


# ── UNAS sync ───────────────────────────────────────────────────────────────


async def sync_unas_backups(
    db: AsyncSession,
    config_id: int,
    unas_data: dict,
) -> dict[str, int]:
    """
    Check for snapshot/replication data from UNAS API and create BackupJob entries.
    UNAS data may include datasets with snapshot info depending on the API version.
    """
    created = updated = 0

    # Look for datasets with snapshot data
    datasets = unas_data.get("datasets", [])
    for ds in datasets:
        ds_name = ds.get("name", "")
        if not ds_name:
            continue

        snapshots = ds.get("snapshots", [])
        if not snapshots:
            continue

        job = await _get_or_create_job(
            db,
            source_type="unas",
            source_config_id=config_id,
            defaults={
                "name": f"snapshot-{ds_name}",
                "target_name": ds_name,
            },
        )
        if job._is_new:
            created += 1
        else:
            updated += 1

        # Find most recent snapshot
        latest_snap = max(snapshots, key=lambda s: s.get("created", ""), default=None)
        if latest_snap:
            try:
                snap_time = datetime.fromisoformat(latest_snap["created"].replace("Z", "+00:00"))
                snap_time = snap_time.replace(tzinfo=None)  # store as naive UTC
            except (ValueError, KeyError):
                snap_time = None

            if snap_time:
                job.last_run_at = snap_time
                job.last_status = "ok"
                job.last_error = None

    await db.flush()
    return {"created": created, "updated": updated}


# ── Compliance check ────────────────────────────────────────────────────────


async def check_backup_compliance(db: AsyncSession) -> list[dict]:
    """
    Check all enabled BackupJobs for overdue or failed backups.
    Returns list of issues: [{"job": ..., "issue": "overdue"|"failed", "hours_since": ...}]
    """
    now = datetime.utcnow()
    result = await db.execute(
        select(BackupJob).where(BackupJob.enabled == True)
    )
    jobs = result.scalars().all()

    issues: list[dict] = []
    for job in jobs:
        if job.last_status == "failed":
            issues.append({
                "job_id": job.id,
                "job_name": job.name,
                "target_name": job.target_name,
                "source_type": job.source_type,
                "issue": "failed",
                "last_status": job.last_status,
                "last_error": job.last_error,
                "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
            })

        if job.last_run_at:
            hours_since = (now - job.last_run_at).total_seconds() / 3600
            if hours_since > job.expected_frequency_hours:
                issues.append({
                    "job_id": job.id,
                    "job_name": job.name,
                    "target_name": job.target_name,
                    "source_type": job.source_type,
                    "issue": "overdue",
                    "hours_since": round(hours_since, 1),
                    "expected_hours": job.expected_frequency_hours,
                    "last_run_at": job.last_run_at.isoformat(),
                })
        elif job.last_status == "unknown":
            issues.append({
                "job_id": job.id,
                "job_name": job.name,
                "target_name": job.target_name,
                "source_type": job.source_type,
                "issue": "never_run",
                "last_status": "unknown",
            })

    return issues


# ── Summary ─────────────────────────────────────────────────────────────────


async def get_backup_summary(db: AsyncSession) -> dict:
    """
    Get a full summary of all backup jobs with their status.
    """
    now = datetime.utcnow()
    result = await db.execute(
        select(BackupJob).order_by(BackupJob.source_type, BackupJob.target_name)
    )
    jobs = result.scalars().all()

    total = len(jobs)
    healthy = 0
    warning = 0
    failed = 0
    unknown = 0
    overdue: list[dict] = []

    job_list: list[dict] = []
    for job in jobs:
        hours_since = None
        is_overdue = False
        if job.last_run_at:
            hours_since = round((now - job.last_run_at).total_seconds() / 3600, 1)
            if hours_since > job.expected_frequency_hours:
                is_overdue = True

        effective_status = job.last_status
        if effective_status == "ok" and is_overdue:
            effective_status = "warning"

        if effective_status == "ok":
            healthy += 1
        elif effective_status in ("warning", "running"):
            warning += 1
        elif effective_status == "failed":
            failed += 1
        else:
            unknown += 1

        entry = {
            "id": job.id,
            "name": job.name,
            "source_type": job.source_type,
            "source_config_id": job.source_config_id,
            "target_name": job.target_name,
            "target_vmid": job.target_vmid,
            "storage_name": job.storage_name,
            "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
            "last_status": job.last_status,
            "effective_status": effective_status,
            "last_duration_sec": job.last_duration_sec,
            "last_size_bytes": job.last_size_bytes,
            "last_error": job.last_error,
            "expected_frequency_hours": job.expected_frequency_hours,
            "hours_since_last": hours_since,
            "is_overdue": is_overdue,
            "enabled": job.enabled,
        }
        job_list.append(entry)

        if is_overdue:
            overdue.append(entry)

    return {
        "total": total,
        "healthy": healthy,
        "warning": warning,
        "failed": failed,
        "unknown": unknown,
        "overdue": overdue,
        "jobs": job_list,
    }


# ── Job history ─────────────────────────────────────────────────────────────


async def get_job_detail(db: AsyncSession, job_id: int) -> dict | None:
    """Get a single backup job with recent history."""
    job = await db.get(BackupJob, job_id)
    if not job:
        return None

    now = datetime.utcnow()
    hours_since = None
    is_overdue = False
    if job.last_run_at:
        hours_since = round((now - job.last_run_at).total_seconds() / 3600, 1)
        if hours_since > job.expected_frequency_hours:
            is_overdue = True

    return {
        "id": job.id,
        "name": job.name,
        "source_type": job.source_type,
        "source_config_id": job.source_config_id,
        "target_name": job.target_name,
        "target_vmid": job.target_vmid,
        "storage_name": job.storage_name,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status,
        "last_duration_sec": job.last_duration_sec,
        "last_size_bytes": job.last_size_bytes,
        "last_error": job.last_error,
        "expected_frequency_hours": job.expected_frequency_hours,
        "hours_since_last": hours_since,
        "is_overdue": is_overdue,
        "enabled": job.enabled,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


async def get_job_history(
    db: AsyncSession,
    job_id: int,
    limit: int = 100,
) -> list[dict]:
    """Get history entries for a backup job, newest first."""
    result = await db.execute(
        select(BackupHistory)
        .where(BackupHistory.job_id == job_id)
        .order_by(BackupHistory.timestamp.desc())
        .limit(limit)
    )
    entries = result.scalars().all()
    return [
        {
            "id": e.id,
            "job_id": e.job_id,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "status": e.status,
            "duration_sec": e.duration_sec,
            "size_bytes": e.size_bytes,
            "error": e.error,
            "details": json.loads(e.details_json) if e.details_json else None,
        }
        for e in entries
    ]


# ── Cleanup ─────────────────────────────────────────────────────────────────


async def cleanup_old_history(db: AsyncSession, days: int = 90) -> int:
    """Remove backup history records older than `days`. Returns count deleted."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        delete(BackupHistory).where(BackupHistory.timestamp < cutoff)
    )
    await db.flush()
    return result.rowcount or 0


# ── Sync all sources ────────────────────────────────────────────────────────


async def sync_all_sources(db: AsyncSession) -> dict[str, Any]:
    """Trigger backup sync from all configured integration sources."""
    import json as _json
    from models.integration import IntegrationConfig
    from services import snapshot as snap_svc

    results: dict[str, Any] = {}

    # Proxmox sources
    pxconfigs = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.type == "proxmox",
            IntegrationConfig.enabled == True,
        )
    )
    for cfg in pxconfigs.scalars().all():
        snap = await snap_svc.get_latest(db, "proxmox", cfg.id)
        if snap and snap.data_json:
            data = _json.loads(snap.data_json)
            try:
                r = await sync_proxmox_backups(db, cfg.id, data)
                results[f"proxmox/{cfg.name}"] = r
            except Exception as exc:
                logger.error("Backup sync failed for proxmox/%s: %s", cfg.name, exc)
                results[f"proxmox/{cfg.name}"] = {"error": str(exc)}

    # UNAS sources
    uconfigs = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.type == "unas",
            IntegrationConfig.enabled == True,
        )
    )
    for cfg in uconfigs.scalars().all():
        snap = await snap_svc.get_latest(db, "unas", cfg.id)
        if snap and snap.data_json:
            data = _json.loads(snap.data_json)
            try:
                r = await sync_unas_backups(db, cfg.id, data)
                results[f"unas/{cfg.name}"] = r
            except Exception as exc:
                logger.error("Backup sync failed for unas/%s: %s", cfg.name, exc)
                results[f"unas/{cfg.name}"] = {"error": str(exc)}

    await db.commit()
    return results

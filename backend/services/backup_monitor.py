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
from services.host_state import iso

logger = logging.getLogger(__name__)


# ── Job state ───────────────────────────────────────────────────────────────


def overdue_after_hours(expected_frequency_hours: int | None) -> float:
    """Hours without a backup after which a job counts as overdue.

    The expected interval plus a quarter of it, at least two hours. A nightly
    job never runs exactly 24 h after the last one: it starts a little later,
    or takes longer. Judged against the bare interval every daily backup was
    "overdue" for the hours before its next run.
    """
    expected = max(1, int(expected_frequency_hours or 24))
    return expected + max(2.0, expected * 0.25)


def _hours(hours: float) -> str:
    if hours < 48:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} days"


def job_state(job: BackupJob, now: datetime) -> dict[str, Any]:
    """Status of a backup job and the reason for it, in words.

    ``effective_status`` is what the UI shows (ok, warning, failed, running,
    unknown); ``status_reason`` says why, so a "Warning" is never just a
    colour.
    """
    hours_since = None
    is_overdue = False
    if job.last_run_at:
        hours_since = round((now - job.last_run_at).total_seconds() / 3600, 1)
        is_overdue = hours_since > overdue_after_hours(job.expected_frequency_hours)

    status = job.last_status or "unknown"
    reason: str | None = None
    if status == "failed":
        reason = f"Last run failed: {job.last_error}" if job.last_error else "Last run failed"
    elif status == "warning":
        reason = "Last run finished with warnings"
        if job.last_error:
            reason += f": {job.last_error}"
    elif status == "running":
        reason = "A backup is running"
    elif status == "unknown" or not job.last_run_at:
        status = "unknown"
        reason = "No backup has been recorded"

    if is_overdue and status in ("ok", "warning"):
        overdue = (f"Last backup {_hours(hours_since)} ago, "
                   f"expected every {_hours(job.expected_frequency_hours or 24)}")
        reason = overdue if status == "ok" else f"{reason}. {overdue}"
        status = "warning"

    return {
        "effective_status": status,
        "status_reason": reason,
        "hours_since_last": hours_since,
        "is_overdue": is_overdue,
        # The name the frontend reads; is_overdue stays for API clients.
        "overdue": is_overdue,
    }


def job_entry(job: BackupJob, now: datetime) -> dict[str, Any]:
    """A backup job as the API returns it."""
    return {
        "id": job.id,
        "name": job.name,
        "source_type": job.source_type,
        "source_config_id": job.source_config_id,
        "target_name": job.target_name,
        "target_vmid": job.target_vmid,
        "storage_name": job.storage_name,
        "last_run_at": iso(job.last_run_at),
        "last_status": job.last_status,
        "last_duration_sec": job.last_duration_sec,
        "last_size_bytes": job.last_size_bytes,
        "last_error": job.last_error,
        "expected_frequency_hours": job.expected_frequency_hours,
        "enabled": job.enabled,
        **job_state(job, now),
    }


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
                "last_run_at": iso(job.last_run_at),
            })

        if job.last_run_at:
            hours_since = (now - job.last_run_at).total_seconds() / 3600
            if hours_since > overdue_after_hours(job.expected_frequency_hours):
                issues.append({
                    "job_id": job.id,
                    "job_name": job.name,
                    "target_name": job.target_name,
                    "source_type": job.source_type,
                    "issue": "overdue",
                    "hours_since": round(hours_since, 1),
                    "expected_hours": job.expected_frequency_hours,
                    "last_run_at": iso(job.last_run_at),
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

    The counts are returned under ``summary`` (what the UI reads) and, as
    before, at the top level. Disabled jobs are listed but not counted: a job
    somebody switched off is not a problem.
    """
    now = datetime.utcnow()
    result = await db.execute(
        select(BackupJob).order_by(BackupJob.source_type, BackupJob.target_name)
    )
    jobs = result.scalars().all()

    counts = {"total": 0, "healthy": 0, "warning": 0, "failed": 0, "unknown": 0}
    job_list: list[dict] = []
    for job in jobs:
        entry = job_entry(job, now)
        job_list.append(entry)
        if not job.enabled:
            continue
        counts["total"] += 1
        status = entry["effective_status"]
        if status == "ok":
            counts["healthy"] += 1
        elif status in ("warning", "running"):
            counts["warning"] += 1
        elif status == "failed":
            counts["failed"] += 1
        else:
            counts["unknown"] += 1

    return {
        **counts,
        "summary": counts,
        "overdue": [e for e in job_list if e["is_overdue"] and e["enabled"]],
        "jobs": job_list,
    }


async def get_compliance_groups(db: AsyncSession) -> dict[str, list[dict]]:
    """Enabled jobs that need attention, grouped the way the UI lists them."""
    summary = await get_backup_summary(db)
    jobs = [j for j in summary["jobs"] if j["enabled"]]
    failed = [j for j in jobs if j["effective_status"] == "failed"]
    never_run = [j for j in jobs if j["effective_status"] == "unknown"]
    overdue = [j for j in jobs if j["is_overdue"] and j["effective_status"] != "failed"]
    return {"failed": failed, "overdue": overdue, "never_run": never_run}


# From this many jobs of one source with the same problem on, they are one
# incident. A backup server that stopped, or a host that fell out of the
# schedule, makes dozens of jobs overdue at once: that is one thing to tell
# somebody about, not one notification per virtual machine.
GROUP_FROM = 3


def backup_conditions(groups: dict[str, list[dict]]) -> dict:
    """The incidents the compliance findings call for: dedup key -> condition.

    One incident per job while a source has only a few jobs in trouble, one
    per source and kind of problem (failed, overdue) from ``GROUP_FROM`` on.
    The grouped key names the source, not the jobs, so the incident stays the
    same one while jobs join or recover.
    """
    from services.condition_incidents import Condition

    wanted: dict[str, Condition] = {}
    for kind, severity, word in (("failed", "critical", "failed"),
                                 ("overdue", "warning", "overdue")):
        by_source: dict[tuple, list[dict]] = {}
        for job in groups.get(kind, []):
            by_source.setdefault((job.get("source_type"), job.get("source_config_id")), []).append(job)
        for (source_type, source_id), jobs in by_source.items():
            if len(jobs) < GROUP_FROM:
                for job in jobs:
                    wanted.setdefault(f"bk:{job['id']}", Condition(
                        f"Backup {word}: {job['target_name']}", severity,
                        f"Backup {job['name']} of {job['target_name']}: {job['status_reason']}"))
                continue
            names = sorted(str(j["target_name"]) for j in jobs)
            shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
            source = str(source_type or "backup source")
            summary = f"{len(jobs)} backups of {source} are {word}: {shown}."
            if kind == "overdue":
                ages = [j["hours_since_last"] for j in jobs if j.get("hours_since_last") is not None]
                if ages:
                    summary += f" The oldest last ran {_hours(max(ages))} ago."
                summary += " Check that the backup job itself still runs."
            else:
                reasons = sorted({str(j.get("last_error")) for j in jobs if j.get("last_error")})
                if reasons:
                    summary += f" Reported: {'; '.join(reasons[:2])[:200]}."
            wanted[f"bk-{kind}:{source}:{source_id}"[:64]] = Condition(
                f"{len(jobs)} backups {word} ({source})", severity, summary,
                absorbs=frozenset(f"bk:{j['id']}" for j in jobs))
    return wanted


# ── Job history ─────────────────────────────────────────────────────────────


async def get_job_detail(db: AsyncSession, job_id: int) -> dict | None:
    """Get a single backup job with recent history."""
    job = await db.get(BackupJob, job_id)
    if not job:
        return None

    return {
        **job_entry(job, datetime.utcnow()),
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
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
            "timestamp": iso(e.timestamp),
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

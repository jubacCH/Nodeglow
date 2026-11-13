"""Backup job status: what the Backups page shows and why."""
from datetime import datetime, timedelta

from models.backup import BackupJob
from services import backup_monitor as bk

NOW = datetime(2026, 11, 12, 22, 30)


def _job(**fields) -> BackupJob:
    defaults = dict(name="vzdump-101", source_type="proxmox", target_name="srv",
                    last_status="ok", expected_frequency_hours=24, enabled=True)
    return BackupJob(**{**defaults, **fields})


def test_a_nightly_backup_is_not_overdue_before_its_next_run():
    """23 h 30 min after last night's run a daily job is on schedule.

    Judged against the bare 24 h every daily job turned "Warning" shortly
    before the next run, and the whole page was amber with no reason given.
    """
    state = bk.job_state(_job(last_run_at=NOW - timedelta(hours=25)), NOW)
    assert state["effective_status"] == "ok"
    assert state["is_overdue"] is False
    assert state["status_reason"] is None


def test_an_overdue_backup_says_how_late_it_is():
    state = bk.job_state(_job(last_run_at=NOW - timedelta(hours=50)), NOW)
    assert state["effective_status"] == "warning"
    assert state["is_overdue"] is True and state["overdue"] is True
    assert state["status_reason"] == "Last backup 2 days ago, expected every 24 h"


def test_every_status_other_than_ok_has_a_reason():
    ran = NOW - timedelta(hours=2)
    failed = bk.job_state(_job(last_status="failed", last_error="exit code 255", last_run_at=ran), NOW)
    assert failed["effective_status"] == "failed"
    assert failed["status_reason"] == "Last run failed: exit code 255"

    warned = bk.job_state(_job(last_status="warning", last_run_at=ran), NOW)
    assert warned["status_reason"] == "Last run finished with warnings"

    never = bk.job_state(_job(last_status="unknown", last_run_at=None), NOW)
    assert never["effective_status"] == "unknown"
    assert never["status_reason"] == "No backup has been recorded"


def test_overdue_threshold_scales_with_the_interval():
    assert bk.overdue_after_hours(24) == 30
    assert bk.overdue_after_hours(1) == 3
    assert bk.overdue_after_hours(168) == 210
    assert bk.overdue_after_hours(None) == 30


async def test_summary_has_the_shape_the_page_reads(db):
    """Counts under ``summary``, ``overdue`` per job, UTC timestamps with Z.

    The page read ``summary.*`` and ``job.overdue``; the API sent flat counts
    and ``is_overdue``, so every tile showed a dash and no job said "overdue".
    """
    now = datetime.utcnow()
    db.add_all([
        _job(name="a", last_run_at=now - timedelta(hours=3)),
        _job(name="b", last_run_at=now - timedelta(hours=60)),
        _job(name="c", last_status="failed", last_error="boom", last_run_at=now - timedelta(hours=3)),
        _job(name="d", last_status="unknown"),
        _job(name="e", last_status="failed", enabled=False),
    ])
    await db.commit()

    data = await bk.get_backup_summary(db)
    assert data["summary"] == {"total": 4, "healthy": 1, "warning": 1, "failed": 1, "unknown": 1}
    by_name = {j["name"]: j for j in data["jobs"]}
    assert by_name["b"]["overdue"] is True
    assert by_name["b"]["status_reason"].startswith("Last backup")
    assert by_name["a"]["last_run_at"].endswith("Z")

    groups = await bk.get_compliance_groups(db)
    assert [j["name"] for j in groups["failed"]] == ["c"]
    assert [j["name"] for j in groups["overdue"]] == ["b"]
    assert [j["name"] for j in groups["never_run"]] == ["d"]

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


def _finding(i, *, source="proxmox", config=1, hours=80.0, error=None):
    return {"id": i, "name": f"vzdump-{i}", "target_name": f"vm-{i:02d}", "source_type": source,
            "source_config_id": config, "hours_since_last": hours, "last_error": error,
            "status_reason": "Last backup 3 days ago, expected every 24 h"}


def test_many_overdue_jobs_of_one_source_are_one_incident():
    """A host that fell out of the backup schedule is one alert, not 45.

    The first run on a real installation would have opened one incident and
    sent one notification per virtual machine.
    """
    groups = {"failed": [], "overdue": [_finding(i, hours=70 + i) for i in range(45)]}
    wanted = bk.backup_conditions(groups)
    assert list(wanted) == ["bk-overdue:proxmox:1"]
    cond = wanted["bk-overdue:proxmox:1"]
    assert cond.title == "45 backups overdue (proxmox)" and cond.severity == "warning"
    assert "vm-00, vm-01" in cond.summary and "and 39 more" in cond.summary
    assert "The oldest last ran 5 days ago" in cond.summary

    # The key does not depend on which jobs are in it: one more, same incident.
    groups["overdue"].append(_finding(99))
    assert list(bk.backup_conditions(groups)) == ["bk-overdue:proxmox:1"]


def test_a_few_jobs_keep_their_own_incident_and_sources_stay_apart():
    groups = {
        "overdue": [_finding(1), _finding(2), *[_finding(10 + i, source="pbs", config=7) for i in range(3)]],
        "failed": [_finding(3, error="exit code 255")],
    }
    wanted = bk.backup_conditions(groups)
    assert set(wanted) == {"bk:1", "bk:2", "bk:3", "bk-overdue:pbs:7"}
    assert wanted["bk:3"].severity == "critical" and wanted["bk:3"].title == "Backup failed: vm-03"
    assert wanted["bk:1"].title == "Backup overdue: vm-01"


def test_failed_jobs_group_too_and_name_the_error():
    groups = {"overdue": [], "failed": [_finding(i, error="storage offline") for i in range(4)]}
    (key, cond), = bk.backup_conditions(groups).items()
    assert key == "bk-failed:proxmox:1" and cond.severity == "critical"
    assert cond.title == "4 backups failed (proxmox)" and "storage offline" in cond.summary


# ── Proxmox sync: one job per guest and cluster ─────────────────────────────


def _cluster(*vmids, online=True, files=None, ctime=None):
    """A Proxmox snapshot with the given guests and one backup file each."""
    ctime = ctime or int(datetime.utcnow().timestamp()) - 3600
    files = vmids if files is None else files
    return {
        "nodes": [{"name": "pve1", "online": True}, {"name": "pve2", "online": online}],
        "vms": [{"id": v, "name": f"vm-{v}"} for v in vmids],
        "containers": [],
        "tasks": [],
        "backups": {"nfs": [{"vmid": v, "ctime": ctime, "size": 1, "volid": f"nfs:{v}"} for v in files]},
    }


async def _two_integrations(db, group="lab"):
    from models.integration import IntegrationConfig

    a = IntegrationConfig(type="proxmox", name="pve1", config_json="{}", cluster_group=group)
    b = IntegrationConfig(type="proxmox", name="pve2", config_json="{}", cluster_group=group)
    db.add_all([a, b])
    await db.flush()
    return a.id, b.id


async def _jobs(db):
    from sqlalchemy import select

    return (await db.execute(select(BackupJob).order_by(BackupJob.id))).scalars().all()


async def test_the_standby_of_a_cluster_has_no_backup_jobs_of_its_own(db):
    """Two integrations on one cluster: one collects, one stands by.

    On a real installation every guest had a job under each. The ones of the
    standby were three days old and never updated: 43 "overdue" incidents for
    backups that ran every night.
    """
    from sqlalchemy import select

    from models.backup import BackupHistory

    primary, standby = await _two_integrations(db)
    old = datetime.utcnow().replace(microsecond=0) - timedelta(days=3)
    stale = _job(name="vzdump-101-vm-101", source_config_id=standby, target_vmid=101, last_run_at=old)
    db.add(stale)
    await db.flush()
    db.add(BackupHistory(job_id=stale.id, timestamp=old, status="ok"))
    await db.flush()

    result = await bk.sync_proxmox_backups(db, primary, _cluster(101))
    await db.commit()
    (job,) = await _jobs(db)
    assert job.source_config_id == primary and result["created"] == 0
    assert bk.job_state(job, datetime.utcnow())["is_overdue"] is False
    stamps = (await db.execute(select(BackupHistory.timestamp).where(BackupHistory.job_id == job.id))).scalars().all()
    assert len(stamps) == 2 and old in stamps, "the history of the standby moves along"

    # Roles change: the same job, now under the other integration.
    await bk.sync_proxmox_backups(db, standby, _cluster(101))
    await db.commit()
    (job,) = await _jobs(db)
    assert job.source_config_id == standby


async def test_duplicate_jobs_of_a_cluster_are_folded_into_the_newest(db):
    primary, standby = await _two_integrations(db)
    now = datetime.utcnow()
    db.add_all([
        _job(name="a", source_config_id=primary, target_vmid=101, last_run_at=now - timedelta(hours=2)),
        _job(name="b", source_config_id=standby, target_vmid=101, last_run_at=now - timedelta(days=3)),
    ])
    await db.flush()
    result = await bk.sync_proxmox_backups(db, primary, _cluster(101))
    await db.commit()
    assert result["merged"] == 1
    assert [j.name for j in await _jobs(db)] == ["a"]


async def test_integrations_of_different_clusters_keep_their_jobs_apart(db):
    from models.integration import IntegrationConfig

    a = IntegrationConfig(type="proxmox", name="site-a", config_json="{}", cluster_group="a")
    b = IntegrationConfig(type="proxmox", name="site-b", config_json="{}", cluster_group="b")
    c = IntegrationConfig(type="proxmox", name="single", config_json="{}")
    db.add_all([a, b, c])
    await db.flush()
    for cfg in (a, b, c):
        await bk.sync_proxmox_backups(db, cfg.id, _cluster(101))
    await db.commit()
    assert sorted(j.source_config_id for j in await _jobs(db)) == sorted([a.id, b.id, c.id])


async def test_a_deleted_guest_has_no_backup_job(db):
    """Its files stay on the storage and only get older. The job was overdue
    for ever."""
    primary, _ = await _two_integrations(db)
    db.add(_job(name="vzdump-114-guest-114", source_config_id=primary, target_vmid=114,
                last_run_at=datetime.utcnow() - timedelta(days=11)))
    await db.flush()

    # A node is offline: a guest that is missing from the list proves nothing.
    await bk.sync_proxmox_backups(db, primary, _cluster(101, files=(101, 114), online=False))
    assert len(await _jobs(db)) == 2

    result = await bk.sync_proxmox_backups(db, primary, _cluster(101, files=(101, 114)))
    await db.commit()
    assert result["removed"] == 1
    assert [j.target_vmid for j in await _jobs(db)] == [101]


async def test_an_older_file_does_not_hide_a_newer_run(db):
    primary, _ = await _two_integrations(db)
    newer = datetime.utcnow().replace(microsecond=0) - timedelta(hours=1)
    db.add(_job(source_config_id=primary, target_vmid=101, last_run_at=newer))
    await db.flush()
    await bk.sync_proxmox_backups(
        db, primary, _cluster(101, ctime=int((newer - timedelta(days=2)).timestamp())))
    (job,) = await _jobs(db)
    assert job.last_run_at == newer


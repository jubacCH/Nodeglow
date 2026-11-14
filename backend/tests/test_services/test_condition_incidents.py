"""Incidents for certificate expiry and backup compliance."""
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models.incident import Incident
from services import condition_incidents as cond


async def _incidents(db, rule):
    return (await db.execute(select(Incident).where(Incident.rule == rule))).scalars().all()


async def test_sync_opens_escalates_and_resolves(db):
    """One incident per key: opened once, re-notified when it gets worse,
    resolved when the key is gone."""
    warn = {"ssl:1": cond.Condition("Certificate expires in 10 day(s): web", "warning", "ten days")}
    crit = {"ssl:1": cond.Condition("Certificate expires in 2 day(s): web", "critical", "two days")}

    notes = await cond.sync_rule(db, "ssl_expiry", warn, event_type="e", resolved_summary="ok again")
    await db.commit()
    assert [n.title for n in notes] == ["🔴 Incident: Certificate expires in 10 day(s): web"]

    assert await cond.sync_rule(db, "ssl_expiry", warn, event_type="e", resolved_summary="ok again") == []
    await db.commit()

    notes = await cond.sync_rule(db, "ssl_expiry", crit, event_type="e", resolved_summary="ok again")
    await db.commit()
    assert [n.severity for n in notes] == ["critical"]
    (incident,) = await _incidents(db, "ssl_expiry")
    assert incident.severity == "critical" and incident.title.startswith("Certificate expires in 2")

    notes = await cond.sync_rule(db, "ssl_expiry", {}, event_type="e", resolved_summary="ok again")
    await db.commit()
    assert [n.title for n in notes] == ["✅ Resolved: Certificate expires in 2 day(s): web"]
    (incident,) = await _incidents(db, "ssl_expiry")
    assert incident.status == "resolved"


def test_certificate_thresholds():
    from scheduler import certificate_condition

    assert certificate_condition("web", "web.example.com", 30) is None
    assert certificate_condition("web", "web.example.com", None) is None
    assert certificate_condition("web", "web.example.com", 14).severity == "warning"
    assert certificate_condition("web", "web.example.com", 3).severity == "critical"
    assert certificate_condition("web", "web.example.com", 0).title == "Certificate expires today: web"
    expired = certificate_condition("web", "web.example.com", -2)
    assert expired.severity == "critical" and "expired 2 day(s) ago" in expired.summary


async def test_auto_resolve_leaves_these_incidents_to_their_job(db):
    from services import correlation as corr

    await cond.sync_rule(db, "backup", {"bk:1": cond.Condition("Backup overdue: srv", "warning", "late")},
                         event_type="backup_issue", resolved_summary="ok")
    await db.commit()
    with patch("notifications.notify", new=AsyncMock()):
        await corr._auto_resolve(db, offline_hosts=[])
        await db.commit()
    (incident,) = await _incidents(db, "backup")
    assert incident.status == "open"


async def test_incidents_folded_into_a_group_close_without_a_notification(db):
    """Three overdue jobs become one incident: the two that were open are not
    reported as resolved, because nothing was resolved."""
    from services import backup_monitor as bk

    def job(i):
        return {"id": i, "name": f"vzdump-{i}", "target_name": f"vm-{i}", "source_type": "proxmox",
                "source_config_id": 1, "hours_since_last": 80.0, "last_error": None,
                "status_reason": "Last backup 3 days ago"}

    async def sync(jobs):
        notes = await cond.sync_rule(db, "backup", bk.backup_conditions({"failed": [], "overdue": jobs}),
                                     event_type="backup_issue", resolved_summary="ran again")
        await db.commit()
        return [n.title for n in notes]

    assert len(await sync([job(1), job(2)])) == 2
    assert await sync([job(1), job(2), job(3)]) == ["🔴 Incident: 3 backups overdue (proxmox)"]
    incidents = await _incidents(db, "backup")
    assert sorted(i.status for i in incidents) == ["open", "resolved", "resolved"]

    # The group itself does announce its end.
    assert await sync([]) == ["✅ Resolved: 3 backups overdue (proxmox)"]


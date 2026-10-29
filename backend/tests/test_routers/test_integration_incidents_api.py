"""The integration endpoints name the open incidents of each integration."""
from datetime import datetime

from services.tenancy import bind_session


async def test_detail_lists_and_list_counts_open_incidents(client):
    from database import AsyncSessionLocal
    from models.incident import Incident
    from services import integration as int_svc
    from services import integration_alerts as ia

    async with AsyncSessionLocal() as s:
        bind_session(s, 1)
        cfg = await int_svc.create_config(s, "unas", "nas", {"host": "https://192.0.2.10"})
        other = await int_svc.create_config(s, "unas", "other", {"host": "https://192.0.2.11"})
        s.add_all([
            Incident(rule=ia.ALERT_RULE, title="nas: Disk error – Slot 3", severity="critical",
                     status="open", host_ids_hash=f"{ia.alert_prefix(cfg)}abc", created_at=datetime(2026, 10, 29, 12)),
            Incident(rule=ia.FAILED_RULE, title="other: integration cannot collect", severity="warning",
                     status="open", host_ids_hash=ia.failed_key(other)),
            Incident(rule=ia.ALERT_RULE, title="nas: old", severity="warning",
                     status="resolved", host_ids_hash=f"{ia.alert_prefix(cfg)}def"),
        ])
        await s.commit()
        cfg_id, other_id = cfg.id, other.id

    detail = (await client.get(f"/api/v1/integrations/{cfg_id}")).json()
    assert [(i["title"], i["severity"], i["created_at"]) for i in detail["open_incidents"]] == [
        ("nas: Disk error – Slot 3", "critical", "2026-10-29T12:00:00Z")]
    counts = {i["id"]: i["open_incidents"] for i in (await client.get("/api/v1/integrations")).json()}
    assert (counts[cfg_id], counts[other_id]) == (1, 1)

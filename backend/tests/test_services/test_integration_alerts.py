"""Integration incidents: parse_alerts() findings and collection failures.

Before this, parse_alerts() was never called: a failed disk or an expiring
certificate showed on the detail page and nowhere else, and a broken
integration raised nothing unless its host was down as well.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from integrations._base import Alert, BaseIntegration, CollectorResult
from models.incident import Incident
from models.integration import Snapshot
from services import integration as int_svc
from services import integration_alerts as ia
from services.integration_ingest import CollectStamp, Outcome, ingest


class _Alerting(BaseIntegration):
    name = "alertint"
    display_name = "Alerting fake"
    description = "test fake"
    config_fields = []

    async def collect(self) -> CollectorResult:  # pragma: no cover - not collected here
        return CollectorResult(success=True, data={})

    def parse_alerts(self, data: dict) -> list[Alert]:
        return [Alert(severity=a.get("severity", "critical"), title=a["title"],
                      detail=a.get("detail", ""), entity=a.get("entity", ""), host=a.get("host", ""))
                for a in data.get("alerts", [])]


@pytest.fixture(autouse=True)
def _quiet():
    ia._streaks.clear()
    with patch("notifications.notify", new=AsyncMock()) as notify:
        yield notify
    ia._streaks.clear()


async def _cfg(db, name="nas", cluster_group=None):
    return await int_svc.create_config(db, "alertint", name, {"host": "https://192.0.2.10"},
                                       cluster_group=cluster_group)


CONFIG = {"host": "https://nas.example.com:8443"}


async def _ok(db, cfg, *alerts):
    out = await ingest(db, integration_type="alertint", cfg_id=cfg.id, stamp=CollectStamp.of(cfg),
                       result=CollectorResult(success=True, data={"alerts": list(alerts)}),
                       instance=_Alerting(config=CONFIG), config=CONFIG)
    await db.commit()
    await ia.send_notifications(ia.take_notifications(db))
    return out


async def _fail(db, cfg, error="connection refused"):
    out = await ingest(db, integration_type="alertint", cfg_id=cfg.id, stamp=CollectStamp.of(cfg),
                       result=CollectorResult(success=False, error=error),
                       instance=_Alerting(config=CONFIG), config=CONFIG)
    await db.commit()
    await ia.send_notifications(ia.take_notifications(db))
    return out


async def _host(db, name, hostname, ip=None, maintenance=False):
    from models.ping import PingHost

    host = PingHost(name=name, hostname=hostname, ip_address=ip, maintenance=maintenance)
    db.add(host)
    await db.commit()
    return host


async def _incidents(db, rule=None):
    q = select(Incident).order_by(Incident.id)
    if rule:
        q = q.where(Incident.rule == rule)
    return list((await db.execute(q)).scalars())


DISK = {"title": "Disk error", "entity": "Slot 3", "detail": "Slot 3: failed"}


async def test_an_alert_opens_an_incident_on_the_second_snapshot(db, _quiet):
    cfg = await _cfg(db)
    assert await _ok(db, cfg, DISK) == Outcome.STORED
    assert await _incidents(db) == []

    await _ok(db, cfg, DISK)
    [inc] = await _incidents(db)
    assert (inc.rule, inc.status, inc.severity) == (ia.ALERT_RULE, "open", "critical")
    assert inc.title == "nas: Disk error – Slot 3"
    assert _quiet.await_count == 1


async def test_a_persisting_alert_stays_one_incident_and_resolves_when_gone(db, _quiet):
    cfg = await _cfg(db)
    for _ in range(4):
        await _ok(db, cfg, DISK)
    assert len(await _incidents(db)) == 1

    await _ok(db, cfg)
    [inc] = await _incidents(db)
    assert inc.status == "resolved" and inc.resolved_at is not None
    assert _quiet.await_count == 2  # opened, resolved


async def test_a_one_off_spike_never_opens_an_incident(db):
    cfg = await _cfg(db)
    await _ok(db, cfg, DISK)
    await _ok(db, cfg)
    await _ok(db, cfg, DISK)
    assert await _incidents(db) == []


async def test_info_alerts_are_not_incidents(db):
    cfg = await _cfg(db)
    for _ in range(3):
        await _ok(db, cfg, {"title": "Update available", "severity": "info"})
    assert await _incidents(db) == []


async def test_alerts_of_different_entities_are_separate_incidents(db):
    cfg = await _cfg(db)
    other = dict(DISK, entity="Slot 4")
    for _ in range(2):
        await _ok(db, cfg, DISK, other)
    await _ok(db, cfg, other)
    by_title = {i.title: i.status for i in await _incidents(db)}
    assert by_title == {"nas: Disk error – Slot 3": "resolved", "nas: Disk error – Slot 4": "open"}


async def test_a_cluster_failover_continues_the_open_incident(db):
    first = await _cfg(db, "pve-a", cluster_group="c1")
    second = await _cfg(db, "pve-b", cluster_group="c1")
    for _ in range(2):
        await _ok(db, first, DISK)
    await _ok(db, second, DISK)  # the other member is primary now
    [inc] = await _incidents(db)
    assert inc.status == "open"


async def test_failures_open_an_incident_after_two_in_a_row_and_resolve_on_success(db, _quiet):
    cfg = await _cfg(db)
    await _fail(db, cfg)
    assert await _incidents(db) == []
    await _fail(db, cfg, "timeout")
    [inc] = await _incidents(db, ia.FAILED_RULE)
    assert inc.status == "open" and inc.title == "nas: integration cannot collect"

    await _ok(db, cfg)
    [inc] = await _incidents(db, ia.FAILED_RULE)
    assert inc.status == "resolved"


async def test_a_recent_success_holds_the_failure_incident_back(db):
    """Failures right after a success are a blip, not an outage."""
    cfg = await _cfg(db)
    await _ok(db, cfg)
    await _fail(db, cfg)
    await _fail(db, cfg)
    await _fail(db, cfg)
    assert await _incidents(db) == []


async def test_failures_alarm_once_the_last_success_is_old_enough(db):
    from datetime import datetime, timedelta

    cfg = await _cfg(db)
    await _ok(db, cfg)
    snap = (await db.execute(select(Snapshot).where(Snapshot.entity_id == cfg.id))).scalar_one()
    snap.timestamp = datetime.utcnow() - timedelta(minutes=ia.FAILED_MIN_MINUTES + 1)
    await db.commit()
    await _fail(db, cfg)
    await _fail(db, cfg)
    assert len(await _incidents(db, ia.FAILED_RULE)) == 1


async def test_incidents_of_a_disabled_or_deleted_config_are_resolved(db):
    cfg = await _cfg(db)
    gone = await _cfg(db, "old")
    for _ in range(2):
        await _fail(db, cfg)
        await _fail(db, gone)
    assert len(await _incidents(db)) == 2

    cfg.enabled = False
    await int_svc.delete_config(db, gone.id)
    await db.commit()
    assert await ia.resolve_orphans(db) == 2
    await db.commit()
    assert {i.status for i in await _incidents(db)} == {"resolved"}


async def test_live_configs_keep_their_incidents(db):
    cfg = await _cfg(db)
    for _ in range(2):
        await _ok(db, cfg, DISK)
    for _ in range(2):
        await _fail(db, cfg)
    assert await ia.resolve_orphans(db) == 0


async def test_correlation_auto_resolve_leaves_integration_incidents_alone(db):
    from services.correlation import _auto_resolve

    cfg = await _cfg(db)
    for _ in range(2):
        await _ok(db, cfg, DISK)
    await _auto_resolve(db, offline_hosts=[])
    [inc] = await _incidents(db)
    assert inc.status == "open"


async def test_a_broken_parse_alerts_does_not_cost_the_snapshot(db, caplog):
    class _Broken(_Alerting):
        def parse_alerts(self, data):
            raise RuntimeError("boom")

    cfg = await _cfg(db)
    out = await ingest(db, integration_type="alertint", cfg_id=cfg.id, stamp=CollectStamp.of(cfg),
                       result=CollectorResult(success=True, data={"x": 1}), instance=_Broken(config={}))
    await db.commit()
    assert out == Outcome.STORED
    assert "Integration alerting failed" in caplog.text


# -- Hosts, maintenance, notifications ----------------------------------------

async def test_incidents_name_the_integration_host(db):
    nas = await _host(db, "NAS", "nas.example.com")
    cfg = await _cfg(db)
    for _ in range(2):
        await _ok(db, cfg, DISK)
    [inc] = await _incidents(db)
    assert inc.host_ids == f"[{nas.id}]"


async def test_an_alert_names_the_device_it_is_about(db):
    await _host(db, "NAS", "nas.example.com")
    node = await _host(db, "pve2", "pve2.example.com", ip="192.0.2.22")
    cfg = await _cfg(db)
    for _ in range(2):
        await _ok(db, cfg, {"title": "Node offline", "entity": "node: pve2", "host": "pve2"})
    [inc] = await _incidents(db)
    assert inc.host_ids == f"[{node.id}]"


async def test_maintenance_on_the_integration_host_opens_nothing(db):
    await _host(db, "NAS", "nas.example.com", maintenance=True)
    cfg = await _cfg(db)
    for _ in range(3):
        await _ok(db, cfg, DISK)
    for _ in range(2):
        await _fail(db, cfg)
    assert await _incidents(db) == []


async def test_maintenance_on_the_device_opens_nothing(db):
    await _host(db, "pve2", "pve2.example.com", maintenance=True)
    cfg = await _cfg(db)
    for _ in range(3):
        await _ok(db, cfg, {"title": "Node offline", "entity": "node: pve2", "host": "pve2.example.com"})
    assert await _incidents(db) == []


async def test_an_open_incident_still_resolves_during_maintenance(db):
    nas = await _host(db, "NAS", "nas.example.com")
    cfg = await _cfg(db)
    for _ in range(2):
        await _ok(db, cfg, DISK)
    nas.maintenance = True
    await db.commit()
    await _ok(db, cfg)
    [inc] = await _incidents(db)
    assert inc.status == "resolved"


async def test_nothing_is_announced_before_the_commit(db, _quiet):
    cfg = await _cfg(db)
    for _ in range(2):
        await ingest(db, integration_type="alertint", cfg_id=cfg.id, stamp=CollectStamp.of(cfg),
                     result=CollectorResult(success=True, data={"alerts": [DISK]}),
                     instance=_Alerting(config=CONFIG), config=CONFIG)
    assert _quiet.await_count == 0
    await db.commit()
    await ia.send_notifications(ia.take_notifications(db))
    assert _quiet.await_count == 1


def test_endpoint_and_host_matching():
    from types import SimpleNamespace as H

    assert ia.endpoint_of({"host": "https://NAS.example.com:8443/api"}) == "nas.example.com"
    assert ia.endpoint_of({"url": "192.0.2.5"}) == "192.0.2.5"
    assert ia.endpoint_of({"token": "x"}) == ""
    idx = ia.HostIndex([H(id=1, name="NAS", hostname="nas.example.com", ip_address="192.0.2.5"),
                        H(id=2, name="first", hostname="a.one.example", ip_address=None),
                        H(id=3, name="second", hostname="a.two.example", ip_address=None)])
    assert idx.find("https://192.0.2.5").id == 1
    assert idx.find("nas").id == 1
    assert idx.find("a") is None  # ambiguous short name
    assert idx.find("") is None



def test_a_collection_error_reads_as_something_to_act_on():
    """No web-reference link, and one sentence on what the error usually means."""
    from services.integration_alerts import explain_failure

    raw = ("Client error '400 Bad Request' for url 'http://10.10.70.10:81/api/tokens' "
           "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400")
    text = explain_failure(raw)
    assert "mozilla" not in text
    assert "http://10.10.70.10:81/api/tokens" in text
    assert text.endswith("Check the user name, password or token in the integration's settings.")

    assert "reachable from Nodeglow" in explain_failure("collect timed out after 30s")
    assert "No connection" in explain_failure("All connection attempts failed")
    assert explain_failure(None) == "unknown error"
    assert explain_failure("something odd") == "something odd"

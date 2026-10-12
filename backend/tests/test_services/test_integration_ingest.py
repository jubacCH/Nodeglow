"""Probe-side integrations P1: the collector split and the shared ingest path.

* every config is collected by exactly one party (``collector``); the core
  collects only ``collector = 'core'`` — never a probe's config, also not one
  whose probe was deleted ("needs a probe");
* ``config_rev`` is bumped on every change, and a result is stored only if
  the rev and collector it was started with are still current (rule 6) —
  for core results (move / edit during a collect) and probe results alike;
* core snapshots look exactly as before (no ``collected_by``/``collected_at``);
* ``network_scope``; the phpIPAM fetch/apply split; UniFi's reverse DNS in
  collect(); Proxmox's backup sync by config id without decrypting.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from integrations._base import NETWORK_SCOPES, BaseIntegration, CollectorResult
from models.agent import Agent
from models.integration import IntegrationConfig, Snapshot
from services import integration as int_svc
from services import integration_ingest as ingest_mod
from services.integration_ingest import CollectStamp, Outcome


class _Fake(BaseIntegration):
    name = "fakeint"
    display_name = "Fake"
    description = "test fake"
    config_fields = []

    calls: list[dict] = []
    during_collect = None      # async callable run inside collect()
    hooked: list[tuple[int | None, dict]] = []

    async def collect(self) -> CollectorResult:
        type(self).calls.append(dict(self.config))
        if type(self).during_collect is not None:
            await type(self).during_collect()
        return CollectorResult(success=True, data={"seen": self.config.get("host"), "n": 1})

    async def on_snapshot(self, data, config, db) -> None:
        type(self).hooked.append((self.config_id, dict(config)))


@pytest.fixture
def fake(monkeypatch):
    from integrations import _registry, get_registry

    get_registry()
    _Fake.calls, _Fake.during_collect, _Fake.hooked = [], None, []
    monkeypatch.setitem(_registry, "fakeint", _Fake)
    yield _Fake
    _registry.pop("fakeint", None)


@pytest.fixture
def scheduler_db(db):
    """run_integration_checks against the test session."""
    @asynccontextmanager
    async def _ctx():
        yield db

    with patch("scheduler.AsyncSessionLocal", side_effect=_ctx):
        yield db


async def _probe(db, name="probe-1") -> Agent:
    agent = Agent(name=name, hostname=name, token=f"tok-{name}", is_probe=True)
    db.add(agent)
    await db.flush()
    return agent


async def _cfg(db, name="cfg", host="https://192.0.2.10") -> IntegrationConfig:
    return await int_svc.create_config(db, "fakeint", name, {"host": host, "secret": "s3cret"})


async def _snaps(db, cfg_id: int) -> list[Snapshot]:
    return list((await db.execute(
        select(Snapshot).where(Snapshot.entity_type == "fakeint", Snapshot.entity_id == cfg_id)
        .order_by(Snapshot.id))).scalars())


async def _run_checks():
    from scheduler import run_integration_checks

    await run_integration_checks()


# ── Defaults and revisions ───────────────────────────────────────────────────

async def test_new_and_existing_configs_are_core_collected(db):
    cfg = await _cfg(db)
    assert (cfg.collector, cfg.probe_id, cfg.config_rev) == ("core", None, 1)


async def test_every_update_bumps_the_revision(db):
    cfg = await _cfg(db)
    await int_svc.update_config(db, cfg.id, name="renamed")
    await int_svc.update_config(db, cfg.id, config_dict={"host": "https://192.0.2.11"})
    assert (await db.get(IntegrationConfig, cfg.id)).config_rev == 3


async def test_assign_collector_moves_and_bumps(db):
    cfg = await _cfg(db)
    probe = await _probe(db)
    await int_svc.assign_collector(db, cfg, "probe", probe.id)
    assert (cfg.collector, cfg.probe_id, cfg.config_rev) == ("probe", probe.id, 2)
    await int_svc.assign_collector(db, cfg, "core")
    assert (cfg.collector, cfg.probe_id, cfg.config_rev) == ("core", None, 3)


async def test_assign_collector_refuses_what_is_not_a_probe(db):
    cfg = await _cfg(db)
    plain = Agent(name="agent", hostname="agent", token="tok-agent", is_probe=False)
    db.add(plain)
    await db.flush()
    with pytest.raises(ValueError):
        await int_svc.assign_collector(db, cfg, "probe", plain.id)
    with pytest.raises(ValueError):
        await int_svc.assign_collector(db, cfg, "probe", 999_999)
    with pytest.raises(ValueError):
        await int_svc.assign_collector(db, cfg, "probe", None)
    with pytest.raises(ValueError):
        await int_svc.assign_collector(db, cfg, "somewhere")
    assert (cfg.collector, cfg.config_rev) == ("core", 1)


async def test_the_schema_refuses_a_probe_id_on_a_core_config(db):
    probe = await _probe(db)
    db.add(IntegrationConfig(type="fakeint", name="bad", config_json="x",
                             collector="core", probe_id=probe.id))
    with pytest.raises(Exception, match="CHECK"):
        await db.flush()
    await db.rollback()
    db.add(IntegrationConfig(type="fakeint", name="bad", config_json="x", collector="edge"))
    with pytest.raises(Exception, match="CHECK"):
        await db.flush()
    await db.rollback()


# ── The core collects only its own configs (rule 1) ──────────────────────────

async def test_core_collects_only_core_configs(scheduler_db, fake):
    db = scheduler_db
    core = await _cfg(db, "core")
    on_probe = await _cfg(db, "on-probe", host="https://192.0.2.20")
    orphan = await _cfg(db, "needs-a-probe", host="https://192.0.2.30")
    probe = await _probe(db)
    await int_svc.assign_collector(db, on_probe, "probe", probe.id)
    await int_svc.assign_collector(db, orphan, "probe", probe.id)
    # The probe was deleted: SET NULL (probe_id) on PostgreSQL; the
    # collector stays 'probe' — "needs a probe", the core does not take over.
    orphan.probe_id = None
    await db.commit()

    await _run_checks()

    assert [c["host"] for c in fake.calls] == ["https://192.0.2.10"]
    assert len(await _snaps(db, core.id)) == 1
    assert await _snaps(db, on_probe.id) == []
    assert await _snaps(db, orphan.id) == []


async def test_core_snapshot_is_written_as_before(scheduler_db, fake):
    db = scheduler_db
    cfg = await _cfg(db)
    before = datetime.utcnow()

    await _run_checks()

    [snap] = await _snaps(db, cfg.id)
    assert snap.ok and snap.error is None
    assert json.loads(snap.data_json) == {"seen": "https://192.0.2.10", "n": 1}
    assert snap.collected_by is None and snap.collected_at is None
    assert before - timedelta(seconds=1) <= snap.timestamp <= datetime.utcnow()
    # The collector got the decrypted config, the hook its config id.
    assert fake.calls == [{"host": "https://192.0.2.10", "secret": "s3cret"}]
    assert fake.hooked == [(cfg.id, {"host": "https://192.0.2.10", "secret": "s3cret"})]


# ── Rule 6: a result must match the config it was started with ───────────────

async def test_move_to_probe_during_a_core_collect_writes_nothing(scheduler_db, fake):
    db = scheduler_db
    cfg = await _cfg(db)
    probe = await _probe(db)
    await db.commit()

    async def move():
        row = await db.get(IntegrationConfig, cfg.id)
        await int_svc.assign_collector(db, row, "probe", probe.id)
        await db.commit()

    fake.during_collect = move
    await _run_checks()

    assert len(fake.calls) == 1
    assert await _snaps(db, cfg.id) == []
    assert fake.hooked == []


async def test_edit_during_a_core_collect_writes_nothing(scheduler_db, fake):
    db = scheduler_db
    cfg = await _cfg(db)

    async def edit():
        await int_svc.update_config(db, cfg.id, config_dict={"host": "https://198.51.100.5"})

    fake.during_collect = edit
    await _run_checks()
    assert await _snaps(db, cfg.id) == []

    # The next pass collects the new config.
    fake.during_collect = None
    await _run_checks()
    [snap] = await _snaps(db, cfg.id)
    assert json.loads(snap.data_json)["seen"] == "https://198.51.100.5"


async def _ingest(db, cfg_id, stamp, **kw):
    async with ingest_mod.snapshot_write_lock():
        out = await ingest_mod.ingest(
            db, integration_type="fakeint", cfg_id=cfg_id, stamp=stamp,
            result=kw.pop("result", CollectorResult(success=True, data={"n": 2})),
            integration_cls=_Fake, config={}, **kw)
        await db.commit()
    return out


async def test_a_probe_result_is_stored_with_its_collection_time(db, fake):
    cfg = await _cfg(db)
    probe = await _probe(db)
    await int_svc.assign_collector(db, cfg, "probe", probe.id)
    await db.commit()
    at = datetime.utcnow() - timedelta(seconds=40)

    out = await _ingest(db, cfg.id, CollectStamp(2, "probe", probe.id), collected_at=at)

    assert out is Outcome.STORED
    [snap] = await _snaps(db, cfg.id)
    assert (snap.collected_by, snap.collected_at, snap.timestamp) == (probe.id, at, at)
    assert fake.hooked == [(cfg.id, {})]


async def test_a_probe_result_after_move_to_server_is_rejected(db, fake):
    cfg = await _cfg(db)
    probe = await _probe(db)
    await int_svc.assign_collector(db, cfg, "probe", probe.id)      # rev 2
    await int_svc.assign_collector(db, cfg, "core")                 # rev 3
    await db.commit()

    assert await _ingest(db, cfg.id, CollectStamp(2, "probe", probe.id)) \
        is Outcome.STALE_CONFIG_REV
    # Even a probe claiming the current rev is not the collector.
    assert await _ingest(db, cfg.id, CollectStamp(3, "probe", probe.id)) is Outcome.NOT_ASSIGNED
    assert await _snaps(db, cfg.id) == []


async def test_older_and_never_issued_revisions_are_rejected(db, fake):
    cfg = await _cfg(db)
    await int_svc.update_config(db, cfg.id, name="x")              # rev 2
    assert await _ingest(db, cfg.id, CollectStamp(1)) is Outcome.STALE_CONFIG_REV
    assert await _ingest(db, cfg.id, CollectStamp(3)) is Outcome.STALE_CONFIG_REV
    assert await _ingest(db, cfg.id, CollectStamp(2)) is Outcome.STORED
    assert len(await _snaps(db, cfg.id)) == 1


async def test_another_probe_and_a_needs_a_probe_config_are_rejected(db, fake):
    cfg = await _cfg(db)
    probe, other = await _probe(db), await _probe(db, "probe-2")
    await int_svc.assign_collector(db, cfg, "probe", probe.id)
    await db.commit()
    assert await _ingest(db, cfg.id, CollectStamp(2, "probe", other.id)) is Outcome.NOT_ASSIGNED
    assert await _ingest(db, cfg.id, CollectStamp(2)) is Outcome.NOT_ASSIGNED
    cfg.probe_id = None  # probe deleted
    await db.commit()
    assert await _ingest(db, cfg.id, CollectStamp(2, "probe", None)) is Outcome.NOT_ASSIGNED
    assert await _snaps(db, cfg.id) == []


async def test_a_deleted_config_gets_nothing(db, fake):
    cfg = await _cfg(db)
    await int_svc.delete_config(db, cfg.id)
    assert await _ingest(db, cfg.id, CollectStamp(1)) is Outcome.DELETED


async def test_failed_results_are_stored_as_failed(db, fake):
    cfg = await _cfg(db)
    out = await _ingest(db, cfg.id, CollectStamp(1),
                        result=CollectorResult(success=False, error="401 Unauthorized"))
    assert out is Outcome.FAILED
    [snap] = await _snaps(db, cfg.id)
    assert (snap.ok, snap.error) == (False, "401 Unauthorized")
    assert fake.hooked == []


async def test_cluster_group_standby_through_ingest(db, fake):
    a = await _cfg(db, "a")
    b = await _cfg(db, "b", host="https://192.0.2.11")
    a.cluster_group = b.cluster_group = "grp"
    await db.commit()
    owners: set = set()
    assert await _ingest(db, a.id, CollectStamp(1), cluster_owners=owners) is Outcome.STORED
    assert await _ingest(db, b.id, CollectStamp(1), cluster_owners=owners) is Outcome.STANDBY
    [snap_b] = await _snaps(db, b.id)
    assert (snap_b.ok, snap_b.error, snap_b.data_json) == (True, ingest_mod.STANDBY_MARKER, None)


# ── network_scope ─────────────────────────────────────────────────────────────

def test_every_integration_declares_a_known_network_scope():
    from integrations import get_registry

    scopes = {name: cls.network_scope for name, cls in get_registry().items()}
    assert set(scopes.values()) <= set(NETWORK_SCOPES), scopes
    assert scopes["speedtest"] == "site"
    assert scopes["cloudflare"] == "cloud"
    assert scopes["proxmox"] == scopes["unifi"] == "any"


# ── phpIPAM: fetch (collect) + apply (backend) ────────────────────────────────

_PHPIPAM_ADDRESSES = [
    {"ip": "192.0.2.1", "hostname": "gw.example.com", "subnetId": "1", "active": "1"},
    {"ip": "192.0.2.2", "hostname": "", "description": "printer", "subnetId": "1"},
    {"ip": "192.0.2.3", "hostname": "old.example.com", "subnetId": "1", "active": "0"},
    {"ip": "", "hostname": "no-ip", "subnetId": "2"},
    {"ip": "192.0.2.4", "subnetId": "2"},
]


async def _phpipam_collect():
    from integrations.phpipam import PhpIpamIntegration, PhpIpamClient

    with patch.object(PhpIpamClient, "authenticate", AsyncMock()), \
            patch.object(PhpIpamClient, "get_addresses",
                         AsyncMock(return_value=_PHPIPAM_ADDRESSES)):
        return await PhpIpamIntegration(config={
            "host": "https://ipam.example.com", "app_id": "ng", "app_secret": "tok",
        }).collect()


async def test_phpipam_apply_from_collected_data(db):
    from integrations.phpipam import apply_phpipam_hosts
    from models.ping import PingHost

    result = await _phpipam_collect()
    assert result.success and result.data["source_url"] == "https://ipam.example.com"
    db.add(PingHost(name="manual-gw", hostname="192.0.2.1", source="manual"))
    await db.commit()

    out = await apply_phpipam_hosts(db, result.data, result.data["source_url"])

    # As the old single-pass sync: one inactive and one address without an IP
    # skipped, the manual host merged (and renamed), two new hosts.
    assert out == {"added": 2, "merged": 1, "skipped": 2, "errors": []}
    hosts = {h.hostname: (h.name, h.source, h.source_detail)
             for h in (await db.execute(select(PingHost))).scalars()}
    assert hosts == {
        "192.0.2.1": ("gw.example.com", "phpipam", None),
        "192.0.2.2": ("printer", "phpipam", "https://ipam.example.com"),
        "192.0.2.4": ("192.0.2.4", "phpipam", "https://ipam.example.com"),
    }


async def test_phpipam_sync_of_a_probe_config_uses_the_snapshot_not_the_secrets(db):
    from routers.settings.general import sync_phpipam_config
    from models.ping import PingHost
    from services import snapshot as snap_svc

    cfg = await int_svc.create_config(db, "phpipam", "ipam", {
        "host": "https://ipam.example.com", "app_id": "ng", "app_secret": "tok"})
    await int_svc.assign_collector(db, cfg, "probe", (await _probe(db)).id)
    await db.commit()

    with patch.object(int_svc, "decrypt_config", AsyncMock(side_effect=AssertionError("no"))), \
            patch("integrations.phpipam.PhpIpamClient.get_addresses",
                  AsyncMock(side_effect=AssertionError("no live call"))):
        assert (await sync_phpipam_config(db, cfg))["errors"] == [
            "No phpIPAM data from the probe yet"]
        await snap_svc.save(db, "phpipam", cfg.id, ok=True, data=(await _phpipam_collect()).data)
        out = await sync_phpipam_config(db, cfg)
    assert (out["added"], out["skipped"]) == (3, 2)
    assert len((await db.execute(select(PingHost))).scalars().all()) == 3


async def test_phpipam_sync_of_a_core_config_is_live_as_before(db):
    from routers.settings.general import sync_phpipam_config

    cfg = await int_svc.create_config(db, "phpipam", "ipam", {
        "host": "https://ipam.example.com", "app_id": "ng", "app_secret": "tok"})
    from integrations.phpipam import PhpIpamClient

    with patch.object(PhpIpamClient, "get_addresses", AsyncMock(return_value=_PHPIPAM_ADDRESSES)):
        out = await sync_phpipam_config(db, cfg)
    assert out == {"added": 3, "merged": 0, "skipped": 2, "errors": []}
    with patch.object(PhpIpamClient, "get_addresses", AsyncMock(side_effect=OSError("down"))):
        assert (await sync_phpipam_config(db, cfg))["errors"] == ["down"]


# ── UniFi: reverse DNS in collect(), not in the hook ──────────────────────────

async def test_unifi_reverse_dns_runs_in_collect():
    from integrations import unifi

    async def ptr(ip):
        return {"192.0.2.5": "ap-1.example.com", "192.0.2.6": "192.0.2.6",
                "192.0.2.7": "nodots"}.get(ip)

    data = {"devices": [{"ip": "192.0.2.5"}, {"ip": "192.0.2.6"}, {"ip": "192.0.2.7"},
                        {"ip": ""}]}
    with patch.object(unifi, "reverse_lookup", ptr):
        out = await unifi.enrich_reverse_dns(data)
    assert [d.get("fqdn") for d in out["devices"]] == ["ap-1.example.com", None, None, None]


async def test_unifi_reverse_lookup_handles_missing_records():
    from integrations import unifi

    with patch("socket.gethostbyaddr", side_effect=OSError("no PTR")):
        assert await unifi.reverse_lookup("192.0.2.9") is None
    with patch("socket.gethostbyaddr", return_value=("sw.example.com", [], ["192.0.2.9"])):
        assert await unifi.reverse_lookup("192.0.2.9") == "sw.example.com"


async def test_unifi_host_import_needs_no_network(db):
    from integrations.unifi import import_unifi_devices
    from models.ping import PingHost

    data = {"devices": [
        {"ip": "192.0.2.5", "mac": "02:00:00:00:00:05", "name": "ap-1",
         "fqdn": "ap-1.example.com", "state": 1},
        {"ip": "192.0.2.6", "mac": "02:00:00:00:00:06", "name": "sw-1", "state": 1},
    ]}
    with patch("socket.gethostbyaddr", side_effect=AssertionError("network in a hook")):
        out = await import_unifi_devices("https://unifi.example.com", data, db)
    assert out == {"added": 2, "merged": 0, "skipped": 0}
    hosts = {h.ip_address: h.hostname for h in (await db.execute(select(PingHost))).scalars()}
    assert hosts == {"192.0.2.5": "ap-1.example.com", "192.0.2.6": "192.0.2.6"}


# ── Proxmox: backup sync by config id ─────────────────────────────────────────

async def test_proxmox_hook_syncs_backups_by_its_config_id_without_decrypting(db):
    from integrations.proxmox import ProxmoxIntegration

    inst = ProxmoxIntegration(config={"host": "https://192.0.2.40:8006"})
    inst.config_id = 17
    with patch("integrations.proxmox.import_proxmox_hosts", AsyncMock()), \
            patch("services.backup_monitor.sync_proxmox_backups", AsyncMock()) as sync, \
            patch.object(int_svc, "decrypt_config", AsyncMock(side_effect=AssertionError("no"))):
        await inst.on_snapshot({"cluster_name": "pve"}, {"host": "https://192.0.2.40:8006"}, db)
    sync.assert_awaited_once_with(db, 17, {"cluster_name": "pve"})

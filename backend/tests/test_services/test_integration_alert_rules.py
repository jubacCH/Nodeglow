"""parse_alerts() of each integration and the tenant's alert thresholds."""
import json

import pytest

from integrations import get_registry
from services import integration_thresholds as thr


def _alerts(name, data, **overrides):
    inst = get_registry()[name](config={})
    inst.threshold_overrides = overrides
    return [(a.severity, a.title, a.entity, a.host) for a in inst.parse_alerts(data)]


# ── Rules ────────────────────────────────────────────────────────────────────

def test_unifi_disconnected_device_and_wan():
    data = {"wan": {"status": "error"}, "devices": [
        {"name": "SW1", "mac": "02:00:00:00:00:01", "type_label": "Switch", "state": 0, "ip": "192.0.2.11"},
        {"name": "AP1", "mac": "02:00:00:00:00:02", "state": 1, "ip": "192.0.2.12"},
        {"name": "AP2", "mac": "02:00:00:00:00:03", "state": 5, "ip": "192.0.2.13"},  # upgrading
    ]}
    assert _alerts("unifi", data) == [
        ("critical", "WAN down", "wan", ""),
        ("critical", "Device disconnected", "device: 02:00:00:00:00:01", "192.0.2.11"),
    ]
    assert _alerts("unifi", {"wan": {"status": "ok"}, "devices": []}) == []


def test_swisscom_connection_down():
    assert _alerts("swisscom", {"device": {"status": "Up"}}) == []
    assert _alerts("swisscom", {"device": {"status": "Down"}})[0][:2] == ("critical", "Internet connection down")
    assert _alerts("swisscom", {"device": {}}) == []


def test_redfish_health_power_and_sensors():
    data = {"status": "Warning", "power_state": "Off", "health_summary": "PSU 2 failed",
            "temperatures": [{"name": "Inlet", "status": "OK"}, {"name": "CPU1", "status": "Critical"}],
            "fans": [{"name": "Fan 3", "status": "Absent"}]}
    assert [a[:2] for a in _alerts("redfish", data)] == [
        ("warning", "Server health degraded"),
        ("critical", "Server powered off"),
        ("critical", "Temperature sensor critical"),
    ]
    assert _alerts("redfish", {"status": "OK", "power_state": "On"}) == []


def test_portainer_crash_loops_and_health_not_stopped_containers():
    data = {"environments": [
        {"name": "local", "healthy": True, "containers": [
            {"name": "db", "state": "running", "status": "Up 2 hours (unhealthy)"},
            {"name": "web", "state": "restarting", "status": "Restarting (1) 3 seconds ago"},
            {"name": "old", "state": "exited", "status": "Exited (0) 2 days ago"},
        ]},
        {"name": "edge", "healthy": False, "status": "down", "containers": []},
    ]}
    assert [a[1] for a in _alerts("portainer", data)] == [
        "Container unhealthy", "Container restarting", "Environment unreachable"]


def test_adguard_pihole_firewall():
    assert _alerts("adguard", {"status": "stopped"})[0][:2] == ("critical", "DNS service stopped")
    assert _alerts("adguard", {"status": "running", "filtering_enabled": False})[0][1] == "Filtering disabled"
    assert _alerts("adguard", {"status": "running", "filtering_enabled": True}) == []
    assert _alerts("pihole", {"status": "disabled"})[0][1] == "Blocking disabled"
    assert _alerts("pihole", {"status": "enabled"}) == []
    assert [a[1] for a in _alerts("firewall", {"cpu_pct": 95, "mem_pct": 40})] == ["Firewall CPU high"]
    assert _alerts("firewall", {"cpu_pct": 95, "mem_pct": 40}, cpu_pct=99) == []


def test_speedtest_minimums_are_off_by_default_and_ignore_failed_runs():
    run = {"download_mbps": 80.0, "upload_mbps": 900.0}
    assert _alerts("speedtest", run) == []
    assert [a[1] for a in _alerts("speedtest", run, min_download_mbps=100)] == ["Download below minimum"]
    assert _alerts("speedtest", {"download_mbps": 0.0, "upload_mbps": 0.0}, min_download_mbps=100) == []


def test_proxmox_uses_thresholds_and_names_the_node():
    node = {"name": "pve2", "online": True, "cpu_pct": 91.0, "mem_pct": 50.0, "disk_pct": 20.0}
    assert _alerts("proxmox", {"nodes": [node]}) == []
    assert _alerts("proxmox", {"nodes": [node]}, node_cpu_pct=90) == [
        ("warning", "Node CPU critical", "node: pve2", "pve2")]
    assert _alerts("proxmox", {"nodes": [{**node, "online": False}]}) == [
        ("critical", "Node offline", "node: pve2", "pve2")]


def test_unas_disk_temperature_threshold():
    data = {"storage_pools": [], "raids": [], "disks": [{"name": "Slot 1", "ok": True, "temp": 52}]}
    assert [a[1] for a in _alerts("unas", data)] == ["Disk overheating"]
    assert _alerts("unas", data, disk_temp_c=55) == []


def test_npm_certificate_warning_days():
    data = {"certificates": [{"nice_name": "example.com", "days_left": 10}]}
    assert _alerts("npm", data) == []
    assert [a[0] for a in _alerts("npm", data, cert_warn_days=14)] == ["warning"]


@pytest.mark.parametrize("name", sorted(get_registry()))
def test_every_parse_alerts_survives_an_empty_snapshot(name):
    _alerts(name, {})


@pytest.mark.parametrize("name", sorted(get_registry()))
def test_declared_thresholds_have_valid_defaults(name):
    for t in get_registry()[name].alert_thresholds:
        assert t.minimum <= t.default and (t.maximum is None or t.default <= t.maximum), t
        assert get_registry()[name](config={}).threshold(t.key) == t.default


# ── Tenant overrides ─────────────────────────────────────────────────────────

def test_validate_drops_defaults_and_refuses_bad_values():
    keep, refused = thr.validate({
        "proxmox.node_cpu_pct": 90, "proxmox.node_mem_pct": 95,  # 95 is the default
        "proxmox.node_disk_pct": 150, "nope.x": 1, "npm.cert_warn_days": True,
    })
    assert keep == {"proxmox.node_cpu_pct": 90.0}
    assert sorted(refused) == ["nope.x", "npm.cert_warn_days", "proxmox.node_disk_pct"]


async def test_saved_overrides_reach_the_integration(db):
    assert await thr.save(db, {"proxmox.node_cpu_pct": 80, "npm.cert_warn_days": 30}) == []
    assert await thr.overrides_for(db, "proxmox") == {"node_cpu_pct": 80.0}
    rows = {r["id"]: r for r in await thr.catalogue(db)}
    assert rows["proxmox.node_cpu_pct"]["value"] == 80.0
    assert rows["proxmox.node_mem_pct"]["value"] == rows["proxmox.node_mem_pct"]["default"]


async def test_a_refused_value_stores_nothing(db):
    await thr.save(db, {"proxmox.node_cpu_pct": 80})
    assert await thr.save(db, {"proxmox.node_cpu_pct": 70, "proxmox.node_mem_pct": -1}) == ["proxmox.node_mem_pct"]
    assert await thr.overrides_for(db, "proxmox") == {"node_cpu_pct": 80.0}


async def test_a_broken_setting_falls_back_to_the_defaults(db, caplog):
    from database import set_setting

    await set_setting(db, thr.SETTING, "{not json")
    assert await thr.load(db) == {}
    await set_setting(db, thr.SETTING, json.dumps({"proxmox.node_cpu_pct": "high"}))
    assert await thr.load(db) == {}

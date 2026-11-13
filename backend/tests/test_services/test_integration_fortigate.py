"""FortiGate integration: parsers, alerts and collect() through a mocked transport."""
from __future__ import annotations

import httpx
import pytest

from integrations import fortigate
from integrations.fortigate import (
    FortiGateIntegration,
    parse_firmware,
    parse_ha,
    parse_interfaces,
    parse_ipsec,
    parse_licenses,
    parse_resources,
    parse_status,
)

TOKEN = "s3cr3t-token-value"
NOW = "2026-11-12T10:00:00+00:00"
NOW_EPOCH = 1794477600  # 2026-11-12T10:00:00Z


def _epoch_in(days: float) -> int:
    return int(NOW_EPOCH + days * 86400)


# ── Payload builders ─────────────────────────────────────────────────────────

STATUS = {
    "http_method": "GET", "status": "success", "serial": "FGT60FTK00000001",
    "version": "v7.2.4", "build": 1396, "vdom": "root",
    "results": {"model_name": "FortiGate", "model_number": "60F", "hostname": "fw-edge"},
}
USAGE = {"results": {
    "cpu": [{"current": 12, "historical": {"1-min": {"min": 3, "max": 20, "average": 9}}}],
    "mem": [{"current": 41, "historical": {"1-min": {"average": 40}}}],
    "session": [{"current": 5230, "historical": {}}],
    "setuprate": [{"current": 17, "historical": {}}],
}}
IFACES = {"results": {
    "wan1": {"id": "wan1", "name": "wan1", "link": True, "speed": 1000.0, "ip": "203.0.113.2", "mask": 29,
             "rx_bytes": 1000, "tx_bytes": 2000, "rx_packets": 10, "tx_packets": 20, "rx_errors": 0, "tx_errors": 1},
    "lan2": {"id": "lan2", "name": "lan2", "link": False, "speed": 0.0, "ip": "0.0.0.0", "mask": 0,
             "rx_bytes": 5000, "tx_bytes": 5000, "rx_packets": 5, "tx_packets": 5},
    "lan3": {"id": "lan3", "name": "lan3", "link": False, "speed": 0.0,
             "rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0},
    "lan4": {"id": "lan4", "name": "lan4", "link": False, "rx_bytes": 9, "tx_bytes": 9},
}}
IFACES_CFG = {"results": [
    {"name": "wan1", "status": "up", "type": "physical", "role": "wan"},
    {"name": "lan2", "status": "up", "type": "physical", "role": "lan"},
    {"name": "lan3", "status": "up", "type": "physical", "role": "lan"},
    {"name": "lan4", "status": "down", "type": "physical", "role": "lan"},
]}
IPSEC = {"results": [
    {"name": "to-hq", "rgwy": "198.51.100.1", "type": "automatic",
     "proxyid": [{"p2name": "p2-a", "status": "up", "incoming_bytes": 10, "outgoing_bytes": 20}]},
    {"name": "to-branch", "rgwy": "198.51.100.9", "type": "automatic",
     "proxyid": [{"p2name": "p2-b", "status": "down"}]},
    {"name": "mixed", "rgwy": "198.51.100.5",
     "proxyid": [{"p2name": "one", "status": "up"}, {"p2name": "two", "status": "down"}]},
]}
LICENSE = {"results": {
    "forticare": {"support": {"enhanced": {"expires": _epoch_in(200), "entitled": True}}, "status": "registered"},
    "antivirus": {"expires": _epoch_in(5), "status": "licensed"},
    "ips": {"expires": _epoch_in(-3), "status": "expired"},
    "web_filtering": {"expires": 0, "status": "unlicensed"},
}}
FIRMWARE = {"results": {"current": {"version": "v7.2.4"},
                        "available": [{"version": "v7.2.5"}, {"version": "v7.4.1"}]}}
HA_PEERS = {"results": [{"hostname": "fw-2", "serial_no": "FGT-2", "priority": 100, "master": False}]}
HA_SUMS_OK = {"results": [{"serial_no": "A", "checksum": {"all": "x"}}, {"serial_no": "B", "checksum": {"all": "x"}}]}
HA_SUMS_BAD = {"results": [{"serial_no": "A", "checksum": {"all": "x"}}, {"serial_no": "B", "checksum": {"all": "y"}}]}


# ── Parsers ──────────────────────────────────────────────────────────────────


def test_status():
    s = parse_status(STATUS)
    assert s["hostname"] == "fw-edge" and s["version"] == "v7.2.4"
    assert s["serial"] == "FGT60FTK00000001" and s["model"] == "FortiGate 60F" and s["build"] == 1396


def test_status_empty():
    s = parse_status({})
    assert s["hostname"] == "" and s["build"] is None


def test_resources_take_current_of_interval_arrays():
    r = parse_resources(USAGE)
    assert r == {"cpu_pct": 12.0, "mem_pct": 41.0, "sessions": 5230, "session_rate": 17}


def test_resources_missing_is_none_not_zero():
    r = parse_resources({"results": {"cpu": [], "mem": [{"historical": {}}]}})
    assert r["cpu_pct"] is None and r["mem_pct"] is None and r["sessions"] is None
    assert parse_resources({})["cpu_pct"] is None


def test_interfaces_merge_admin_status():
    by = {i["name"]: i for i in parse_interfaces(IFACES, IFACES_CFG)}
    assert by["wan1"]["ipaddr"] == "203.0.113.2/29" and by["wan1"]["admin_status"] == "up"
    assert by["lan2"]["ipaddr"] == "" and by["lan2"]["ever_active"] is True
    assert by["lan3"]["ever_active"] is False
    assert by["lan4"]["admin_status"] == "down"


def test_interfaces_without_cmdb_have_unknown_admin_status():
    by = {i["name"]: i for i in parse_interfaces(IFACES, None)}
    assert by["lan2"]["admin_status"] is None and by["lan2"]["type"] is None


def test_ipsec_states():
    by = {t["name"]: t for t in parse_ipsec(IPSEC)}
    assert by["to-hq"]["state"] == "up"
    assert by["to-branch"]["state"] == "down"
    assert by["mixed"]["state"] == "up"
    assert parse_ipsec({}) == [] and parse_ipsec({"results": "x"}) == []


def test_ipsec_without_any_status_is_unknown():
    assert parse_ipsec({"results": [{"name": "t"}]})[0]["state"] == "unknown"
    assert parse_ipsec({"results": [{"name": "t", "proxyid": []}]})[0]["state"] == "down"


def test_ha_states():
    assert parse_ha("standalone", {"results": []}, None)["state"] == "standalone"
    assert parse_ha("a-p", {"results": []}, None)["state"] == "peer_missing"
    assert parse_ha("a-p", HA_PEERS, HA_SUMS_OK)["state"] == "healthy"
    assert parse_ha("a-p", HA_PEERS, HA_SUMS_BAD)["state"] == "out_of_sync"
    assert parse_ha("a-p", HA_PEERS, None)["state"] == "healthy"


def test_ha_peer_list_holding_only_the_local_unit_is_peer_missing():
    # ha-peer may list the local unit too; a lost peer then leaves only ourselves.
    own = {"results": [{"hostname": "fw-1", "serial_no": "FGT-1", "priority": 200, "master": True}]}
    assert parse_ha("a-p", own, None, own_serial="FGT-1")["state"] == "peer_missing"
    both = {"results": own["results"] + HA_PEERS["results"]}
    assert parse_ha("a-p", both, HA_SUMS_OK, own_serial="FGT-1")["state"] == "healthy"
    # A list without the local unit still counts its entries as peers.
    assert parse_ha("a-p", HA_PEERS, None, own_serial="FGT-1")["state"] == "healthy"


async def test_collect_passes_own_serial_to_ha(monkeypatch):
    alone = {"results": [{"hostname": "fw-edge", "serial_no": STATUS["serial"], "master": True}]}
    _install(monkeypatch, _router({"/api/v2/monitor/system/ha-peer": alone}))
    res = await _plugin().collect()
    assert res.data["ha"]["state"] == "peer_missing"
    assert [a.entity for a in _alerts(res.data) if a.entity.startswith("ha")] == ["ha: peer"]


def test_firmware_ignores_downgrades_and_the_running_version():
    body = {"results": {"current": {"version": "v7.2.4", "major": 7, "minor": 2, "patch": 4},
                        "available": [{"version": "v7.0.12", "major": 7, "minor": 0, "patch": 12},
                                      {"version": "v7.2.4", "major": 7, "minor": 2, "patch": 4},
                                      {"version": "v7.2.5", "major": 7, "minor": 2, "patch": 5}]}}
    f = parse_firmware(body)
    assert f["available"] == ["v7.2.5"] and f["update_available"] is True
    only_old = {"results": {"current": {"version": "v7.2.4"}, "available": [{"version": "v7.0.12"}]}}
    assert parse_firmware(only_old)["update_available"] is False


def test_ha_unreadable_is_unknown():
    assert parse_ha("a-p", None, None)["state"] is None
    assert parse_ha(None, {"results": []}, None)["state"] is None
    assert parse_ha(None, HA_PEERS, HA_SUMS_OK)["state"] == "healthy"


def test_licenses_skip_zero_expiry():
    names = [lic["name"] for lic in parse_licenses(LICENSE)]
    assert names == ["antivirus", "forticare.support.enhanced", "ips"]
    assert parse_licenses({}) == []


def test_firmware():
    f = parse_firmware(FIRMWARE)
    assert f["current"] == "v7.2.4" and f["available"] == ["v7.2.5", "v7.4.1"] and f["update_available"]
    assert parse_firmware({})["update_available"] is False


# ── Alerts ───────────────────────────────────────────────────────────────────


def _alerts(data, overrides=None):
    p = FortiGateIntegration({})
    p.threshold_overrides = overrides or {}
    return p.parse_alerts(data)


def _snap(**kw):
    base = {"collected_at": NOW}
    base.update(kw)
    return base


def test_alerts_never_raise_on_junk():
    for d in ({}, {"interfaces": [None, 1, {}], "ipsec": [None], "licenses": ["x"], "ha": "x",
                   "cpu_pct": "high"}, {"collected_at": "garbage", "licenses": [{"name": "a", "expires_at": "x"}]}):
        assert _alerts(d) == []


def test_cpu_memory_edges():
    assert _alerts(_snap(cpu_pct=89.9, mem_pct=89.9)) == []
    a = _alerts(_snap(cpu_pct=90, mem_pct=90))
    assert {x.entity for x in a} == {"cpu", "memory"}
    assert _alerts(_snap(cpu_pct=None, mem_pct=None)) == []
    assert len(_alerts(_snap(cpu_pct=80), {"cpu_pct": 75})) == 1


def test_ipsec_alerts():
    a = _alerts(_snap(ipsec=parse_ipsec(IPSEC)))
    ents = {x.entity: x.severity for x in a}
    assert ents == {"ipsec: to-branch": "critical", "ipsec: mixed/two": "warning"}
    assert _alerts(_snap(ipsec=None)) == []


def test_ha_alerts():
    assert _alerts(_snap(ha=parse_ha("a-p", {"results": []}, None)))[0].entity == "ha: peer"
    assert _alerts(_snap(ha=parse_ha("a-p", HA_PEERS, HA_SUMS_BAD)))[0].entity == "ha: sync"
    assert _alerts(_snap(ha=parse_ha("a-p", HA_PEERS, HA_SUMS_OK))) == []
    assert _alerts(_snap(ha=parse_ha("a-p", None, None))) == []


def test_interface_alert_only_for_used_enabled_ports():
    a = _alerts(_snap(interfaces=parse_interfaces(IFACES, IFACES_CFG)))
    # lan2: enabled, no link, has moved traffic -> alert.
    # lan3: never had traffic; lan4: administratively down; wan1: link up.
    assert [x.entity for x in a] == ["interface: lan2"]


def test_interface_without_admin_status_never_alerts():
    assert _alerts(_snap(interfaces=parse_interfaces(IFACES, None))) == []


def test_license_edges():
    def lic(days):
        return [{"name": "av", "expires_at": fortigate._iso(_epoch_in(days))}]

    assert _alerts(_snap(licenses=lic(31))) == []
    w = _alerts(_snap(licenses=lic(30)))
    assert w[0].severity == "warning" and w[0].entity == "license: av"
    assert _alerts(_snap(licenses=lic(8)))[0].severity == "warning"
    assert _alerts(_snap(licenses=lic(7)))[0].severity == "critical"
    e = _alerts(_snap(licenses=lic(-1)))
    assert e[0].severity == "critical" and e[0].title == "Licence expired"


def test_license_without_collected_at_is_skipped():
    lic = [{"name": "av", "expires_at": fortigate._iso(_epoch_in(-5))}]
    assert _alerts({"licenses": lic}) == []


# ── collect() through a mocked transport ─────────────────────────────────────


def _install(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))

    monkeypatch.setattr(fortigate, "safe_async_client", factory)
    return seen


def _ok(body):
    return httpx.Response(200, json=body)


def _router(overrides=None):
    routes = {
        "/api/v2/monitor/system/status": STATUS,
        "/api/v2/monitor/system/resource/usage": USAGE,
        "/api/v2/monitor/system/interface": IFACES,
        "/api/v2/cmdb/system/interface": IFACES_CFG,
        "/api/v2/monitor/vpn/ipsec": IPSEC,
        "/api/v2/cmdb/system/ha": {"results": {"mode": "a-p"}},
        "/api/v2/monitor/system/ha-peer": HA_PEERS,
        "/api/v2/monitor/system/ha-checksums": HA_SUMS_OK,
        "/api/v2/monitor/license/status": LICENSE,
        "/api/v2/monitor/system/firmware": FIRMWARE,
    }
    routes.update(overrides or {})

    def handler(request):
        r = routes.get(request.url.path)
        if isinstance(r, httpx.Response):
            return r
        if r is None:
            return httpx.Response(404, json={"status": "error"})
        return _ok(r)

    return handler


def _plugin(**cfg):
    base = {"host": "https://fw.example.com", "api_token": TOKEN}
    base.update(cfg)
    return FortiGateIntegration(base)


async def test_collect_ok_and_token_only_in_header(monkeypatch):
    seen = _install(monkeypatch, _router())
    res = await _plugin(vdom="root").collect()
    assert res.success, res.error
    d = res.data
    assert d["hostname"] == "fw-edge" and d["cpu_pct"] == 12.0 and d["unavailable"] == []
    assert d["ha"]["state"] == "healthy" and len(d["ipsec"]) == 3
    assert d["collected_at"].endswith("+00:00")
    assert len(seen) >= 10
    for req in seen:
        assert TOKEN not in str(req.url)
        assert "access_token" not in str(req.url)
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.url.params.get("vdom") == "root"


async def test_collect_rejected_token_fails(monkeypatch):
    _install(monkeypatch, _router({"/api/v2/monitor/system/status": httpx.Response(401, json={})}))
    res = await _plugin().collect()
    assert res.success is False and "token" in res.error and TOKEN not in res.error


async def test_collect_unreachable_fails(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _install(monkeypatch, boom)
    res = await _plugin().collect()
    assert res.success is False and res.error


async def test_collect_non_json_status_fails(monkeypatch):
    _install(monkeypatch, _router({"/api/v2/monitor/system/status": httpx.Response(200, text="<html>")}))
    assert (await _plugin().collect()).success is False


async def test_failing_optional_parts_are_named(monkeypatch):
    _install(monkeypatch, _router({
        "/api/v2/monitor/vpn/ipsec": httpx.Response(500),
        "/api/v2/monitor/license/status": httpx.Response(403),
        "/api/v2/cmdb/system/interface": httpx.Response(403),
    }))
    res = await _plugin().collect()
    assert res.success
    assert res.data["unavailable"] == ["IPsec tunnels", "interface admin status", "licences"]
    assert res.data["ipsec"] is None and res.data["licenses"] is None
    # Unknown stays unknown: no alert from the missing parts.
    assert _alerts(res.data) == []


async def test_standalone_skips_ha_requests(monkeypatch):
    seen = _install(monkeypatch, _router({"/api/v2/cmdb/system/ha": {"results": {"mode": "standalone"}}}))
    res = await _plugin().collect()
    assert res.data["ha"]["state"] == "standalone"
    assert not any("ha-peer" in r.url.path for r in seen)


def test_host_without_scheme_gets_https():
    assert fortigate.FortiGateAPI("fw.local", "t").base == "https://fw.local"

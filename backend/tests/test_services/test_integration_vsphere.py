"""VMware vSphere integration: parsers, alerts and collect() through a mocked transport."""
import httpx
import pytest

from integrations import vsphere
from integrations.vsphere import (
    APPLIANCE_HEALTH,
    VsphereIntegration,
    parse_clusters,
    parse_datastores,
    parse_health_value,
    parse_hosts,
    parse_version,
    parse_vms,
)

CONFIG = {"host": "https://vc.example.com/", "username": "svc@vsphere.local", "password": "pw-secret"}
GB = 1024 ** 3


def _plugin() -> VsphereIntegration:
    return VsphereIntegration(CONFIG)


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_parse_hosts():
    out = parse_hosts([
        {"host": "host-2", "name": "esx2.example.com", "connection_state": "DISCONNECTED", "power_state": "POWERED_ON"},
        {"host": "host-1", "name": "esx1.example.com", "connection_state": "CONNECTED", "power_state": "POWERED_ON"},
        {"host": "host-3"},
        "junk",
    ])
    assert [h["name"] for h in out] == ["esx1.example.com", "esx2.example.com", "host-3"]
    assert out[2]["connection_state"] is None


def test_parse_vms_keeps_missing_numbers_none():
    out = parse_vms([
        {"vm": "vm-1", "name": "web", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 8192},
        {"vm": "vm-2", "name": "tpl", "power_state": "POWERED_OFF"},
    ])
    by = {v["name"]: v for v in out}
    assert by["tpl"]["cpu_count"] is None and by["tpl"]["memory_mib"] is None
    assert by["web"]["memory_mib"] == 8192
    assert parse_vms(None) == []


def test_parse_datastores_usage():
    out = parse_datastores([
        {"datastore": "datastore-1", "name": "vsan", "type": "VSAN", "capacity": 100 * GB, "free_space": 25 * GB},
        {"datastore": "datastore-2", "name": "iso", "type": "NFS", "capacity": 0, "free_space": 0},
        {"datastore": "datastore-3", "name": "detached", "type": "VMFS"},
    ])
    by = {d["name"]: d for d in out}
    assert by["vsan"]["pct"] == 75.0 and by["vsan"]["used"] == 75 * GB
    assert by["iso"]["pct"] is None
    assert by["detached"]["pct"] is None and by["detached"]["capacity"] is None


def test_parse_clusters_version_and_health():
    assert parse_clusters([{"cluster": "domain-c1", "name": "Prod", "ha_enabled": True, "drs_enabled": False}]) == [
        {"id": "domain-c1", "name": "Prod", "ha_enabled": True, "drs_enabled": False}]
    v = parse_version({"version": "8.0.2.00100", "build": "22617221", "product": "VMware vCenter Server"})
    assert v["version"] == "8.0.2.00100" and v["summary"] is None
    assert parse_version("nope") is None
    assert parse_health_value("GREEN") == "green"
    assert parse_health_value({"x": 1}) is None and parse_health_value("") is None


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_alerts_tolerate_empty():
    p = _plugin()
    assert p.parse_alerts({}) == []
    assert p.parse_alerts({"hosts": None, "datastores": None, "appliance_health": None}) == []


def test_host_alerts_set_host_and_entity():
    data = {"hosts": parse_hosts([
        {"host": "h1", "name": "esx1", "connection_state": "CONNECTED", "power_state": "POWERED_ON"},
        {"host": "h2", "name": "esx2", "connection_state": "DISCONNECTED", "power_state": "POWERED_ON"},
        {"host": "h3", "name": "esx3", "connection_state": "NOT_RESPONDING", "power_state": "POWERED_ON"},
        {"host": "h4", "name": "esx4", "power_state": "POWERED_ON"},
        {"host": "h5", "name": "esx5", "connection_state": "WEIRD"},
    ])}
    got = {a.entity: (a.severity, a.host) for a in _plugin().parse_alerts(data)}
    assert got == {
        "host: esx2": ("critical", "esx2"),
        "host: esx3": ("critical", "esx3"),
        "host: esx5": ("warning", "esx5"),
    }


def test_datastore_threshold_edges():
    def ds(name, used_pct):
        return {"name": name, "capacity": 1000, "free_space": 1000 - used_pct * 10}

    data = {"datastores": [ds("a79", 79.9), ds("a80", 80), ds("a89", 89.9), ds("a90", 90), ds("a100", 100),
                           {"name": "unknown", "capacity": None, "free_space": None},
                           {"name": "zero", "capacity": 0, "free_space": 0}]}
    got = {a.entity: a.severity for a in _plugin().parse_alerts(data)}
    assert got == {"datastore: a80": "warning", "datastore: a89": "warning",
                   "datastore: a90": "critical", "datastore: a100": "critical"}


def test_datastore_threshold_override():
    p = _plugin()
    p.threshold_overrides = {"datastore_warn_pct": 50, "datastore_crit_pct": 60}
    got = p.parse_alerts({"datastores": [{"name": "x", "capacity": 100, "free_space": 45}]})
    assert [a.severity for a in got] == ["warning"]


def test_appliance_health_alerts():
    data = {"appliance_health": {"system": "green", "storage": "yellow", "database_storage": "red",
                                 "load": "gray", "memory": None, "swap": "orange"}}
    got = {a.entity: a.severity for a in _plugin().parse_alerts(data)}
    # gray means vCenter has no data: unknown, not an alert.
    assert got == {"appliance: storage": "warning", "appliance: database_storage": "critical",
                   "appliance: swap": "warning"}


def test_appliance_health_incident_key_survives_a_colour_change():
    def key(colour):
        (a,) = _plugin().parse_alerts({"appliance_health": {"storage": colour}})
        return a.title, a.entity

    assert key("yellow") == key("orange") == key("red")


# ── collect() through a mocked transport ──────────────────────────────────────

HOSTS = [{"host": "host-1", "name": "esx1", "connection_state": "CONNECTED", "power_state": "POWERED_ON"}]
DATASTORES = [{"datastore": "datastore-1", "name": "ds1", "type": "VMFS", "capacity": 100 * GB, "free_space": 10 * GB}]
VMS = [{"vm": "vm-1", "name": "web", "power_state": "POWERED_ON", "cpu_count": 2, "memory_size_MiB": 4096}]
CLUSTERS = [{"cluster": "domain-c1", "name": "Prod", "ha_enabled": True, "drs_enabled": True}]
VERSION = {"version": "8.0.2.00100", "build": "1", "product": "VMware vCenter Server"}


class Fake:
    """Records requests; routes maps (method, path) to (status, body) or a callable."""

    def __init__(self, routes, session_status=201):
        self.routes = routes
        self.session_status = session_status
        self.log: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.log.append((request.method, request.url.path, dict(request.url.params)))
        if key == ("POST", "/api/session"):
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(self.session_status, json="TOKEN-1" if self.session_status < 300 else {})
        if key == ("DELETE", "/api/session"):
            assert request.headers["vmware-api-session-id"] == "TOKEN-1"
            return httpx.Response(204)
        assert request.headers["vmware-api-session-id"] == "TOKEN-1"
        spec = self.routes.get(key)
        if spec is None:
            return httpx.Response(404, json={"error_type": "NOT_FOUND"})
        if callable(spec):
            return spec(request)
        return httpx.Response(spec[0], json=spec[1])

    @property
    def deleted(self) -> int:
        return sum(1 for m, p, _ in self.log if (m, p) == ("DELETE", "/api/session"))


def _ok_routes(override=None):
    routes = {
        ("GET", "/api/vcenter/host"): (200, HOSTS),
        ("GET", "/api/vcenter/datastore"): (200, DATASTORES),
        ("GET", "/api/vcenter/vm"): (200, VMS),
        ("GET", "/api/vcenter/cluster"): (200, CLUSTERS),
        ("GET", "/api/appliance/system/version"): (200, VERSION),
    }
    for path in APPLIANCE_HEALTH.values():
        routes[("GET", path)] = (200, "green")
    routes.update(override or {})
    return routes


def _install(monkeypatch, fake: Fake):
    monkeypatch.setattr(vsphere, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(fake)))


async def test_collect_success_deletes_session(monkeypatch):
    fake = Fake(_ok_routes())
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert res.success, res.error
    d = res.data
    assert d["unavailable"] == [] and d["vm_list_error"] is None
    assert d["version"]["version"] == "8.0.2.00100"
    assert d["totals"] == {"hosts_total": 1, "hosts_connected": 1, "vms_total": 1, "vms_powered_on": 1,
                           "datastores_total": 1, "clusters_total": 1}
    assert d["datastores"][0]["pct"] == 90.0
    assert d["appliance_health"]["system"] == "green"
    assert fake.deleted == 1
    assert fake.log[-1][:2] == ("DELETE", "/api/session")  # session is closed last


async def test_bad_credentials_fail_and_do_not_leak_password(monkeypatch):
    fake = Fake({}, session_status=401)
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert not res.success and "pw-secret" not in res.error
    assert fake.deleted == 0  # there is no session to close


async def test_core_request_failure_fails_and_session_is_still_deleted(monkeypatch):
    fake = Fake(_ok_routes({("GET", "/api/vcenter/host"): (500, {})}))
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert not res.success and "500" in res.error
    assert fake.deleted == 1


async def test_session_deleted_when_transport_raises(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")

    fake = Fake(_ok_routes({("GET", "/api/vcenter/datastore"): boom}))
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert not res.success
    assert fake.deleted == 1


async def test_failed_delete_does_not_fail_collection(monkeypatch):
    fake = Fake(_ok_routes())

    def handler(request):
        if request.method == "DELETE":
            raise httpx.ConnectError("gone")
        return fake(request)

    monkeypatch.setattr(vsphere, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await _plugin().collect()
    assert res.success


async def test_non_vcsa_puts_appliance_parts_into_unavailable(monkeypatch):
    routes = _ok_routes()
    for path in list(APPLIANCE_HEALTH.values()) + ["/api/appliance/system/version"]:
        routes.pop(("GET", path))
    fake = Fake(routes)
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert res.success
    assert res.data["version"] is None
    assert res.data["unavailable"] == ["appliance health (HTTP 404)", "vCenter version (HTTP 404)"]
    assert set(res.data["appliance_health"].values()) == {None}
    assert not any(a.entity.startswith("appliance:") for a in _plugin().parse_alerts(res.data))


async def test_single_health_endpoint_missing_is_named(monkeypatch):
    routes = _ok_routes({("GET", "/api/appliance/health/swap"): (403, {})})
    _install(monkeypatch, Fake(routes))
    res = await _plugin().collect()
    assert res.success
    assert res.data["unavailable"] == ["appliance health: swap (HTTP 403)"]
    assert res.data["appliance_health"]["swap"] is None


async def test_unhealthy_appliance_value_is_alerted(monkeypatch):
    routes = _ok_routes({("GET", "/api/appliance/health/storage"): (200, "red")})
    _install(monkeypatch, Fake(routes))
    res = await _plugin().collect()
    alerts = _plugin().parse_alerts(res.data)
    assert [(a.entity, a.severity) for a in alerts if a.entity.startswith("appliance:")] == [
        ("appliance: storage", "critical")]


async def test_vm_list_limit_falls_back_per_host_and_is_reported(monkeypatch):
    def vms(request):
        if "hosts" in request.url.params:
            assert request.url.params["hosts"] == "host-1"
            return httpx.Response(200, json=VMS)
        return httpx.Response(400, json={"error_type": "UNABLE_TO_ALLOCATE_RESOURCE", "messages": []})

    fake = Fake(_ok_routes({("GET", "/api/vcenter/vm"): vms}))
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert res.success
    assert len(res.data["vms"]) == 1
    assert "more than 4000" in res.data["vm_list_error"]
    assert res.data["unavailable"] == ["VMs (incomplete)"]


async def test_vm_list_limit_without_recovery_reports_vms_unavailable(monkeypatch):
    def vms(request):
        return httpx.Response(400, json={"error_type": "UNABLE_TO_ALLOCATE_RESOURCE"})

    _install(monkeypatch, Fake(_ok_routes({("GET", "/api/vcenter/vm"): vms})))
    res = await _plugin().collect()
    assert res.success
    assert res.data["vms"] is None and res.data["totals"]["vms_total"] is None
    assert res.data["unavailable"] == ["VMs"]
    assert "per-host retry failed" in res.data["vm_list_error"]


async def test_other_vm_list_error_is_not_retried(monkeypatch):
    fake = Fake(_ok_routes({("GET", "/api/vcenter/vm"): (403, {"error_type": "UNAUTHORIZED"})}))
    _install(monkeypatch, fake)
    res = await _plugin().collect()
    assert res.success and res.data["vms"] is None
    assert res.data["unavailable"] == ["VMs"]
    assert sum(1 for m, p, _ in fake.log if p == "/api/vcenter/vm") == 1


async def test_missing_config_fails_cleanly():
    res = await VsphereIntegration({}).collect()
    assert not res.success and res.error


def test_metadata():
    p = VsphereIntegration
    assert p.name == "vsphere" and p.network_scope == "any"
    pw = next(f for f in p.config_fields if f.key == "password")
    assert pw.encrypted and pw.field_type == "password"


@pytest.mark.parametrize("raw,expected", [
    ("vc.example.com", "https://vc.example.com"),
    ("https://vc.example.com/", "https://vc.example.com"),
    ("", ""),
])
def test_normalize_base(raw, expected):
    assert vsphere.normalize_base(raw) == expected


async def test_gray_appliance_value_is_unavailable_not_alerted(monkeypatch):
    routes = _ok_routes({("GET", "/api/appliance/health/load"): (200, "gray")})
    _install(monkeypatch, Fake(routes))
    res = await _plugin().collect()
    assert res.success
    assert res.data["unavailable"] == ["appliance health: load (unknown (gray))"]
    assert not any(a.entity == "appliance: load" for a in _plugin().parse_alerts(res.data))

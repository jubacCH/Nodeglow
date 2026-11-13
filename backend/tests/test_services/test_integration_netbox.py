"""NetBox integration: parsers, alerts, collect() through a mocked transport and
the opt-in host import (add-only, no duplicates).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from integrations import netbox as nb
from integrations.netbox import (
    NetBoxIntegration,
    apply_netbox_hosts,
    build_snapshot,
    importable_entries,
    parse_device,
    parse_prefix,
    parse_site,
    parse_vm,
)

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


# ── Payload builders (shapes of NetBox 4.x REST responses) ───────────────────


def status(value="active"):
    return {"value": value, "label": value.capitalize()}


def device(id_=1, name="sw-01", st="active", ip4="192.0.2.10/24", ip6=None, site="Zurich",
           role="Switch", tenant="Acme", legacy_role=False):
    obj = {
        "id": id_, "name": name, "status": status(st),
        "site": {"id": 1, "name": site} if site else None,
        "tenant": {"id": 1, "name": tenant} if tenant else None,
        "device_type": {"id": 3, "model": "EX4300"},
        "primary_ip4": {"id": 9, "address": ip4} if ip4 else None,
        "primary_ip6": {"id": 10, "address": ip6} if ip6 else None,
    }
    obj["device_role" if legacy_role else "role"] = {"id": 2, "name": role}
    return obj


def vm(id_=1, name="vm-01", st="active", ip4="192.0.2.50/24", cluster="pve"):
    return {
        "id": id_, "name": name, "status": status(st), "role": None, "site": None,
        "cluster": {"id": 1, "name": cluster}, "tenant": None,
        "primary_ip4": {"address": ip4} if ip4 else None, "primary_ip6": None,
    }


def prefix(id_=1, cidr="192.0.2.0/24", **extra):
    return {"id": id_, "prefix": cidr, "status": status("active"), "vrf": None,
            "scope": {"name": "Zurich"}, "tenant": None, "description": "LAN", **extra}


def site(id_=1, name="Zurich"):
    return {"id": id_, "name": name, "status": status("active"), "region": {"name": "CH"},
            "tenant": None, "device_count": 12}


def snapshot(devices=(), vms=(), prefixes=(), sites=(), failed=None, cut=None, counts=None):
    cut = cut or {}
    counts = counts or {}

    def entry(key, objs):
        return list(objs), counts.get(key, len(objs)), bool(cut.get(key))

    return build_snapshot(
        status={"netbox-version": "4.1.5", "python-version": "3.12.3", "rq-workers-running": 2},
        raw={"sites": entry("sites", sites), "devices": entry("devices", devices),
             "vms": entry("vms", vms), "prefixes": entry("prefixes", prefixes)},
        failed_devices=[parse_device(d) for d in devices if d["status"]["value"] == "failed"] if failed is None else failed,
        now=NOW, unavailable=set(), source_url="https://netbox.example.com",
    )


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_device_row():
    row = parse_device(device(ip4="192.0.2.10/24"))
    assert row == {"id": 1, "name": "sw-01", "status": "active", "role": "Switch", "type": "EX4300",
                   "site": "Zurich", "tenant": "Acme", "ip4": "192.0.2.10", "ip6": "", "ip": "192.0.2.10"}


def test_device_legacy_role_and_ipv6_only():
    row = parse_device(device(legacy_role=True, ip4=None, ip6="2001:db8::5/64"))
    assert row["role"] == "Switch"
    assert row["ip4"] == "" and row["ip6"] == "2001:db8::5" and row["ip"] == "2001:db8::5"


def test_device_prefers_ipv4_when_both_exist():
    assert parse_device(device(ip6="2001:db8::5/64"))["ip"] == "192.0.2.10"


def test_device_with_missing_or_odd_fields():
    row = parse_device({"id": "7", "status": "offline"})
    assert row["id"] == 7 and row["status"] == "offline" and row["name"] == "" and row["ip"] == ""
    assert parse_device({"primary_ip4": {"address": "not-an-ip"}})["ip"] == ""
    assert parse_device({"primary_ip4": "x", "site": 3, "status": None})["status"] == ""
    assert parse_device({"status": {"label": "Planned"}})["status"] == "planned"


def test_vm_row():
    row = parse_vm(vm())
    assert row["cluster"] == "pve" and row["ip"] == "192.0.2.50" and row["status"] == "active"
    assert parse_vm({})["name"] == ""


def test_prefix_utilization_is_none_when_not_reported():
    assert parse_prefix(prefix())["utilization"] is None
    assert parse_prefix(prefix(utilization=37.456))["utilization"] == 37.5
    assert parse_prefix(prefix(utilization=0))["utilization"] == 0.0     # a real zero is not "unknown"
    assert parse_prefix(prefix(utilization="n/a"))["utilization"] is None
    assert parse_prefix({"site": {"name": "Bern"}})["scope"] == "Bern"


def test_site_row():
    assert parse_site(site()) == {"id": 1, "name": "Zurich", "status": "active", "region": "CH",
                                  "tenant": "", "device_count": 12}


# ── Snapshot ──────────────────────────────────────────────────────────────────


def test_snapshot_totals_use_the_api_count_when_the_list_is_cut():
    snap = snapshot(devices=[device(1), device(2, "sw-02", "failed")], counts={"devices": 5000}, cut={"devices": True})
    assert snap["totals"]["devices"] == 5000
    assert snap["truncated"] == {"sites": False, "devices": True, "vms": False, "prefixes": False}
    assert snap["device_status"] == {"active": 1, "failed": 1}
    assert snap["version"] == "4.1.5" and snap["collected_at"] == "2026-11-12T12:00:00Z"


def test_cut_list_without_a_count_has_no_total():
    snap = build_snapshot(status={}, raw={"devices": ([device()], None, True)}, failed_devices=[],
                          now=NOW, unavailable=set())
    assert snap["totals"]["devices"] is None


def test_unavailable_resources_are_unknown_not_zero():
    snap = build_snapshot(status={}, raw={"sites": ([site()], 1, False)}, failed_devices=None, now=NOW,
                          unavailable={"devices (token lacks permission: dcim.view_device)"})
    assert snap["totals"] == {"sites": 1, "devices": None, "vms": None, "prefixes": None}
    assert snap["devices"] is None and snap["device_status"] is None and snap["failed_devices"] is None
    assert snap["unavailable"] == ["devices (token lacks permission: dcim.view_device)"]


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_parse_alerts_tolerates_anything():
    plugin = NetBoxIntegration(config={})
    for sample in ({}, {"foo": "bar"}, {"devices": []}, {"failed_devices": None}, {"failed_devices": "x"},
                   {"failed_devices": [None, 3, {}, {"status": "failed"}]}):
        assert plugin.parse_alerts(sample) == []
    assert plugin.parse_alerts(None) == []  # type: ignore[arg-type]


def test_only_failed_devices_alert():
    snap = snapshot(devices=[device(1, "ok"), device(2, "off", "offline"), device(3, "planned", "planned"),
                             device(4, "broken", "failed", ip4="192.0.2.77/24"),
                             device(5, "orphan", "failed", site=None, ip4=None)],
                    vms=[vm(1, "vm-failed", "failed")])
    alerts = NetBoxIntegration(config={}).parse_alerts(snap)
    assert sorted((a.entity, a.severity, a.host) for a in alerts) == [
        ("device: Zurich/broken", "warning", "192.0.2.77"),
        ("device: orphan", "warning", ""),
    ]
    assert {a.title for a in alerts} == {"Device marked failed in NetBox"}


def test_healthy_inventory_has_no_alerts():
    snap = snapshot(devices=[device()], vms=[vm()], prefixes=[prefix()], sites=[site()])
    assert NetBoxIntegration(config={}).parse_alerts(snap) == []


# ── collect() through a mocked transport ──────────────────────────────────────


class FakeNetBox:
    def __init__(self):
        self.data: dict[str, list[dict]] = {}
        self.errors: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.status_body: dict | str = {"netbox-version": "4.1.5", "python-version": "3.12.3", "rq-workers-running": 1}
        self.status_code = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/status/":
            if isinstance(self.status_body, str):
                return httpx.Response(self.status_code, text=self.status_body)
            return httpx.Response(self.status_code, json=self.status_body)
        if path in self.errors:
            return httpx.Response(self.errors[path], json={"detail": "nope"})
        if path not in self.data:
            return httpx.Response(404, json={"detail": "Not found."})
        items = self.data[path]
        wanted = request.url.params.get("status")
        if wanted:
            items = [i for i in items if i["status"]["value"] == wanted]
        offset, limit = int(request.url.params.get("offset", 0)), int(request.url.params["limit"])
        page = items[offset:offset + limit]
        more = offset + limit < len(items)
        # Deliberately wrong host in `next`, as behind a reverse proxy: must not be followed.
        return httpx.Response(200, json={"count": len(items), "results": page,
                                         "next": "http://internal.invalid/api/next" if more else None,
                                         "previous": None})


def healthy() -> FakeNetBox:
    fake = FakeNetBox()
    fake.data = {
        "/api/dcim/sites/": [site()],
        "/api/dcim/devices/": [device(1), device(2, "sw-02", "failed")],
        "/api/virtualization/virtual-machines/": [vm()],
        "/api/ipam/prefixes/": [prefix()],
    }
    return fake


@pytest.fixture
def netbox(monkeypatch):
    fake = healthy()
    kwargs_seen: list[dict] = []

    def factory(**kwargs):
        kwargs_seen.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(nb, "safe_async_client", factory)
    fake.client_kwargs = kwargs_seen  # type: ignore[attr-defined]
    return fake


def plugin(**cfg) -> NetBoxIntegration:
    return NetBoxIntegration(config={"host": "https://netbox.example.com/", "api_token": "tok-s3cret", **cfg})


async def test_collect_healthy(netbox):
    result = await plugin().collect()
    assert result.success, result.error
    data = result.data
    assert data["version"] == "4.1.5"
    assert data["totals"] == {"sites": 1, "devices": 2, "vms": 1, "prefixes": 1}
    assert data["unavailable"] == [] and not any(data["truncated"].values())
    assert [d["name"] for d in data["failed_devices"]] == ["sw-02"]
    assert data["source_url"] == "https://netbox.example.com"
    assert {r.method for r in netbox.requests} == {"GET"}
    assert all(r.headers["authorization"] == "Token tok-s3cret" for r in netbox.requests)
    assert all("tok-s3cret" not in str(r.url) for r in netbox.requests)
    assert len(NetBoxIntegration(config={}).parse_alerts(data)) == 1


async def test_v2_tokens_use_bearer(netbox):
    await plugin(api_token="nbt_abc.def").collect()
    assert netbox.requests[0].headers["authorization"] == "Bearer nbt_abc.def"


async def test_core_failure_is_a_failed_collect(netbox):
    netbox.status_code = 500
    netbox.status_body = {"detail": "boom"}
    result = await plugin().collect()
    assert result.success is False and "HTTP 500" in result.error and "tok-s3cret" not in result.error


async def test_invalid_token_is_a_failed_collect(netbox):
    netbox.status_code = 403
    result = await plugin().collect()
    assert result.success is False and "403" in result.error and "tok-s3cret" not in result.error


async def test_connection_error_is_a_failed_collect(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused https://netbox.example.com/?token=tok-s3cret")

    monkeypatch.setattr(nb, "safe_async_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    result = await plugin().collect()
    assert result.success is False and "ConnectError" in result.error and "tok-s3cret" not in result.error


async def test_missing_config_makes_no_request(netbox):
    assert (await plugin(host="").collect()).success is False
    assert (await plugin(api_token="").collect()).success is False
    assert netbox.requests == []


async def test_denied_resource_is_unavailable_with_the_permission(netbox):
    netbox.errors["/api/dcim/devices/"] = 403
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == ["devices (token lacks permission: dcim.view_device)"]
    assert result.data["devices"] is None and result.data["totals"]["devices"] is None
    assert result.data["failed_devices"] is None
    assert result.data["totals"]["vms"] == 1


async def test_server_error_on_a_resource_is_unavailable(netbox):
    netbox.errors["/api/ipam/prefixes/"] = 502
    result = await plugin().collect()
    assert result.success and result.data["unavailable"] == ["prefixes (HTTP 502)"]
    assert result.data["prefixes"] is None


async def test_nothing_readable_is_a_failure(netbox):
    for path in ("/api/dcim/sites/", "/api/dcim/devices/", "/api/virtualization/virtual-machines/", "/api/ipam/prefixes/"):
        netbox.errors[path] = 403
    result = await plugin().collect()
    assert result.success is False and "No resource could be read" in result.error


async def test_unavailable_list_is_sorted(netbox):
    netbox.errors["/api/virtualization/virtual-machines/"] = 403
    netbox.errors["/api/dcim/sites/"] = 403
    result = await plugin().collect()
    assert result.data["unavailable"] == sorted(result.data["unavailable"])
    assert len(result.data["unavailable"]) == 2


async def test_pagination_uses_offset_and_ignores_next(netbox, monkeypatch):
    monkeypatch.setattr(nb, "PAGE_LIMIT", 2)
    netbox.data["/api/dcim/devices/"] = [device(i, f"sw-{i:02d}") for i in range(1, 6)]
    result = await plugin().collect()
    assert result.success
    assert len(result.data["devices"]) == 5 and result.data["truncated"]["devices"] is False
    offsets = [r.url.params["offset"] for r in netbox.requests if r.url.path == "/api/dcim/devices/"]
    assert offsets == ["0", "2", "4"]
    assert all(r.url.host == "netbox.example.com" for r in netbox.requests)


async def test_cap_truncates_flags_it_and_still_finds_failed_devices(netbox, monkeypatch):
    monkeypatch.setattr(nb, "PAGE_LIMIT", 2)
    monkeypatch.setitem(nb.MAX_ITEMS, "devices", 4)
    devices = [device(i, f"sw-{i:02d}") for i in range(1, 9)]
    devices[7] = device(8, "far-away", "failed")      # beyond the cap
    netbox.data["/api/dcim/devices/"] = devices
    result = await plugin().collect()
    assert result.success
    assert len(result.data["devices"]) == 4
    assert result.data["truncated"]["devices"] is True
    assert result.data["totals"]["devices"] == 8      # the API's count, not the cut list's length
    assert [d["name"] for d in result.data["failed_devices"]] == ["far-away"]
    alerts = NetBoxIntegration(config={}).parse_alerts(result.data)
    assert [a.entity for a in alerts] == ["device: Zurich/far-away"]


async def test_exactly_the_cap_is_not_truncated(netbox, monkeypatch):
    monkeypatch.setattr(nb, "PAGE_LIMIT", 2)
    monkeypatch.setitem(nb.MAX_ITEMS, "devices", 4)
    netbox.data["/api/dcim/devices/"] = [device(i, f"sw-{i:02d}") for i in range(1, 5)]
    result = await plugin().collect()
    assert len(result.data["devices"]) == 4 and result.data["truncated"]["devices"] is False


async def test_failed_lookup_after_a_cut_list_is_reported(netbox, monkeypatch):
    monkeypatch.setattr(nb, "PAGE_LIMIT", 2)
    monkeypatch.setitem(nb.MAX_ITEMS, "devices", 2)
    netbox.data["/api/dcim/devices/"] = [device(i, f"sw-{i:02d}") for i in range(1, 6)]
    original = netbox.handler

    def flaky(request):
        if request.url.params.get("status") == "failed":
            return httpx.Response(500, json={})
        return original(request)

    monkeypatch.setattr(nb, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    result = await plugin().collect()
    assert result.success
    assert result.data["failed_devices"] is None
    assert result.data["unavailable"] == ["failed devices (HTTP 500)"]


async def test_health_check(netbox):
    assert await plugin().health_check() is True
    netbox.status_code = 403
    assert await plugin().health_check() is False


# ── Host import ───────────────────────────────────────────────────────────────


def inventory():
    return snapshot(
        devices=[device(1, "sw-01", ip4="192.0.2.10/24"),
                 device(2, "fw-01", ip4="192.0.2.1/24"),
                 device(3, "offline-sw", "offline", ip4="192.0.2.11/24"),
                 device(4, "planned-sw", "planned", ip4="192.0.2.12/24"),
                 device(5, "no-ip", ip4=None),
                 device(6, "v6-only", ip4=None, ip6="2001:db8::6/64")],
        vms=[vm(1, "vm-01", ip4="192.0.2.50/24"),
             vm(2, "vm-failed", "failed", ip4="192.0.2.51/24"),
             vm(3, "vm-no-ip", ip4=None)],
    )


def test_importable_entries_are_active_with_an_address():
    entries, skipped = importable_entries(inventory())
    assert [e["name"] for e in entries] == ["sw-01", "fw-01", "v6-only", "vm-01"]
    assert skipped == 5
    assert importable_entries({})[0] == [] and importable_entries({"devices": "x"})[0] == []


def test_import_is_opt_in():
    field = next(f for f in NetBoxIntegration.config_fields if f.key == "import_hosts")
    assert field.field_type == "checkbox" and field.default is False and field.required is False
    token = next(f for f in NetBoxIntegration.config_fields if f.key == "api_token")
    assert token.encrypted and token.field_type == "password"


async def _hosts(db):
    from models.ping import PingHost

    return list((await db.execute(select(PingHost).order_by(PingHost.hostname))).scalars())


async def test_on_snapshot_does_nothing_by_default(db):
    inst = NetBoxIntegration(config={})
    for cfg in ({}, {"import_hosts": False}, {"import_hosts": "false"}, {"import_hosts": ""}, {"import_hosts": None}):
        await inst.on_snapshot(inventory(), cfg, db)
    assert await _hosts(db) == []


async def test_on_snapshot_imports_once_and_never_duplicates(db):
    inst = NetBoxIntegration(config={})
    cfg = {"import_hosts": True, "host": "https://netbox.example.com"}

    await inst.on_snapshot(inventory(), cfg, db)
    first = {h.hostname: (h.name, h.ip_address, h.source, h.source_detail, h.check_type, h.enabled)
             for h in await _hosts(db)}
    assert first == {
        "192.0.2.1": ("fw-01", "192.0.2.1", "netbox", "https://netbox.example.com", "icmp", True),
        "192.0.2.10": ("sw-01", "192.0.2.10", "netbox", "https://netbox.example.com", "icmp", True),
        "192.0.2.50": ("vm-01", "192.0.2.50", "netbox", "https://netbox.example.com", "icmp", True),
        "2001:db8::6": ("v6-only", "2001:db8::6", "netbox", "https://netbox.example.com", "icmp", True),
    }

    await inst.on_snapshot(inventory(), cfg, db)
    await inst.on_snapshot(inventory(), cfg, db)
    assert len(await _hosts(db)) == 4


async def test_import_report_counts(db):
    out = await apply_netbox_hosts(db, inventory(), "https://netbox.example.com")
    assert out == {"added": 4, "existing": 0, "skipped": 5, "errors": []}
    again = await apply_netbox_hosts(db, inventory(), "https://netbox.example.com")
    assert again == {"added": 0, "existing": 4, "skipped": 5, "errors": []}


async def test_import_never_touches_existing_hosts(db):
    from models.ping import PingHost

    db.add_all([
        # Same address, hand-made: must keep its name, source and settings.
        PingHost(name="Firewall (manual)", hostname="192.0.2.1", check_type="tcp", port=443,
                 source="manual", enabled=False),
        # Same name as a device, different hostname (e.g. imported from Proxmox).
        PingHost(name="vm-01", hostname="vm-01", ip_address=None, source="proxmox", source_detail="pve:101"),
        # Known by ip_address only.
        PingHost(name="switch", hostname="sw-01.example.com", ip_address="192.0.2.10", source="unifi"),
        # Unrelated host: must survive.
        PingHost(name="unrelated", hostname="198.51.100.9", source="manual"),
    ])
    await db.commit()

    out = await apply_netbox_hosts(db, inventory(), "https://netbox.example.com")

    assert out["added"] == 1 and out["existing"] == 3          # only v6-only is new
    hosts = {h.hostname: h for h in await _hosts(db)}
    assert set(hosts) == {"192.0.2.1", "vm-01", "sw-01.example.com", "198.51.100.9", "2001:db8::6"}
    fw = hosts["192.0.2.1"]
    assert (fw.name, fw.source, fw.check_type, fw.port, fw.enabled, fw.source_detail) == (
        "Firewall (manual)", "manual", "tcp", 443, False, None)
    assert hosts["vm-01"].source == "proxmox" and hosts["vm-01"].source_detail == "pve:101"
    assert hosts["sw-01.example.com"].source == "unifi"
    assert hosts["198.51.100.9"].name == "unrelated"


async def test_a_host_that_left_netbox_is_not_deleted(db):
    inst = NetBoxIntegration(config={})
    cfg = {"import_hosts": True}
    await inst.on_snapshot(inventory(), cfg, db)
    shrunk = snapshot(devices=[device(1, "sw-01", ip4="192.0.2.10/24")])
    await inst.on_snapshot(shrunk, cfg, db)
    assert len(await _hosts(db)) == 4


async def test_import_ignores_a_snapshot_without_inventory(db):
    for data in ({}, {"devices": None, "vms": None}, {"devices": [{}], "vms": [None]}):
        out = await apply_netbox_hosts(db, data, "x")
        assert out["added"] == 0
    assert await _hosts(db) == []


async def test_names_are_truncated_to_the_column_convention(db):
    long_name = "n" * 200
    data = snapshot(devices=[device(1, long_name, ip4="192.0.2.99/24")])
    await apply_netbox_hosts(db, data, "x")
    [host] = await _hosts(db)
    assert len(host.name) == 128 and host.hostname == "192.0.2.99"


async def test_import_matches_existing_hosts_by_name_and_fqdn_label(db):
    from models.ping import PingHost

    db.add_all([
        PingHost(name="fw-01", hostname="203.0.113.1", source="manual"),
        PingHost(name="Switch", hostname="sw-01.example.com", source="manual"),
    ])
    await db.commit()
    out = await apply_netbox_hosts(db, inventory(), "x")
    assert out["added"] == 2 and out["existing"] == 2      # v6-only and vm-01 are new
    assert {h.hostname for h in await _hosts(db)} == {
        "203.0.113.1", "sw-01.example.com", "2001:db8::6", "192.0.2.50"}


async def test_import_through_the_real_ingest_path(db):
    """The hook as the scheduler runs it: snapshot stored, hosts added for the
    config's own tenant, a second run adds nothing, nothing else is touched."""
    from integrations._base import CollectorResult
    from models.integration import Snapshot
    from models.ping import PingHost
    from services import integration as int_svc
    from services import integration_ingest as ingest_mod

    db.add(PingHost(name="keep", hostname="192.0.2.1", source="manual", check_type="tcp", port=22))
    await db.commit()
    config = {"host": "https://netbox.example.com", "api_token": "t", "verify_ssl": True,
              "import_hosts": True}
    cfg = await int_svc.create_config(db, "netbox", "nb", config)
    await db.commit()

    for _ in range(2):
        async with ingest_mod.snapshot_write_lock():
            out = await ingest_mod.ingest(
                db, integration_type="netbox", cfg_id=cfg.id, stamp=ingest_mod.CollectStamp.of(cfg),
                result=CollectorResult(success=True, data=inventory()),
                integration_cls=NetBoxIntegration, config=config)
            await db.commit()
        assert out == ingest_mod.Outcome.STORED

    hosts = await _hosts(db)
    assert sorted(h.hostname for h in hosts) == ["192.0.2.1", "192.0.2.10", "192.0.2.50", "2001:db8::6"]
    assert {h.tenant_id for h in hosts} == {cfg.tenant_id}
    keep = next(h for h in hosts if h.hostname == "192.0.2.1")
    assert (keep.name, keep.source, keep.check_type, keep.port) == ("keep", "manual", "tcp", 22)
    snaps = (await db.execute(select(Snapshot).where(Snapshot.entity_type == "netbox"))).scalars().all()
    assert len(snaps) == 2 and all(s.ok for s in snaps)

    # Import off: the same snapshot adds nothing.
    await db.delete(next(h for h in hosts if h.hostname == "192.0.2.50"))
    await db.commit()
    async with ingest_mod.snapshot_write_lock():
        await ingest_mod.ingest(
            db, integration_type="netbox", cfg_id=cfg.id, stamp=ingest_mod.CollectStamp.of(cfg),
            result=CollectorResult(success=True, data=inventory()),
            integration_cls=NetBoxIntegration, config={**config, "import_hosts": False})
        await db.commit()
    assert "192.0.2.50" not in {h.hostname for h in await _hosts(db)}


def test_unnamed_failed_device_still_alerts():
    data = {"failed_devices": [{"id": 42, "name": "", "status": "failed", "site": "", "ip": ""}]}
    [a] = NetBoxIntegration(config={}).parse_alerts(data)
    assert a.entity == "device: #42"

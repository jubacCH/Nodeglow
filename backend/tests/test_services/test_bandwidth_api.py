"""Traffic page data: the shapes and timestamps the page depends on."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from services import bandwidth as bw
from services import clickhouse_client as ch


def _sample(**fields):
    base = dict(timestamp=datetime.utcnow() - timedelta(seconds=30), source_type="unifi",
                source_id="aa", source_name="switch", interface_name="Port 1",
                rx_bytes=1, tx_bytes=1, rx_rate_bps=1000, tx_rate_bps=500)
    return {**base, **fields}


async def test_interface_timestamps_are_utc_with_z():
    """Without the Z a browser reads the time as local: every interface then
    looked an hour or two old and the page said "0 reporting"."""
    with patch.object(ch, "get_latest_bandwidth_per_iface", new=AsyncMock(return_value=[_sample()])):
        interfaces = await bw.get_bandwidth_interfaces()
        summary = await bw.get_bandwidth_summary()
    assert interfaces[0]["last_seen"].endswith("Z")
    assert summary["top_talkers"][0]["timestamp"].endswith("Z")


def _bucket(hour, **sums):
    row = {"timestamp": datetime(2026, 11, 12, hour, 0)}
    for basis in bw.TOTAL_BASES:
        rx, tx, n = sums.get(basis, (0, 0, 0))
        row.update({f"{basis}_rx": rx, f"{basis}_tx": tx, f"{basis}_n": n})
    return row


async def test_total_history_is_one_point_per_bucket_on_one_basis():
    """The Internet link when a gateway reports it; never that plus the
    guests behind it."""
    rows = [_bucket(20, wan=(3000, 900, 1), guests=(9000, 9000, 40)),
            _bucket(21, guests=(8000, 8000, 40))]
    with patch.object(ch, "get_bandwidth_total_history_ch", new=AsyncMock(return_value=rows)) as q:
        total = await bw.get_bandwidth_total_history(hours=6)
    assert q.await_args.kwargs == {"hours": 6}
    assert total == {"basis": "wan", "samples": [
        {"timestamp": "2026-11-12T20:00:00Z", "rx_rate_bps": 3000, "tx_rate_bps": 900, "interfaces": 1}]}


async def test_total_history_falls_back_to_guests_then_agents_then_nothing():
    async def total(rows):
        with patch.object(ch, "get_bandwidth_total_history_ch", new=AsyncMock(return_value=rows)):
            return await bw.get_bandwidth_total_history()

    both = await total([_bucket(20, guests=(700, 70, 4), agents=(500, 50, 2))])
    assert both["basis"] == "guests" and both["samples"][0]["rx_rate_bps"] == 700
    assert (await total([_bucket(20, agents=(500, 50, 2))]))["basis"] == "agents"
    # Only switch ports report: there is nothing that may be added up.
    assert await total([_bucket(20)]) == {"basis": None, "samples": []}


def test_which_interfaces_a_total_is_built_from():
    assert bw.total_basis_of("unifi", "wan") == "wan"
    assert bw.total_basis_of("proxmox", "lxc/104-paperless") == "guests"
    assert bw.total_basis_of("proxmox", "qemu/100-home") == "guests"
    assert bw.total_basis_of("agent", "total") == "agents"
    for never in (("unifi", "device/SW16/Port 4"), ("unifi", "device/SW16"), ("agent", "eth0"),
                  ("proxmox", "node/pve1"), ("unifi", "total")):
        assert bw.total_basis_of(*never) is None


def test_the_sql_and_the_python_name_the_same_bases():
    assert tuple(ch.BANDWIDTH_TOTAL_BASES) == bw.TOTAL_BASES
    assert f"interface_name = '{bw.WAN}'" in ch.BANDWIDTH_TOTAL_BASES["wan"]
    assert f"interface_name = '{bw.AGENT_TOTAL}'" in ch.BANDWIDTH_TOTAL_BASES["agents"]


async def test_the_summary_total_is_the_internet_link_not_every_port():
    """A 2 Gbit/s backup crossed five switch ports and the page showed a
    total of 5 Gbit/s."""
    latest = [
        _sample(interface_name="wan", rx_rate_bps=40_000_000, tx_rate_bps=8_000_000),
        _sample(interface_name="device/SWFIBER/SFP+ 2", rx_rate_bps=2_000_000_000, tx_rate_bps=50_000_000),
        _sample(interface_name="device/SW16/SFP+ 1", rx_rate_bps=2_000_000_000, tx_rate_bps=50_000_000),
        _sample(interface_name="device/SW16", rx_rate_bps=2_000_000_000, tx_rate_bps=50_000_000),
        _sample(source_type="proxmox", interface_name="lxc/104-paperless", rx_rate_bps=9, tx_rate_bps=9),
    ]
    with patch.object(ch, "get_latest_bandwidth_per_iface", new=AsyncMock(return_value=latest)):
        summary = await bw.get_bandwidth_summary()
    assert (summary["total_basis"], summary["total_rx_bps"], summary["total_tx_bps"]) == ("wan", 40_000_000, 8_000_000)
    assert summary["total_interfaces"] == 5, "every interface is still listed"
    assert summary["top_talkers"][0]["interface_name"].startswith("SW")

    with patch.object(ch, "get_latest_bandwidth_per_iface", new=AsyncMock(return_value=latest[1:4])):
        ports_only = await bw.get_bandwidth_summary()
    assert (ports_only["total_basis"], ports_only["total_rx_bps"]) == (None, 0)


async def _written(extract, *args, **kwargs):
    with patch.object(ch, "insert_bandwidth_metrics", new=AsyncMock()) as insert, \
         patch.object(ch, "get_previous_bandwidth_sample", new=AsyncMock(return_value=None)):
        count = await extract(*args, tenant_id=1, **kwargs)
    rows = insert.await_args.args[0] if insert.await_args else []
    assert count == len(rows)
    return {r["interface_name"]: r for r in rows}


async def test_the_agent_format_is_read():
    """The agent sends its interfaces at the top level. Only the nested form
    of its predecessor was read: no agent's traffic was recorded."""
    body = {"rx_bytes": 3000, "tx_bytes": 600,
            "network_interfaces": [{"name": "eth0", "rx_bytes": 2500, "tx_bytes": 500},
                                   {"name": "docker0", "rx_bytes": 500, "tx_bytes": 100}]}
    rows = await _written(bw.extract_agent_bandwidth, 7, body, source_name="web")
    assert set(rows) == {"eth0", "docker0", "total"}
    assert rows["total"]["rx_bytes"] == 2500 and rows["total"]["source_type"] == "agent"

    legacy = {"network": {"rx_bytes": 9, "tx_bytes": 1, "interfaces": [{"name": "eth0", "rx_bytes": 9, "tx_bytes": 1}]}}
    assert set(await _written(bw.extract_agent_bandwidth, 7, legacy)) == {"eth0", "total"}
    assert await _written(bw.extract_agent_bandwidth, 7, {"cpu_pct": 1}) == {}


async def test_proxmox_guests_are_recorded_and_empty_nodes_are_not():
    """Guests are under ``vms`` / ``containers``; ``qemu`` / ``lxc`` was
    read, and the only rows ever written were nodes at zero."""
    data = {"nodes": [{"name": "pve1", "netin": 0, "netout": 0}],
            "vms": [{"id": 100, "name": "home", "netin": 5000, "netout": 700}],
            "containers": [{"id": 104, "name": "paperless", "netin": 900, "netout": 90}]}
    rows = await _written(bw.extract_proxmox_bandwidth, 1, data)
    assert set(rows) == {"qemu/100-home", "lxc/104-paperless"}
    assert rows["lxc/104-paperless"]["rx_bytes"] == 900


async def test_unifi_writes_the_internet_link():
    data = {"wan": {"status": "ok", "rx_bytes_r": 1000, "tx_bytes_r": 250},
            "devices": [{"name": "SW16", "rx_bytes_r": 10, "tx_bytes_r": 10,
                         "port_table": [{"name": "Port 4", "rx_bytes_r": 5, "tx_bytes_r": 5}]}]}
    rows = await _written(bw.extract_unifi_bandwidth, 3, data)
    assert set(rows) == {"wan", "device/SW16", "device/SW16/Port 4"}
    assert (rows["wan"]["rx_rate_bps"], rows["wan"]["tx_rate_bps"]) == (8000, 2000)
    # No WAN health in the snapshot (no gateway): no row, not a row of zeros.
    assert "wan" not in await _written(bw.extract_unifi_bandwidth, 3, {"wan": {}, "devices": data["devices"]})


def test_bucket_width_follows_the_range():
    assert [ch.bandwidth_bucket_seconds(h) for h in (1, 6, 24, 168)] == [60, 300, 900, 3600]


async def test_total_history_query_is_tenant_scoped_and_bucketed():
    captured = {}

    async def fake_query(sql, params):
        captured["sql"], captured["params"] = sql, params
        return []

    with patch.object(ch, "query", new=fake_query), \
         patch.object(ch, "tenant_clause", new=lambda params: "tenant_id = 1"):
        await ch.get_bandwidth_total_history_ch(hours=24)
    assert "tenant_id = 1" in captured["sql"]
    assert "toStartOfInterval" in captured["sql"]
    for basis in bw.TOTAL_BASES:
        assert f"AS {basis}_rx" in captured["sql"] and f"AS {basis}_n" in captured["sql"]
    assert captured["params"]["b"] == 900 and captured["params"]["h"] == 24


async def test_an_agent_total_counts_the_network_cards_only():
    """The agent adds up every interface but loopback. On a Docker host that
    is the card, the bridge and each container's end of it: one byte three
    times. Seen on a media server: 1.5 Mbit/s total with 0.04 on eth0."""
    body = {"rx_bytes": 9000, "tx_bytes": 900, "network_interfaces": [
        {"name": "lo", "rx_bytes": 50, "tx_bytes": 50},
        {"name": "eth0", "rx_bytes": 1000, "tx_bytes": 100},
        {"name": "eth1", "rx_bytes": 200, "tx_bytes": 20},
        {"name": "docker0", "rx_bytes": 0, "tx_bytes": 0},
        {"name": "br-a46a998b0f34", "rx_bytes": 3900, "tx_bytes": 390},
        {"name": "veth5d78eef", "rx_bytes": 3900, "tx_bytes": 390},
    ]}
    rows = await _written(bw.extract_agent_bandwidth, 7, body, source_name="media")
    assert (rows["total"]["rx_bytes"], rows["total"]["tx_bytes"]) == (1200, 120)
    # Every interface is still listed.
    assert set(rows) == {"lo", "eth0", "eth1", "docker0", "br-a46a998b0f34", "veth5d78eef", "total"}


@pytest.mark.parametrize("name,own", [
    ("eth0", True), ("ens18", True), ("enp3s0", True), ("eno1", True), ("wlan0", True),
    ("Ethernet", True), ("Ethernet 2", True), ("Wi-Fi", True), ("WLAN", True), ("en0", True),
    ("lo", False), ("docker0", False), ("br-5f380e66f205", False), ("veth0d56c47", False),
    ("vmbr0", False), ("bond0", False), ("tap100i0", False), ("fwbr101i0", False),
    ("wg0", False), ("tailscale0", False), ("eth0.30", False), ("virbr0", False),
    ("vEthernet (WSL)", False), ("Loopback Pseudo-Interface 1", False),
    ("Ethernet-WFP Native MAC Layer LightWeight Filter-0000", False),
    ("Ethernet-QoS Packet Scheduler-0000", False), ("total", False), ("", False),
])
def test_which_interfaces_are_a_hosts_own_cards(name, own):
    assert bw.carries_own_traffic(name) is own


async def test_an_agent_without_a_recognisable_card_keeps_its_own_total():
    body = {"rx_bytes": 700, "tx_bytes": 70, "network_interfaces": [
        {"name": "lo", "rx_bytes": 50, "tx_bytes": 50},
        {"name": "wg0", "rx_bytes": 700, "tx_bytes": 70},
    ]}
    rows = await _written(bw.extract_agent_bandwidth, 7, body)
    assert (rows["total"]["rx_bytes"], rows["total"]["tx_bytes"]) == (700, 70)

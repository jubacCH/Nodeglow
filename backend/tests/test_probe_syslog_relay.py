"""Probe syslog relay (multi-tenancy phase 10): site-scoped host resolution,
the relayed-entry parser and the direct listener's modes.

Two tenants, and two sites inside tenant 1 (head office and a branch) that
both use 192.168.1.0/24 — the case the spec's "Syslog ingestion" is about.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from services import syslog
from services.tenancy import system_scope, tenant_scope

HQ, BRANCH, B_SITE = 1, 3, 2


@pytest.fixture
async def hosts(db):
    """Hosts of two tenants and two sites; returns {name: id}."""
    from models.ping import PingHost
    from models.tenant import Site, Tenant

    with system_scope("test: second tenant, branch site"):
        db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
        await db.flush()
        db.add(Site(id=B_SITE, tenant_id=2, name="B"))
        db.add(Site(id=BRANCH, tenant_id=1, name="Branch"))
        await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    rows = {
        1: [("gw-hq", "192.168.1.1", HQ), ("gw-branch", "192.168.1.1", BRANCH),
            ("only-branch", "10.5.5.5", BRANCH), ("fw", "9.9.9.9", HQ),
            ("core", "core.hq.example", HQ), ("core", "core.branch.example", BRANCH)],
        2: [("gw-b", "192.168.1.1", B_SITE), ("srv-b", "10.5.5.5", B_SITE)],
    }
    async with maker() as s:
        for tenant_id, specs in rows.items():
            with tenant_scope(tenant_id):
                for name, hostname, site in specs:
                    s.add(PingHost(name=name, hostname=hostname, site_id=site))
                await s.commit()
        with system_scope("test: read back"):
            found = (await s.execute(select(PingHost))).scalars().all()
    ids = {(h.tenant_id, h.name, h.site_id): h.id for h in found}

    @asynccontextmanager
    async def factory():
        async with maker() as s:
            yield s

    with patch.object(syslog, "AsyncSessionLocal", factory), \
         patch.object(syslog, "_resolve_many", new=AsyncMock(return_value={})), \
         patch("services.tenancy.SingleTenancy.active_tenant_ids",
               new=AsyncMock(return_value=[1, 2])):
        await syslog._refresh_host_cache()
    return ids


# ── site-scoped resolution ──────────────────────────────────────────────────

async def test_the_same_private_address_resolves_per_site(hosts):
    r = syslog._resolve_host_id
    assert r("192.168.1.1", None, 1, HQ) == hosts[(1, "gw-hq", HQ)]
    assert r("192.168.1.1", None, 1, BRANCH) == hosts[(1, "gw-branch", BRANCH)]
    assert r("192.168.1.1", None, 2, B_SITE) == hosts[(2, "gw-b", B_SITE)]


async def test_a_private_address_never_resolves_outside_its_site(hosts):
    r = syslog._resolve_host_id
    # 10.5.5.5 exists only in the branch (and in tenant B): from head office
    # it is an unknown device, not the branch's host.
    assert r("10.5.5.5", None, 1, HQ) is None
    assert r("10.5.5.5", None, 1, BRANCH) == hosts[(1, "only-branch", BRANCH)]
    assert r("10.5.5.5", None, 2, B_SITE) == hosts[(2, "srv-b", B_SITE)]
    # A site the tenant has no hosts in: nothing private resolves.
    assert r("192.168.1.1", None, 1, 99) is None


async def test_names_and_public_addresses_fall_back_to_the_tenant(hosts):
    r = syslog._resolve_host_id
    assert r("172.16.0.9", "only-branch", 1, HQ) == hosts[(1, "only-branch", BRANCH)]
    assert r("9.9.9.9", None, 1, BRANCH) == hosts[(1, "fw", HQ)]
    # A name two sites use is never guessed from a third.
    assert r("172.16.0.9", "core", 1, 99) is None
    assert r("172.16.0.9", "core", 1, HQ) == hosts[(1, "core", HQ)]
    assert r("172.16.0.9", "core", 1, BRANCH) == hosts[(1, "core", BRANCH)]


async def test_another_tenants_hosts_are_never_candidates(hosts):
    r = syslog._resolve_host_id
    assert r("10.9.9.9", "gw-b", 1, HQ) is None
    assert r("10.9.9.9", "srv-b", 1, BRANCH) is None
    assert r("10.9.9.9", "fw", 2, B_SITE) is None
    assert r("9.9.9.9", None, 2, B_SITE) is None


async def test_the_listener_keeps_resolving_tenant_wide(hosts):
    # No site: the direct listener's lookup, as before phase 10.
    assert syslog._resolve_host_id("10.5.5.5", None, 1) == hosts[(1, "only-branch", BRANCH)]
    assert syslog._resolve_host_id("172.16.0.9", "fw") == hosts[(1, "fw", HQ)]


async def test_relayed_lookups_skip_reverse_dns(hosts):
    syslog._rdns_cache["10.77.0.1"] = "gw-hq.example"
    try:
        assert syslog._resolve_host_id("10.77.0.1", None, 1, HQ, rdns=False) is None
        assert syslog._resolve_host_id("10.77.0.1", None, 1, HQ) == hosts[(1, "gw-hq", HQ)]
    finally:
        syslog._rdns_cache.pop("10.77.0.1", None)


async def test_an_agents_own_host_falls_back_to_a_private_request_address(hosts):
    r = syslog.resolve_agent_host_id
    # By name first, inside the tenant and site.
    assert r("203.0.113.7", "gw-hq", 1, HQ) == hosts[(1, "gw-hq", HQ)]
    # A host keyed by its (private) address, the agent reporting another
    # name: the request's address finds it — in the agent's own site only.
    assert r("10.5.5.5", "only-branch-agent", 1, BRANCH) == hosts[(1, "only-branch", BRANCH)]
    assert r("10.5.5.5", "only-branch-agent", 1, HQ) is None
    assert r("192.168.1.1", "unknown-agent", 1, HQ) == hosts[(1, "gw-hq", HQ)]
    # Never another tenant's host.
    assert r("10.5.5.5", "x", 2, B_SITE) == hosts[(2, "srv-b", B_SITE)]
    assert r("192.168.1.99", "x", 2, B_SITE) is None
    # A public address of a multi-site tenant is a NAT more often than not.
    assert r("9.9.9.9", "unknown-agent", 1, HQ) is None
    # No reverse DNS for agents.
    syslog._rdns_cache["10.77.0.1"] = "gw-hq.example"
    try:
        assert r("10.77.0.1", "unknown-agent", 1, HQ) is None
    finally:
        syslog._rdns_cache.pop("10.77.0.1", None)


async def test_an_agents_public_address_counts_in_a_single_site_tenant(hosts):
    # Tenant 2 has hosts in one site only: a public request address is the
    # agent's own (a cloud server), as before phase 10.
    syslog._site_host_cache[(2, B_SITE)]["198.51.100.4"] = 4242
    try:
        assert syslog.resolve_agent_host_id("198.51.100.4", "vps", 2, B_SITE) == 4242
        assert syslog.resolve_agent_host_id(None, "vps", 2, B_SITE) is None
    finally:
        syslog._site_host_cache[(2, B_SITE)].pop("198.51.100.4", None)


async def test_allowlist_is_site_scoped_for_relayed_syslog(hosts):
    syslog._allowlist_only[1] = True
    assert not syslog._is_allowed_source("10.5.5.5", 1, HQ)
    assert syslog._is_allowed_source("10.5.5.5", 1, BRANCH)
    assert syslog._is_allowed_source("9.9.9.9", 1, BRANCH)  # public, known in HQ
    assert not syslog._is_allowed_source("192.168.7.7", 1, HQ)
    # The listener's tenant-wide check is unchanged.
    assert syslog._is_allowed_source("10.5.5.5", 1)
    # Tenant 2 does not enforce it.
    assert syslog._is_allowed_source("192.168.7.7", 2, B_SITE)


async def test_cache_counts_active_tenants_for_the_listener(hosts):
    assert syslog._active_tenant_count == 2


# ── relayed entries ─────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 11, 12, 0, 0)


async def test_relayed_entry_belongs_to_the_probe_whatever_it_claims(hosts):
    entry = {"raw": "<13>Oct 11 11:59:58 gw-b sshd[1]: Accepted CANARY",
             "source_ip": "192.168.1.1", "received_at": "2026-10-11T11:59:59Z",
             "tenant_id": 2, "site_id": B_SITE, "_tenant_id": 2, "host_id": 1}
    parsed, reason = syslog.parse_relayed_entry(entry, tenant_id=1, site_id=HQ, now=NOW)
    assert reason is None
    assert parsed[syslog.TENANT_KEY] == 1
    assert parsed["site_id"] == HQ
    assert parsed["source_ip"] == "192.168.1.1"
    # "gw-b" is tenant B's name; in A's head office the address decides.
    assert parsed["host_id"] == hosts[(1, "gw-hq", HQ)]
    assert parsed["received_at"] == datetime(2026, 10, 11, 11, 59, 59)
    assert parsed["severity"] == 5 and parsed["app_name"] == "sshd"
    assert parsed[syslog.RELAYED_KEY] is True

    row = syslog._clean_rows([parsed])[0]
    assert row["tenant_id"] == 1 and row["site_id"] == HQ
    assert syslog.TENANT_KEY not in row and syslog.RELAYED_KEY not in row


@pytest.mark.parametrize("entry, reason", [
    ({"raw": "<13>x", "source_ip": "not-an-ip"}, "relay_invalid"),
    ({"raw": "<13>x"}, "relay_invalid"),
    ({"raw": 42, "source_ip": "10.0.0.1"}, "relay_invalid"),
    ({"raw": "x" * (64 * 1024 + 1), "source_ip": "10.0.0.1"}, "relay_oversized"),
    ({"raw": "   ", "source_ip": "10.0.0.1"}, "relay_empty"),
])
def test_relayed_entry_rejects(entry, reason):
    assert syslog.parse_relayed_entry(entry, tenant_id=1, site_id=HQ, now=NOW) == (None, reason)


def test_relayed_entry_respects_the_allowlist():
    syslog._allowlist_only[1] = True
    syslog._site_allowlist_ips[(1, HQ)] = {"10.0.0.1"}
    ok, _ = syslog.parse_relayed_entry({"raw": "<13>hi", "source_ip": "10.0.0.1"},
                                       tenant_id=1, site_id=HQ, now=NOW)
    assert ok is not None
    assert syslog.parse_relayed_entry({"raw": "<13>hi", "source_ip": "10.0.0.2"},
                                      tenant_id=1, site_id=HQ, now=NOW) == (None, "relay_not_allowed")


@pytest.mark.parametrize("value, expected", [
    ("2026-10-11T11:00:00Z", datetime(2026, 10, 11, 11, 0, 0)),
    ("2026-10-11T13:00:00+02:00", datetime(2026, 10, 11, 11, 0, 0)),
    ("2026-10-11T11:00:00", datetime(2026, 10, 11, 11, 0, 0)),
    ("2026-10-11T12:10:00Z", NOW),          # ten minutes in the future
    ("2026-09-01T00:00:00Z", NOW),          # older than a week
    ("yesterday", NOW),
    (None, NOW),
    (1760000000, NOW),
])
def test_relay_receive_time_is_sanitised(value, expected):
    assert syslog._relay_received_at(value, NOW) == expected


# ── direct listener modes ───────────────────────────────────────────────────

@pytest.mark.parametrize("value, mode", [
    (None, "auto"), ("", "auto"), ("auto", "auto"), ("ON", "on"), ("1", "on"),
    ("off", "off"), ("false", "off"), ("bogus", "auto"),
])
def test_listener_mode_from_env(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv("NODEGLOW_SYSLOG_LISTENER", raising=False)
    else:
        monkeypatch.setenv("NODEGLOW_SYSLOG_LISTENER", value)
    assert syslog.listener_mode_from_env() == mode


def test_auto_listener_accepts_only_with_one_tenant(caplog):
    syslog._listener_mode = "auto"
    syslog._active_tenant_count = 1
    assert syslog._listener_accepts()
    syslog._active_tenant_count = 2
    syslog._listener_refused_logged = -1e9
    with caplog.at_level("WARNING", logger="nodeglow.syslog"):
        assert not syslog._listener_accepts()
        assert not syslog._listener_accepts()
    # One warning per interval, not one per datagram.
    assert sum("refuses messages" in r.getMessage() for r in caplog.records) == 1
    syslog._listener_mode = "on"
    assert syslog._listener_accepts()


def test_auto_listener_fails_closed_until_the_tenants_are_counted():
    syslog._listener_mode = "auto"
    assert syslog._active_tenant_count is None  # fresh process (reset)
    assert not syslog._listener_accepts()


async def test_a_failing_refresh_does_not_open_the_auto_listener(caplog):
    @asynccontextmanager
    async def broken():
        raise RuntimeError("database unreachable")
        yield  # pragma: no cover

    syslog._listener_mode = "auto"
    with patch.object(syslog, "AsyncSessionLocal", broken),          patch("services.tenancy.SingleTenancy.active_tenant_ids",
               new=AsyncMock(return_value=[1, 2, 3])):
        await syslog._refresh_host_cache()
    assert syslog._active_tenant_count is None
    assert not syslog._listener_accepts()


async def test_the_tenant_count_survives_a_failing_setting(db):
    @asynccontextmanager
    async def factory():
        yield db

    syslog._listener_mode = "auto"
    with patch.object(syslog, "AsyncSessionLocal", factory),          patch.object(syslog, "_resolve_many", new=AsyncMock(return_value={})),          patch("services.tenancy.SingleTenancy.active_tenant_ids",
               new=AsyncMock(return_value=[1, 2, 3])),          patch("database.get_setting", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await syslog._refresh_host_cache()
    assert syslog._active_tenant_count == 3
    assert not syslog._listener_accepts()


async def test_a_refusing_listener_drops_datagrams():
    syslog._listener_mode = "auto"
    syslog._active_tenant_count = 2
    queue = syslog._udp_queue = __import__("asyncio").Queue(maxsize=10)
    try:
        with patch.object(syslog, "_syslog_rate_ok", return_value=True), \
             patch.object(syslog, "_global_rate_ok", return_value=True):
            syslog.SyslogUDPProtocol().datagram_received(b"<14>hello", ("10.0.0.9", 514))
        assert queue.qsize() == 0
        syslog._active_tenant_count = 1
        with patch.object(syslog, "_syslog_rate_ok", return_value=True), \
             patch.object(syslog, "_global_rate_ok", return_value=True):
            syslog.SyslogUDPProtocol().datagram_received(b"<14>hello", ("10.0.0.9", 514))
        assert queue.qsize() == 1
        assert queue.get_nowait()[syslog.TENANT_KEY] == syslog.LISTENER_TENANT_ID
    finally:
        syslog._udp_queue = None


async def test_listener_off_binds_nothing_but_runs_the_pipeline():
    @asynccontextmanager
    async def no_session():
        yield None

    with patch.object(syslog, "_refresh_host_cache", new=AsyncMock()), \
         patch("services.log_intelligence.load_template_cache", new=AsyncMock()), \
         patch.object(syslog, "AsyncSessionLocal", no_session), \
         patch("asyncio.BaseEventLoop.create_datagram_endpoint") as udp, \
         patch("asyncio.start_server") as tcp:
        await syslog.start_syslog_server(udp_port=0, tcp_port=0, mode="off")
        try:
            assert syslog._listener_mode == "off"
            udp.assert_not_called()
            tcp.assert_not_called()
            assert syslog._udp_transport is None and syslog._tcp_server is None
            # Agent and relayed messages still get written.
            assert syslog._flush_task is not None and not syslog._flush_task.done()
        finally:
            await syslog.stop_syslog_server()


def test_received_at_window_is_generous_but_bounded():
    late = NOW - timedelta(days=6, hours=23)
    assert syslog._relay_received_at(late.isoformat(), NOW) == late

"""In-process state is per tenant (spec: "In-process state that must be keyed
by tenant"). One test per row of that table that holds state in this process:
two tenants with colliding keys (the same template, the same IP, the same
alert title) never see or reset each other's state.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from services.tenancy import system_scope, tenant_scope


@pytest.fixture
async def two_tenants(db):
    from models.tenant import Site, Tenant

    with system_scope("test: second tenant"):
        db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
        await db.flush()
        db.add(Site(id=2, tenant_id=2, name="B"))
        await db.commit()
    return async_sessionmaker(db.bind, expire_on_commit=False)


def _factory(session_maker):
    @asynccontextmanager
    async def factory():
        async with session_maker() as s:
            yield s
    return factory


# ── log intelligence: templates, counts, examples, bursts ───────────────────

async def test_same_template_is_learned_separately_per_tenant(two_tenants):
    from models.log_template import LogTemplate
    from services import log_intelligence as li

    li.reset_state()
    a = li.process_message("Link down on port 7", 4, tenant_id=1)
    b = li.process_message("Link down on port 9", 4, tenant_id=2)
    assert a["template_hash"] == b["template_hash"]
    assert a["is_new_template"] and b["is_new_template"]  # B's first sighting is B's
    li.process_message("Link down on port 3", 4, tenant_id=1)
    assert li._tenant_state(1).template_counts[a["template_hash"]] == 2
    assert li._tenant_state(2).template_counts[b["template_hash"]] == 1

    for tid in (1, 2):
        async with two_tenants() as s:
            with tenant_scope(tid):
                await li.flush_templates(s)
    with system_scope("test: read back"):
        async with two_tenants() as s:
            rows = (await s.execute(select(LogTemplate).order_by(LogTemplate.tenant_id))).scalars().all()
    assert [(r.tenant_id, r.count) for r in rows] == [(1, 2), (2, 1)]
    # Each tenant's example is its own log line.
    assert rows[0].example == "Link down on port 7" and rows[1].example == "Link down on port 9"
    assert li._tenant_state(1).template_cache[a["template_hash"]] == rows[0].id
    assert li._tenant_state(2).template_cache[b["template_hash"]] == rows[1].id
    li.reset_state()


def test_bursts_are_per_tenant():
    from services import log_intelligence as li

    li.reset_state()
    for i in range(li._BURST_THRESHOLD):
        li.process_message(f"flap {i}", 6, tenant_id=1)
    assert li.get_active_bursts(tenant_id=1)
    assert li.get_active_bursts(tenant_id=2) == []
    with tenant_scope(2):
        assert li.get_active_bursts() == []
    li.reset_state()


def test_process_message_needs_a_tenant():
    from services import log_intelligence as li
    from services.tenancy import TenantContextError

    with pytest.raises(TenantContextError):
        li.process_message("no tenant")


# ── syslog: host attribution and the live tail ──────────────────────────────

async def test_syslog_host_cache_resolves_inside_the_tenant(two_tenants):
    from models.ping import PingHost
    from services import syslog

    async with two_tenants() as s:
        with tenant_scope(1):
            s.add(PingHost(name="gw-a", hostname="192.168.1.1"))
            await s.commit()
        with tenant_scope(2):
            s.add(PingHost(name="gw-b", hostname="192.168.1.1"))
            await s.commit()
        with system_scope("test"):
            ids = {h.tenant_id: h.id for h in (await s.execute(select(PingHost))).scalars()}
    from tests.conftest import put_settings
    async with two_tenants() as s:
        await put_settings(s, tenant_id=2, syslog_allowlist_only="1")

    syslog._host_cache_ts = 0.0
    with patch.object(syslog, "AsyncSessionLocal", _factory(two_tenants)), \
         patch.object(syslog, "_resolve_many", new=AsyncMock(return_value={})), \
         patch("services.tenancy.SingleTenancy.active_tenant_ids", new=AsyncMock(return_value=[1, 2])):
        await syslog._refresh_host_cache()
    try:
        assert syslog._resolve_host_id("192.168.1.1", None, 1) == ids[1]
        assert syslog._resolve_host_id("192.168.1.1", None, 2) == ids[2]
        assert syslog._resolve_host_id("10.7.7.7", "gw-b", 1) is None  # B's name, A's network
        assert syslog._resolve_host_id("10.7.7.7", "gw-b", 2) == ids[2]
        # The direct listener feeds the default tenant only.
        assert syslog._resolve_host_id("192.168.1.1", None) == ids[1]
        # Allowlist per tenant: B enforces it, A does not.
        assert syslog._is_allowed_source("10.9.9.9", 1)
        assert not syslog._is_allowed_source("10.9.9.9", 2)
        assert syslog._is_allowed_source("192.168.1.1", 2)
    finally:
        syslog._host_cache_ts = 0.0
        syslog._host_cache = {}


async def test_live_tail_only_feeds_the_messages_tenant():
    from services import syslog

    q1, q2 = syslog.subscribe(1), syslog.subscribe(2)
    saved = syslog._buffer
    syslog._buffer = []
    try:
        await syslog._enqueue({"message": "CANARY-B-syslog", "severity": 3,
                               "source_ip": "10.0.0.1", syslog.TENANT_KEY: 2})
        await syslog._enqueue({"message": "for A", "severity": 3,
                               "source_ip": "10.0.0.1", syslog.TENANT_KEY: 1})
        assert q2.get_nowait()["message"] == "CANARY-B-syslog"
        assert q2.empty()
        assert q1.get_nowait()["message"] == "for A"
        assert q1.empty()
        # A message nobody attributed to a tenant goes nowhere.
        await syslog._enqueue({"message": "orphan", "severity": 3, "source_ip": "10.0.0.1"})
        assert q1.empty() and q2.empty()
        assert all(m["message"] != "orphan" for m in syslog._buffer)
        # Nothing tenant-internal reaches the ClickHouse row.
        assert all(syslog.TENANT_KEY not in r for r in syslog._clean_rows(syslog._buffer))
    finally:
        syslog.unsubscribe(q1)
        syslog.unsubscribe(q2)
        syslog._buffer = saved
    with pytest.raises(ValueError):
        syslog.subscribe(None)


# ── correlation streaks and topology ────────────────────────────────────────

def test_correlation_streaks_are_pruned_per_tenant():
    from services import correlation as corr

    corr._rule_hit_counts.clear()
    corr._current_cycle_hits.clear()
    with tenant_scope(2):
        corr._track_rule_hit(None, "host_down_syslog", [5], 2)
        corr._prune_stale_hits()  # end of B's cycle
    with tenant_scope(1):
        corr._track_rule_hit(None, "host_down_syslog", [5], 2)
        corr._prune_stale_hits()  # end of A's cycle: must not reset B
    with tenant_scope(2):
        assert corr._track_rule_hit(None, "host_down_syslog", [5], 2) is True
    corr._rule_hit_counts.clear()
    corr._current_cycle_hits.clear()


async def test_topology_caches_are_per_tenant(two_tenants):
    from services import correlation as corr
    from services import topology

    topology.invalidate_topology_cache()
    corr._topo_cache.clear()
    built = {1: {10: None}, 2: {20: None}}

    async def fake_build(db, *a):
        from services.tenancy import session_tenant_id
        return dict(built[session_tenant_id(db)])

    with patch.object(topology, "build_topology", new=fake_build), \
         patch.object(corr, "build_topology", new=fake_build):
        async with two_tenants() as s:
            for tid in (1, 2, 1):
                with tenant_scope(tid):
                    topo, _, _ = await topology.cached_topology(s)
                    assert topo == built[tid]
                    assert await corr._get_topology(s) == built[tid]
    topology.invalidate_topology_cache()
    corr._topo_cache.clear()


# ── main.py caches ──────────────────────────────────────────────────────────

async def test_nav_counts_are_cached_per_tenant(two_tenants):
    import main

    main._nav_cache.clear()
    calls = []

    async def fake_counts(db):
        from services.tenancy import session_tenant_id
        calls.append(session_tenant_id(db))
        return {"proxmox": session_tenant_id(db)}

    with patch("services.integration.count_all_by_type", new=fake_counts):
        async with two_tenants() as s:
            with tenant_scope(1):
                assert (await main._get_nav_counts(s))["proxmox"] == 1
            with tenant_scope(2):
                assert (await main._get_nav_counts(s))["proxmox"] == 2
                main.invalidate_nav_cache()  # B's entry only
            with tenant_scope(1):
                assert (await main._get_nav_counts(s))["proxmox"] == 1  # cached
    assert calls == [1, 2]
    assert 1 in main._nav_cache and 2 not in main._nav_cache
    main.invalidate_nav_cache(1)
    assert main._nav_cache == {}


def test_live_tail_registration_is_cheap_to_drop():
    """unsubscribe of an unknown queue is a no-op (stream teardown races)."""
    from services import syslog

    syslog.unsubscribe(asyncio.Queue())

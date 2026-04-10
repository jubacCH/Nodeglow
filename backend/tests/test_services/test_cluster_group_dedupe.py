"""Tests for the integration cluster_group dedupe + failover logic in scheduler."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from integrations._base import BaseIntegration, CollectorResult
from models.integration import IntegrationConfig, Snapshot
from sqlalchemy import select


# ── Fake Integration ────────────────────────────────────────────────────────
# We register a fake "proxmox" plugin that returns canned data so the scheduler
# loop can be exercised without hitting a real Proxmox API. The cluster_name
# field controls which logical cluster the snapshot belongs to.


class _FakeProxmox(BaseIntegration):
    name = "proxmox"
    display_name = "Fake Proxmox"
    description = "Test fake"
    config_fields = []

    # Class-level overrides set by tests
    _next_data: dict | None = None
    _next_error: str | None = None

    async def collect(self) -> CollectorResult:
        if self._next_error:
            return CollectorResult(success=False, error=self._next_error)
        return CollectorResult(success=True, data=self._next_data or {})


@pytest.fixture
def fake_proxmox(monkeypatch):
    """Register the fake proxmox class in the registry, restore on teardown."""
    from integrations import _registry, get_registry
    get_registry()  # ensure auto-discover ran
    saved = _registry.get("proxmox")
    _registry["proxmox"] = _FakeProxmox
    yield _FakeProxmox
    if saved is not None:
        _registry["proxmox"] = saved
    else:
        _registry.pop("proxmox", None)


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _make_cfg(db, name: str, cluster_group: str | None = None) -> IntegrationConfig:
    from services.integration import encrypt_config
    cfg = IntegrationConfig(
        type="proxmox",
        name=name,
        config_json=encrypt_config({"host": "https://x", "token_id": "t", "token_secret": "s"}),
        enabled=True,
        cluster_group=cluster_group,
    )
    db.add(cfg)
    await db.flush()
    return cfg


async def _latest_snapshot(db, entity_id: int) -> Snapshot | None:
    res = await db.execute(
        select(Snapshot)
        .where(Snapshot.entity_type == "proxmox", Snapshot.entity_id == entity_id)
        .order_by(Snapshot.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_two_integrations_same_cluster_first_wins(db, fake_proxmox):
    """Two configs in the same cluster_group → first writes data, second is standby."""
    a = await _make_cfg(db, "Proxmox A", cluster_group="prxmxcl01")
    b = await _make_cfg(db, "Proxmox B", cluster_group="prxmxcl01")
    await db.commit()

    fake_proxmox._next_data = {"cluster_name": "prxmxcl01", "containers": [{"id": 1, "name": "c1"}]}
    fake_proxmox._next_error = None

    with patch("scheduler.AsyncSessionLocal") as mock_sess:
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _ctx():
            yield db
        mock_sess.side_effect = _ctx
        from scheduler import run_integration_checks
        await run_integration_checks()

    snap_a = await _latest_snapshot(db, a.id)
    snap_b = await _latest_snapshot(db, b.id)
    from scheduler import _STANDBY_MARKER

    # A is primary: data written, no standby marker
    assert snap_a is not None and snap_a.ok is True
    assert snap_a.data_json is not None and "prxmxcl01" in snap_a.data_json
    assert snap_a.error != _STANDBY_MARKER

    # B is standby: ok=True, no data, standby marker in error field
    assert snap_b is not None and snap_b.ok is True
    assert snap_b.data_json is None
    assert snap_b.error == _STANDBY_MARKER


async def test_auto_populate_cluster_group_from_cluster_name(db, fake_proxmox):
    """Proxmox integration without cluster_group gets one set after first poll."""
    a = await _make_cfg(db, "Proxmox A", cluster_group=None)
    await db.commit()

    fake_proxmox._next_data = {"cluster_name": "auto-detected-cluster"}
    fake_proxmox._next_error = None

    with patch("scheduler.AsyncSessionLocal") as mock_sess:
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _ctx():
            yield db
        mock_sess.side_effect = _ctx
        from scheduler import run_integration_checks
        await run_integration_checks()

    await db.refresh(a)
    assert a.cluster_group == "auto-detected-cluster"


async def test_failover_when_primary_fails(db, fake_proxmox):
    """If A fails, B (next in id order) becomes the primary that cycle."""
    a = await _make_cfg(db, "Proxmox A", cluster_group="prxmxcl01")
    b = await _make_cfg(db, "Proxmox B", cluster_group="prxmxcl01")
    await db.commit()

    # Make A fail by patching collect on the instance level via class override
    call_count = {"n": 0}
    original_collect = _FakeProxmox.collect

    async def fail_first(self):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CollectorResult(success=False, error="connection refused")
        return CollectorResult(success=True, data={"cluster_name": "prxmxcl01"})

    _FakeProxmox.collect = fail_first  # type: ignore
    try:
        with patch("scheduler.AsyncSessionLocal") as mock_sess:
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def _ctx():
                yield db
            mock_sess.side_effect = _ctx
            from scheduler import run_integration_checks
            await run_integration_checks()
    finally:
        _FakeProxmox.collect = original_collect  # type: ignore

    snap_a = await _latest_snapshot(db, a.id)
    snap_b = await _latest_snapshot(db, b.id)
    from scheduler import _STANDBY_MARKER

    # A failed → ok=False, error message
    assert snap_a is not None and snap_a.ok is False
    assert "connection refused" in (snap_a.error or "")

    # B should have become primary (data written, no standby marker)
    assert snap_b is not None and snap_b.ok is True
    assert snap_b.data_json is not None
    assert snap_b.error != _STANDBY_MARKER


async def test_no_cluster_group_means_independent(db, fake_proxmox):
    """Two integrations WITHOUT cluster_group both write their data (no dedupe)."""
    a = await _make_cfg(db, "Proxmox A", cluster_group=None)
    b = await _make_cfg(db, "Proxmox B", cluster_group=None)
    await db.commit()

    # Use distinct cluster_names so the auto-populate doesn't merge them
    call_count = {"n": 0}

    async def two_clusters(self):
        call_count["n"] += 1
        return CollectorResult(success=True, data={"cluster_name": f"cluster-{call_count['n']}"})

    original_collect = _FakeProxmox.collect
    _FakeProxmox.collect = two_clusters  # type: ignore
    try:
        with patch("scheduler.AsyncSessionLocal") as mock_sess:
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def _ctx():
                yield db
            mock_sess.side_effect = _ctx
            from scheduler import run_integration_checks
            await run_integration_checks()
    finally:
        _FakeProxmox.collect = original_collect  # type: ignore

    snap_a = await _latest_snapshot(db, a.id)
    snap_b = await _latest_snapshot(db, b.id)
    from scheduler import _STANDBY_MARKER

    # Both should be primaries (different clusters)
    assert snap_a is not None and snap_a.ok is True and snap_a.data_json is not None
    assert snap_b is not None and snap_b.ok is True and snap_b.data_json is not None
    assert snap_a.error != _STANDBY_MARKER
    assert snap_b.error != _STANDBY_MARKER

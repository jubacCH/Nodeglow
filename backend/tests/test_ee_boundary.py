"""The open-core boundary: the core runs on its own, plugins only extend it.

These tests hold with and without ee/ present (and with NODEGLOW_DISABLE_EE=1).
"""
import logging
import re
from pathlib import Path

import pytest

import ee_loader
import extensions
from extensions import DEFAULT_FEATURES, EDITION_COMMUNITY, EDITION_ENTERPRISE, Registry

BACKEND = Path(__file__).resolve().parent.parent


def test_core_never_imports_the_enterprise_package():
    """Only ee_loader.py may name nodeglow_ee (and only as a string)."""
    pattern = re.compile(r"^\s*(from|import)\s+nodeglow_ee\b", re.MULTILINE)
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv", "venv")):
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(rel)
    assert offenders == []


def test_disabled_ee_loads_nothing(monkeypatch):
    monkeypatch.setenv(ee_loader.DISABLE_ENV, "1")
    reg = Registry()
    assert ee_loader.discover() == []
    assert ee_loader.load_plugins(reg) == []
    assert reg.edition == EDITION_COMMUNITY
    assert reg.features == DEFAULT_FEATURES and not any(reg.features.values())
    assert reg.routers == [] and reg.scheduler_coordinator is None


def test_plugin_registers_through_the_registry(monkeypatch):
    from fastapi import APIRouter

    router = APIRouter()

    class Plugin:
        EDITION = EDITION_ENTERPRISE

        def register(self, reg):
            reg.add_router(router)
            reg.enable_feature("ai_assistant")

    monkeypatch.setattr(ee_loader, "discover", lambda: [("fake", Plugin())])
    reg = Registry()
    assert ee_loader.load_plugins(reg) == ["fake"]
    assert reg.edition == EDITION_ENTERPRISE
    assert reg.routers == [router]
    assert reg.feature_payload()["features"]["ai_assistant"] is True


def test_a_broken_plugin_is_skipped(monkeypatch, caplog):
    class Broken:
        def register(self, reg):
            raise RuntimeError("boom")

    monkeypatch.setattr(ee_loader, "discover", lambda: [("broken", Broken()), ("nothing", object())])
    reg = Registry()
    with caplog.at_level(logging.ERROR, logger="nodeglow.ee_loader"):
        assert ee_loader.load_plugins(reg) == []
    assert reg.edition == EDITION_COMMUNITY
    assert "broken" in caplog.text and "nothing" in caplog.text


def test_source_tree_is_found_by_env_path(monkeypatch, tmp_path):
    (tmp_path / "nodeglow_ee").mkdir()
    (tmp_path / "nodeglow_ee" / "__init__.py").write_text("")
    monkeypatch.setenv(ee_loader.PATH_ENV, str(tmp_path))
    assert ee_loader._source_tree() == tmp_path


async def test_fire_incident_resolved_without_hooks_is_a_noop(monkeypatch):
    monkeypatch.setattr(extensions, "registry", Registry())
    extensions.fire_incident_resolved([1, 2, 3])  # must not raise


async def test_fire_incident_resolved_runs_each_hook(monkeypatch):
    import asyncio

    reg = Registry()
    seen = []

    async def hook(incident_id):
        seen.append(incident_id)

    reg.on_incident_resolved(hook)
    monkeypatch.setattr(extensions, "registry", reg)
    extensions.fire_incident_resolved([7, 8])
    await asyncio.sleep(0)
    assert seen == [7, 8]


async def test_features_endpoint_matches_the_registry():
    from tests.test_routers.conftest import make_client

    async with make_client() as (client, _sf):
        resp = await client.get("/api/v2/features")
        expected = await extensions.registry.resolve_feature_payload()
    assert resp.status_code == 200
    data = resp.json()
    assert data == expected
    assert data["installed"] == extensions.registry.feature_payload()["features"]
    assert data["edition"] in (EDITION_COMMUNITY, EDITION_ENTERPRISE)
    assert set(DEFAULT_FEATURES) <= set(data["features"])
    if data["edition"] == EDITION_COMMUNITY:
        assert data["license"] is None and not any(data["features"].values())


async def test_without_a_license_provider_installed_means_usable():
    reg = Registry()
    reg.enable_feature("ai_assistant")
    payload = await reg.resolve_feature_payload()
    assert payload["features"]["ai_assistant"] is True and payload["license"] is None


async def test_license_provider_gates_the_reported_flags():
    reg = Registry()
    reg.enable_feature("ai_assistant")
    reg.enable_feature("ai_postmortem")

    async def provider():
        return {"license": {"status": "valid"}, "active": {"ai_assistant": True, "ha_scheduler": True}}

    reg.set_license_provider(provider)
    payload = await reg.resolve_feature_payload()
    assert payload["license"] == {"status": "valid"}
    assert payload["features"]["ai_assistant"] is True
    assert payload["features"]["ai_postmortem"] is False  # installed, not licensed
    assert payload["features"]["ha_scheduler"] is False  # licensed, not installed
    assert payload["installed"]["ai_postmortem"] is True
    with pytest.raises(RuntimeError):
        reg.set_license_provider(provider)


async def test_a_failing_license_provider_turns_flags_off(caplog):
    reg = Registry()
    reg.enable_feature("ai_assistant")

    async def provider():
        raise RuntimeError("boom")

    reg.set_license_provider(provider)
    payload = await reg.resolve_feature_payload()
    assert payload["features"]["ai_assistant"] is False
    assert payload["license"]["status"] == "error"


async def test_features_endpoint_needs_a_login():
    from tests.test_routers.conftest import make_client

    async with make_client(fake_user=None) as (client, _sf):
        assert (await client.get("/api/v2/features")).status_code == 401


@pytest.fixture
def registered_scheduler(monkeypatch):
    import scheduler as sched

    async def fake_get_setting(_db, key, default=None):
        return default

    monkeypatch.setattr("database.get_setting", fake_get_setting, raising=False)
    monkeypatch.setattr(extensions, "registry", Registry())
    started = []
    monkeypatch.setattr(sched.scheduler, "start", lambda *a, **kw: started.append(kw))
    yield sched, started
    sched.scheduler.remove_all_jobs()


async def test_community_scheduler_is_single_instance_even_with_redis(registered_scheduler, monkeypatch, caplog):
    sched, started = registered_scheduler
    monkeypatch.setattr(sched.config, "REDIS_URL", "redis://r:6379/0", raising=False)
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        try:
            await sched.start_scheduler()
        except Exception:
            pass  # SNMP seeding may fail against the test DB, after start()
    assert started == [{}]  # started immediately, not paused
    assert "single-instance" in caplog.text
    ids = {job.id for job in sched.scheduler.get_jobs()}
    assert "daily_ai_summary" not in ids and "ping_checks" in ids

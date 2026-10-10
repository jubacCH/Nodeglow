"""The license inside the app: feature gates, /api/v2/features, settings API, audit."""
import json
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from database import Setting
from ee_license_helpers import issue_test_license
from models.audit import AuditLog
from models.incident import Incident
from nodeglow_ee import license_runtime
from tests.test_routers.conftest import make_client

EXPIRED_DAYS = -30  # past the 14-day grace period
GRACE_DAYS = -3     # expired, still in the grace period


class _User:
    def __init__(self, role):
        self.id, self.username, self.role = 7, f"{role}-user", role


async def _resolved_incident(sf, **kw) -> int:
    async with sf() as db:
        inc = Incident(rule="host_down", title="nas01 down", severity="critical", status="resolved",
                       created_at=datetime.utcnow(), resolved_at=datetime.utcnow(), **kw)
        db.add(inc)
        await db.commit()
        return inc.id


# ── GET /api/v2/features per state ────────────────────────────────────────────


async def _features():
    async with make_client() as (client, _sf):
        resp = await client.get("/api/v2/features")
    assert resp.status_code == 200
    return resp.json()


async def test_features_without_a_license(ee_license):
    ee_license.clear()
    data = await _features()
    assert data["edition"] == "enterprise"
    assert data["license"]["status"] == "missing"
    assert not any(data["features"].values())
    assert all(data["installed"][f] for f in ("ha_scheduler", "ai_assistant", "ai_postmortem", "ai_daily_summary"))


async def test_features_with_a_valid_license():
    data = await _features()
    assert data["license"]["status"] == "valid" and data["license"]["days_left"] >= 364
    assert all(data["features"].values())
    assert "customer" not in data["license"]  # details are for admins only


async def test_features_with_a_partial_license(ee_license):
    ee_license.set(issue_test_license(features=["ai_assistant"]))
    data = await _features()
    assert data["features"]["ai_assistant"] is True
    assert data["features"]["ai_postmortem"] is False and data["features"]["ha_scheduler"] is False


async def test_features_in_the_grace_period(ee_license):
    ee_license.set(issue_test_license(days=GRACE_DAYS))
    data = await _features()
    assert data["license"]["status"] == "grace" and all(data["features"].values())


async def test_features_after_expiry(ee_license):
    ee_license.set(issue_test_license(days=EXPIRED_DAYS))
    data = await _features()
    assert data["license"]["status"] == "expired"
    assert data["features"]["ai_assistant"] is False and data["features"]["ai_postmortem"] is False
    assert data["features"]["ha_scheduler"] is True  # keeps monitoring correct


async def test_features_with_an_invalid_license(ee_license):
    ee_license.set("garbage")
    data = await _features()
    assert data["license"]["status"] == "invalid" and not any(data["features"].values())


# ── Glow chat ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("license_days, features, code", [
    (None, None, "license_missing"),
    (EXPIRED_DAYS, ("*",), "license_expired"),
    (365, ("ai_postmortem",), "feature_not_licensed"),
])
async def test_glow_chat_is_gated(ee_license, license_days, features, code):
    ee_license.set(None if license_days is None else issue_test_license(days=license_days, features=features))
    async with make_client() as (client, _sf):
        with patch("services.ai_client._anthropic_stream") as sent:
            resp = await client.post("/api/v1/glow/chat", json={"message": "hi"})
    assert resp.status_code == 402
    assert resp.json()["code"] == code and "license" in resp.json()["error"].lower()
    sent.assert_not_called()


async def test_glow_chat_passes_the_gate_in_the_grace_period(ee_license):
    ee_license.set(issue_test_license(days=GRACE_DAYS))
    async with make_client() as (client, _sf):
        resp = await client.post("/api/v1/glow/chat", json={"message": "hi"})
    assert resp.status_code == 409 and resp.json()["code"] == "ai_disabled"  # next check: the opt-in


# ── Postmortems ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("license_days", [None, EXPIRED_DAYS])
async def test_postmortem_regenerate_is_gated(ee_license, license_days):
    ee_license.set(None if license_days is None else issue_test_license(days=license_days))
    async with make_client() as (client, sf):
        inc_id = await _resolved_incident(sf)
        with patch("nodeglow_ee.ai.postmortem.generate_postmortem", new_callable=AsyncMock) as gen:
            resp = await client.post(f"/api/v1/incidents/{inc_id}/postmortem")
    assert resp.status_code == 402
    gen.assert_not_called()


async def test_automatic_postmortem_is_skipped_without_a_license(ee_license, db):
    from nodeglow_ee.ai import postmortem

    ee_license.clear()
    inc = Incident(rule="host_down", title="x", severity="critical", status="resolved",
                   created_at=datetime.utcnow(), resolved_at=datetime.utcnow())
    db.add(inc)
    await db.commit()

    @asynccontextmanager
    async def factory():
        yield db

    with patch.object(postmortem, "AsyncSessionLocal", factory), \
         patch("services.ai_config.is_ai_enabled", new=AsyncMock(return_value=True)), \
         patch.object(postmortem, "generate_completion") as gen:
        await postmortem.generate_postmortem(inc.id)
    gen.assert_not_called()
    await db.refresh(inc)
    assert inc.postmortem is None


async def test_stored_postmortems_stay_readable_after_expiry(ee_license):
    ee_license.set(issue_test_license(days=EXPIRED_DAYS))
    async with make_client() as (client, sf):
        inc_id = await _resolved_incident(sf, postmortem="## Summary\nDisk full.",
                                          postmortem_generated_at=datetime.utcnow())
        resp = await client.get(f"/api/v1/incidents/{inc_id}")
    assert resp.status_code == 200
    assert resp.json()["postmortem"] == "## Summary\nDisk full."


# ── Daily summary ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("license_days", [None, EXPIRED_DAYS])
async def test_daily_summary_test_send_is_gated(ee_license, license_days):
    ee_license.set(None if license_days is None else issue_test_license(days=license_days))
    async with make_client() as (client, _sf):
        # The endpoint is rate limited (3/min per IP); other tests use it too.
        with patch("services.shared_state.window_count", new=AsyncMock(return_value=0)):
            resp = await client.post("/settings/ai/test-summary")
    assert resp.status_code == 402


async def test_daily_summary_job_does_not_run_without_a_license(ee_license):
    from nodeglow_ee.ai import daily_summary

    ee_license.clear()
    opened = []

    @asynccontextmanager
    async def factory():
        opened.append(True)
        yield None

    with patch.object(license_runtime.manager, "_read_db", new=AsyncMock(return_value=(None, None))), \
         patch.object(daily_summary, "AsyncSessionLocal", factory):
        await daily_summary.run_daily_ai_summary()
    assert opened == []


async def test_daily_summary_job_runs_with_a_license():
    from nodeglow_ee.ai import daily_summary

    opened = []

    @asynccontextmanager
    async def factory():
        opened.append(True)
        raise RuntimeError("stop here")
        yield  # pragma: no cover

    with patch.object(license_runtime.manager, "_read_db", new=AsyncMock(return_value=(None, None))), \
         patch.object(daily_summary, "AsyncSessionLocal", factory), pytest.raises(RuntimeError):
        await daily_summary.run_daily_ai_summary()
    assert opened == [True]


# ── HA coordinator ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("license_text, expected", [
    (None, False),
    ("valid", True),
    ("expired", True),        # exempt: turning it off would duplicate every job
    ("no_ha", False),
])
def test_ha_coordinator_follows_the_license(ee_license, monkeypatch, license_text, expected):
    from services import shared_state
    from nodeglow_ee.ha.coordinator import LeaderElectionCoordinator

    texts = {
        None: None,
        "valid": issue_test_license(),
        "expired": issue_test_license(days=EXPIRED_DAYS),
        "no_ha": issue_test_license(features=["ai_assistant"]),
    }
    ee_license.set(texts[license_text])
    monkeypatch.setattr(shared_state, "_redis_url", lambda: "redis://r:6379/0")
    assert LeaderElectionCoordinator(instance_id="t").wants_control() is expected


async def test_license_hook_reads_the_license_before_the_coordinator(ee_license, monkeypatch):
    """A license stored in Settings counts for HA at scheduler start."""
    from services import shared_state
    from nodeglow_ee.ha.coordinator import LeaderElectionCoordinator

    ee_license.clear()
    monkeypatch.setattr(shared_state, "_redis_url", lambda: "redis://r:6379/0")
    coord = LeaderElectionCoordinator(instance_id="t")
    assert coord.wants_control() is False
    with patch.object(license_runtime.manager, "_read_db",
                      new=AsyncMock(return_value=(issue_test_license(), None))):
        await license_runtime.on_scheduler_start(None)
    assert coord.wants_control() is True


# ── Settings API: GET / POST / DELETE /settings/license ───────────────────────


async def _audit(sf, action):
    async with sf() as db:
        return (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()


async def test_admin_installs_replaces_and_removes_a_license(ee_license):
    ee_license.clear()
    async with make_client() as (client, sf):
        status = (await client.get("/settings/license")).json()
        assert status["status"] == "missing" and status["managed_by_env"] is False
        assert status["install_id"].startswith("ngi_")

        token = issue_test_license(customer="ACME AG", license_id="lic_acme", features=["ai_assistant"])
        resp = await client.post("/settings/license", json={"license": token})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "valid" and data["customer"] == "ACME AG"
        assert data["source"] == "settings" and data["features"] == ["ai_assistant"]

        async with sf() as db:
            assert (await db.get(Setting, license_runtime.SETTING_KEY)).value == token
        (entry,) = await _audit(sf, "license.install")
        assert entry.target_name == "ACME AG" and entry.username == "admin"
        assert json.loads(entry.details)["license_id"] == "lic_acme"

        features = (await client.get("/api/v2/features")).json()
        assert features["features"]["ai_assistant"] is True
        assert features["features"]["ai_postmortem"] is False

        # Another worker (fresh cache) reads it from the settings table.
        license_runtime.manager.reset()
        assert (await license_runtime.manager.current()).license.customer == "ACME AG"

        # Replace.
        resp = await client.post("/settings/license", json={"license": issue_test_license(customer="ACME 2")})
        assert resp.status_code == 200 and resp.json()["customer"] == "ACME 2"
        assert len(await _audit(sf, "license.install")) == 2

        # Remove.
        resp = await client.delete("/settings/license")
        assert resp.status_code == 200 and resp.json()["status"] == "missing"
        async with sf() as db:
            assert await db.get(Setting, license_runtime.SETTING_KEY) is None
        (removed,) = await _audit(sf, "license.remove")
        assert removed.target_name == "ACME 2"
        assert not any((await client.get("/api/v2/features")).json()["features"].values())


async def test_an_invalid_upload_is_rejected_and_nothing_changes(ee_license):
    ee_license.clear()
    async with make_client() as (client, sf):
        forged = issue_test_license(key=Ed25519PrivateKey.generate())
        for body in ({"license": forged}, {"license": "nope"}, {"license": ""}, {}):
            resp = await client.post("/settings/license", json=body)
            assert resp.status_code == 400, body
        async with sf() as db:
            assert await db.get(Setting, license_runtime.SETTING_KEY) is None
        assert await _audit(sf, "license.install") == []


async def test_an_expired_upload_is_stored_and_reported(ee_license):
    ee_license.clear()
    async with make_client() as (client, _sf):
        resp = await client.post("/settings/license", json={"license": issue_test_license(days=EXPIRED_DAYS)})
    assert resp.status_code == 200 and resp.json()["status"] == "expired"


async def test_install_id_binding_via_the_settings_page(ee_license):
    ee_license.clear()
    async with make_client() as (client, _sf):
        install_id = (await client.get("/settings/license")).json()["install_id"]
        ok = await client.post("/settings/license", json={"license": issue_test_license(install_id=install_id)})
        assert ok.status_code == 200 and ok.json()["install_id_bound"] is True
        other = await client.post("/settings/license", json={"license": issue_test_license(install_id="ngi_x")})
        assert other.status_code == 400 and "different installation" in other.json()["error"]


@pytest.mark.parametrize("role", ["editor", "readonly"])
async def test_only_admins_see_or_change_the_license(ee_license, role):
    ee_license.clear()
    async with make_client(fake_user=_User(role)) as (client, sf):
        assert (await client.get("/settings/license")).status_code == 403
        resp = await client.post("/settings/license", json={"license": issue_test_license()})
        assert resp.status_code == 403
        assert (await client.delete("/settings/license")).status_code == 403
        async with sf() as db:
            assert await db.get(Setting, license_runtime.SETTING_KEY) is None
        assert await _audit(sf, "license.install") == []


async def test_license_endpoints_need_a_login(ee_license):
    async with make_client(fake_user=None) as (client, _sf):
        assert (await client.get("/settings/license")).status_code in (401, 302, 303, 307)
        resp = await client.post("/settings/license", json={"license": issue_test_license()})
        assert resp.status_code in (401, 302, 303, 307, 403)


async def test_env_license_wins_and_locks_the_settings_page():
    async with make_client() as (client, _sf):
        status = (await client.get("/settings/license")).json()
        assert status["source"] == "environment" and status["managed_by_env"] is True
        assert (await client.post("/settings/license", json={"license": issue_test_license()})).status_code == 409
        assert (await client.delete("/settings/license")).status_code == 409


async def test_env_license_may_be_a_file_path(ee_license, tmp_path):
    path = tmp_path / "license.txt"
    path.write_text(issue_test_license(customer="From File") + "\n")
    ee_license.set(str(path))
    status = await license_runtime.manager.current()
    assert status.status == "valid" and status.license.customer == "From File"

"""Enterprise AI features: Glow, postmortems, daily summary — opt-in and wiring."""
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import notification_channels as nc
from database import Setting, encrypt_value
from models.incident import Incident
from tests.test_routers.conftest import make_client


async def _set(sf, **values):
    async with sf() as db:
        for k, v in values.items():
            row = await db.get(Setting, k)
            if row:
                row.value = v
            else:
                db.add(Setting(key=k, value=v))
        await db.commit()


def _factory(db):
    @asynccontextmanager
    async def factory():
        yield db
    return factory


# ── Registration ──────────────────────────────────────────────────────────────


async def test_features_endpoint_reports_enterprise():
    async with make_client() as (client, _sf):
        data = (await client.get("/api/v2/features")).json()
    assert data["edition"] == "enterprise"
    for flag in ("ha_scheduler", "ai_assistant", "ai_postmortem", "ai_daily_summary"):
        assert data["features"][flag] is True, flag


def test_plugin_registers_everything_on_a_fresh_registry():
    import nodeglow_ee
    from extensions import Registry

    reg = Registry()
    nodeglow_ee.plugin.register(reg)
    paths = {r.path for router in reg.routers for r in router.routes}
    assert {"/api/v1/glow/chat", "/api/v1/incidents/{incident_id}/postmortem",
            "/settings/ai/test-summary"} <= paths
    assert "/settings/license" in paths
    assert reg.scheduler_coordinator is not None and reg.license_provider is not None
    # The license hook first, then the daily summary job.
    assert len(reg.incident_resolved_hooks) == 1 and len(reg.scheduler_hooks) == 2


async def test_daily_summary_hook_schedules_the_job(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from nodeglow_ee.ai import daily_summary

    async def fake_get_setting(_db, key, default=None):
        return "7" if key == "daily_ai_summary_hour" else default

    monkeypatch.setattr("database.get_setting", fake_get_setting)
    sched = AsyncIOScheduler()
    await daily_summary.schedule(sched)
    job = sched.get_job("daily_ai_summary")
    assert job is not None and job.func is daily_summary.run_daily_ai_summary
    assert "hour='7'" in str(job.trigger)


async def test_resolving_an_incident_fires_the_postmortem_hook():
    async with make_client() as (client, sf):
        async with sf() as db:
            inc = Incident(rule="host_down", title="x", severity="critical", status="open",
                           created_at=datetime.utcnow())
            db.add(inc)
            await db.commit()
            inc_id = inc.id
        with patch("nodeglow_ee.ai.postmortem.generate_postmortem", new_callable=AsyncMock) as gen:
            resp = await client.post(f"/api/v1/incidents/{inc_id}/resolve")
            assert resp.status_code == 200, resp.text
            import asyncio
            await asyncio.sleep(0)
        gen.assert_awaited_once_with(inc_id)


# ── Opt-in is honoured ────────────────────────────────────────────────────────


async def test_glow_chat_refuses_when_ai_is_off():
    async with make_client() as (client, sf):
        await _set(sf, claude_api_key=encrypt_value("sk-ant-x"))  # key alone is not consent
        with patch("services.ai_client._anthropic_stream") as sent:
            resp = await client.post("/api/v1/glow/chat", json={"message": "hi"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "ai_disabled"
        assert "Settings > AI" in resp.json()["error"]
        sent.assert_not_called()


async def test_glow_chat_reports_missing_provider_config():
    async with make_client() as (client, sf):
        await _set(sf, ai_enabled="1", ai_provider="openai_compatible")
        resp = await client.post("/api/v1/glow/chat", json={"message": "hi"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "ai_not_configured"


async def test_postmortem_regenerate_refuses_when_ai_is_off():
    async with make_client() as (client, sf):
        async with sf() as db:
            inc = Incident(rule="host_down", title="x", severity="critical", status="resolved",
                           created_at=datetime.utcnow(), resolved_at=datetime.utcnow())
            db.add(inc)
            await db.commit()
            inc_id = inc.id
        with patch("nodeglow_ee.ai.postmortem.generate_postmortem", new_callable=AsyncMock) as gen:
            resp = await client.post(f"/api/v1/incidents/{inc_id}/postmortem")
        assert resp.status_code == 409 and resp.json()["code"] == "ai_disabled"
        gen.assert_not_called()


async def test_daily_summary_test_refuses_when_ai_is_off():
    async with make_client() as (client, sf):
        await _set(sf, claude_api_key=encrypt_value("sk-ant-x"))
        resp = await client.post("/settings/ai/test-summary")
        assert resp.status_code == 409 and resp.json()["code"] == "ai_disabled"


async def test_auto_postmortem_is_skipped_when_ai_is_off(db):
    from nodeglow_ee.ai import postmortem

    inc = Incident(rule="host_down", title="nas01 down", severity="critical", status="resolved",
                   created_at=datetime.utcnow(), resolved_at=datetime.utcnow())
    db.add(inc)
    await db.commit()
    with patch.object(postmortem, "AsyncSessionLocal", _factory(db)), \
         patch.object(postmortem, "generate_completion") as gen:
        await postmortem.generate_postmortem(inc.id)
    gen.assert_not_called()
    await db.refresh(inc)
    assert inc.postmortem is None and inc.postmortem_generated_at is None


# ── Daily summary delivery ────────────────────────────────────────────────────


async def test_test_summary_reaches_selected_new_channels():
    async with make_client() as (client, sf):
        await _set(
            sf, ai_enabled="1", claude_api_key=encrypt_value("sk-ant-x"),
            daily_ai_summary_channels="telegram,slack,ntfy",
            telegram_bot_token=encrypt_value("1:A"), telegram_chat_id="-100",
            teams_enabled="1", teams_webhook_url=encrypt_value("https://x.logic.azure.com/w"),
            slack_enabled="1", slack_webhook_url=encrypt_value("https://hooks.slack.com/services/x"),
            ntfy_enabled="1", ntfy_topic="alerts",
        )
        usage = {"input_tokens": 1, "output_tokens": 1, "model": "m"}
        with patch("nodeglow_ee.ai.daily_summary.build_daily_summary_data", new=AsyncMock(return_value={})), \
             patch("nodeglow_ee.ai.daily_summary.format_daily_summary_prompt", return_value="p"), \
             patch("services.ai_client.generate_completion",
                   new=AsyncMock(return_value=("All quiet.", usage))), \
             patch("notifications._send_telegram", new_callable=AsyncMock) as tg, \
             patch.object(nc, "send_teams", new_callable=AsyncMock) as teams, \
             patch.object(nc, "send_slack", new_callable=AsyncMock) as slack, \
             patch.object(nc, "send_ntfy", side_effect=nc.ChannelError("ntfy: HTTP 500")):
            r = await client.post("/settings/ai/test-summary")
        assert r.status_code == 200, r.text
        assert tg.await_args.args[0] == "1:A"
        slack.assert_awaited_once()
        assert slack.await_args.args[0] == "https://hooks.slack.com/services/x"
        assert slack.await_args.args[2] == "All quiet."
        teams.assert_not_called()  # not selected
        assert "ntfy: HTTP 500" in r.json()["message"]

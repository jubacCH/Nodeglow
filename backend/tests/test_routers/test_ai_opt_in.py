"""AI settings honour the opt-in and record who enabled it (core plumbing)."""
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from database import Setting, encrypt_value
from models.audit import AuditLog
from tests.test_routers.conftest import make_client

JSON = {"accept": "application/json"}


async def _set(sf, **values):
    async with sf() as db:
        for k, v in values.items():
            row = await db.get(Setting, k)
            if row:
                row.value = v
            else:
                db.add(Setting(key=k, value=v))
        await db.commit()


async def _get(sf, key):
    async with sf() as db:
        row = await db.get(Setting, key)
        return row.value if row else None


async def test_status_endpoint():
    async with make_client() as (client, sf):
        data = (await client.get("/api/v1/ai/status")).json()
        assert data["enabled"] is False and data["available"] is False
        await _set(sf, ai_enabled="1", claude_api_key=encrypt_value("sk-ant-x"))
        data = (await client.get("/api/v1/ai/status")).json()
        assert data["available"] is True and data["provider"] == "anthropic"
        assert "sk-ant" not in str(data)


async def test_enabling_requires_a_configured_provider():
    async with make_client() as (client, sf):
        resp = await client.post("/settings/ai/save", data={"ai_enabled": "1"}, headers=JSON)
        assert resp.status_code == 400 and "claude_api_key" in resp.json()["error"]
        assert await _get(sf, "ai_enabled") is None


async def test_enable_records_who_and_when_and_audits():
    async with make_client() as (client, sf):
        resp = await client.post(
            "/settings/ai/save",
            data={"ai_enabled": "1", "ai_provider": "anthropic", "claude_api_key": "sk-ant-new"},
            headers=JSON,
        )
        assert resp.status_code == 200, resp.text
        cfg = resp.json()["config"]
        assert cfg["ai_enabled"] is True and cfg["ai_enabled_by"] == "admin" and cfg["ai_enabled_at"]
        assert cfg["claude_has_key"] is True and "sk-ant-new" not in resp.text
        # Stored encrypted, like the other secrets.
        assert (await _get(sf, "claude_api_key")) != "sk-ant-new"
        async with sf() as db:
            audit = (await db.execute(select(AuditLog))).scalars().all()
        assert [a.action for a in audit] == ["settings.ai_enabled"]
        assert audit[0].username == "admin"

        resp = await client.post("/settings/ai/save", data={"ai_enabled": "0"}, headers=JSON)
        assert resp.json()["config"]["ai_enabled"] is False
        async with sf() as db:
            actions = [a.action for a in (await db.execute(select(AuditLog))).scalars().all()]
        assert actions == ["settings.ai_enabled", "settings.ai_disabled"]


async def test_openai_compatible_settings_round_trip_without_leaking_the_key():
    async with make_client() as (client, sf):
        resp = await client.post("/settings/ai/save", data={
            "ai_provider": "openai_compatible",
            "ai_openai_base_url": "http://10.0.0.5:11434/v1",
            "ai_openai_model": "llama3.1:8b",
            "ai_openai_api_key": "local-secret",
            "ai_redact_hostnames": "1",
        }, headers=JSON)
        assert resp.status_code == 200, resp.text
        data = (await client.get("/settings/ai/config")).json()
        assert data["ai_provider"] == "openai_compatible"
        assert data["ai_openai_has_key"] is True and data["configured"] is True
        assert data["ai_redact_enabled"] is True and data["ai_redact_hostnames"] is True
        assert "local-secret" not in str(data)
        assert (await _get(sf, "ai_openai_api_key")) != "local-secret"


async def test_metadata_endpoint_is_rejected():
    async with make_client() as (client, _sf):
        resp = await client.post("/settings/ai/save", data={
            "ai_provider": "openai_compatible",
            "ai_openai_base_url": "http://169.254.169.254/v1",
            "ai_openai_model": "x",
        }, headers=JSON)
        assert resp.status_code == 400 and "metadata" in resp.json()["error"]


async def test_connection_test_uses_form_values():
    async with make_client() as (client, _sf):
        with patch("services.ai_client.check_connection", new_callable=AsyncMock,
                   return_value={"ok": True, "message": "Connected"}) as check:
            resp = await client.post("/settings/ai/test-connection", data={
                "ai_provider": "openai_compatible",
                "ai_openai_base_url": "http://127.0.0.1:1234/v1",
                "ai_openai_model": "qwen",
            })
        assert resp.status_code == 200
        cfg = check.call_args.args[0]
        assert cfg.openai_base_url == "http://127.0.0.1:1234/v1" and cfg.openai_model == "qwen"


async def test_settings_require_admin():
    class Viewer:
        id = 2
        username = "viewer"
        role = "readonly"

    async with make_client(fake_user=Viewer()) as (client, _sf):
        assert (await client.get("/settings/ai/config")).status_code == 403
        assert (await client.post("/settings/ai/save", data={"ai_enabled": "1"})).status_code == 403
        assert (await client.post("/settings/ai/test-connection")).status_code == 403

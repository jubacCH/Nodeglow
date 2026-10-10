"""Telegram / Discord / webhook secrets: encrypted, never sent to the UI,
blank keeps, *_clear removes; the daily summary defaults include Teams/Slack/ntfy."""
from unittest.mock import AsyncMock, patch

import notification_channels as nc
from database import Setting, decrypt_value, encrypt_value
from tests.test_routers.conftest import make_client

JSON = {"accept": "application/json"}
SECRETS = {
    "telegram_bot_token": "123456:ABC-tg-secret",
    "discord_webhook_url": "https://discord.com/api/webhooks/1/dc-secret",
    "webhook_url": "https://hooks.example.com/wh-secret",
    "webhook_secret": "signing-secret",
}


async def _raw(sf, key):
    async with sf() as db:
        row = await db.get(Setting, key)
        return row.value if row else None


async def _set(sf, **values):
    async with sf() as db:
        for k, v in values.items():
            row = await db.get(Setting, k)
            if row:
                row.value = v
            else:
                db.add(Setting(key=k, value=v))
        await db.commit()


async def test_save_encrypts_and_json_never_returns_the_values():
    async with make_client() as (client, sf):
        r = await client.post("/settings/notifications/save", headers=JSON,
                              data={**SECRETS, "telegram_chat_id": "-100"})
        assert r.status_code == 200, r.text
        for key, value in SECRETS.items():
            raw = await _raw(sf, key)
            assert raw != value and decrypt_value(raw) == value

        resp = await client.get("/settings/json")
        body = resp.json()
        for key, value in SECRETS.items():
            assert key not in body
            assert body[f"{key}_has_value"] is True
            assert value not in resp.text
        assert body["telegram_chat_id"] == "-100"


async def test_legacy_plaintext_rows_do_not_leak_either():
    async with make_client() as (client, sf):
        await _set(sf, **SECRETS)
        resp = await client.get("/settings/json")
        for value in SECRETS.values():
            assert value not in resp.text
        assert resp.json()["webhook_secret_has_value"] is True


async def test_blank_keeps_and_clear_removes():
    async with make_client() as (client, sf):
        await client.post("/settings/notifications/save", headers=JSON, data=SECRETS)
        before = {k: await _raw(sf, k) for k in SECRETS}

        # A save from the UI sends the fields blank: nothing may change.
        r = await client.post("/settings/notifications/save", headers=JSON,
                              data={k: "" for k in SECRETS})
        assert r.status_code == 200
        assert {k: await _raw(sf, k) for k in SECRETS} == before

        r = await client.post("/settings/notifications/save", headers=JSON, data={
            "telegram_bot_token_clear": "1", "webhook_secret_clear": "1",
        })
        assert r.status_code == 200
        body = (await client.get("/settings/json")).json()
        assert body["telegram_bot_token_has_value"] is False
        assert body["webhook_secret_has_value"] is False
        assert body["discord_webhook_url_has_value"] is True
        assert body["webhook_url_has_value"] is True


async def test_test_endpoint_decrypts_telegram_token():
    async with make_client() as (client, sf):
        await _set(sf, telegram_bot_token=encrypt_value(SECRETS["telegram_bot_token"]),
                   telegram_chat_id="-100")
        with patch("notifications._send_telegram", new_callable=AsyncMock) as tg, \
             patch("notifications._log_notification", new_callable=AsyncMock):
            r = await client.post("/settings/notifications/test", json={"channel": "telegram"})
        assert r.status_code == 200, r.text
        assert tg.await_args.args[0] == SECRETS["telegram_bot_token"]


async def test_daily_summary_defaults_include_new_channels():
    async with make_client() as (client, _sf):
        body = (await client.get("/settings/ai/config")).json()
        assert body["daily_ai_summary_channels"].split(",") == [
            "telegram", "discord", "webhook", "email", "teams", "slack", "ntfy",
        ]
        assert "teams" in (await client.get("/settings/json")).json()["daily_ai_summary_channels"]


async def test_send_to_selected_skips_disabled_and_collects_errors():
    cfg = nc.ExtraChannelConfig(
        teams_enabled=False, teams_webhook_url="https://x/w",
        slack_enabled=True, slack_webhook_url="https://hooks.slack.com/s",
        ntfy_enabled=True, ntfy_topic="alerts",
    )
    with patch.object(nc, "send_teams", new_callable=AsyncMock) as teams, \
         patch.object(nc, "send_slack", new_callable=AsyncMock), \
         patch.object(nc, "send_ntfy", side_effect=nc.ChannelError("boom")):
        results = await nc.send_to_selected(cfg, {"teams", "slack", "ntfy"}, "t", "m")
    teams.assert_not_called()
    assert [(n, str(e) if e else None) for n, e in results] == [("slack", None), ("ntfy", "boom")]

"""Telegram / Discord / webhook secrets: encrypted, never sent to the UI,
blank keeps, *_clear removes; the daily summary defaults include Teams/Slack/ntfy."""
from unittest.mock import AsyncMock, patch

import notification_channels as nc
from database import Setting, encrypt_value
from tests.test_routers.conftest import make_client

JSON = {"accept": "application/json"}
SECRETS = {
    "telegram_bot_token": "123456:ABC-tg-secret",
    "discord_webhook_url": "https://discord.com/api/webhooks/1/dc-secret",
    "webhook_url": "https://hooks.example.com/wh-secret",
    "webhook_secret": "signing-secret",
}


async def _raw(sf, key):
    from tests.conftest import setting_row
    async with sf() as db:
        row = await setting_row(db, key)
        return row.value if row else None


async def _open(sf, raw, tenant_id=1):
    """A stored secret decrypted with the tenant's data key."""
    from services import tenant_keys
    async with sf() as db:
        return await tenant_keys.decrypt_secret(db, raw, tenant_id=tenant_id)


async def _set(sf, **values):
    from tests.conftest import put_settings
    async with sf() as db:
        await put_settings(db, **values)


async def test_save_encrypts_and_json_never_returns_the_values():
    async with make_client() as (client, sf):
        r = await client.post("/settings/notifications/save", headers=JSON,
                              data={**SECRETS, "telegram_chat_id": "-100"})
        assert r.status_code == 200, r.text
        for key, value in SECRETS.items():
            raw = await _raw(sf, key)
            assert raw != value and raw.startswith("v2:") and await _open(sf, raw) == value

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


# ── A channel secret that does not decrypt fails only its own channel ────────

async def _seed_with_one_broken_secret(sf):
    """Discord fine; the webhook's signing secret and the ntfy token are
    tenant-key values that do not decrypt (a corrupted / foreign value)."""
    from services import tenant_keys
    async with sf() as db:
        good = await tenant_keys.encrypt_secret(db, SECRETS["discord_webhook_url"], tenant_id=1)
        wh_url = await tenant_keys.encrypt_secret(db, SECRETS["webhook_url"], tenant_id=1)
        await db.commit()
    broken = good[:-6] + "AAAAAA"
    await _set(sf, notify_enabled="1", discord_webhook_url=good, webhook_url=wh_url,
               webhook_secret=broken, ntfy_enabled="1", ntfy_topic="alerts", ntfy_token=broken)
    return broken


async def test_one_undecryptable_channel_secret_does_not_stop_the_others():
    import notifications

    async with make_client() as (_client, sf):
        broken = await _seed_with_one_broken_secret(sf)
        with patch("database.AsyncSessionLocal", sf), \
             patch("notifications._send_discord", new_callable=AsyncMock) as dc, \
             patch("notifications._send_webhook", new_callable=AsyncMock) as wh, \
             patch.object(nc, "send_ntfy", new_callable=AsyncMock) as ntfy, \
             patch("notifications._log_notification", new_callable=AsyncMock) as logged:
            await notifications.notify("Host down", "srv01 is down", "critical", tenant_id=1)
    assert dc.await_args.args[0] == SECRETS["discord_webhook_url"]
    wh.assert_not_called()      # never unsigned, never with the ciphertext as secret
    ntfy.assert_not_awaited()   # never without its token
    status = {c.args[0]: (c.args[4], c.args[5] if len(c.args) > 5 else None)
              for c in logged.await_args_list}
    assert status["discord"] == ("sent", None)
    assert status["webhook"][0] == "failed" and "does not decrypt" in status["webhook"][1]
    assert status["ntfy"][0] == "failed"
    for _ch, (_s, err) in status.items():
        assert not err or (broken not in err and SECRETS["webhook_url"] not in err)


async def test_test_endpoint_reports_only_the_tested_channels_secret():
    async with make_client() as (client, sf):
        await _seed_with_one_broken_secret(sf)
        await _set(sf, slack_enabled="1", slack_webhook_url="https://hooks.slack.com/services/T/B/x")
        with patch("notifications._send_discord", new_callable=AsyncMock) as dc, \
             patch.object(nc, "send_slack", new_callable=AsyncMock) as slack, \
             patch("notifications._log_notification", new_callable=AsyncMock):
            ok = await client.post("/settings/notifications/test", json={"channel": "discord"})
            # ntfy's token is broken; Slack's own secret is fine.
            ok_slack = await client.post("/settings/notifications/test", json={"channel": "slack"})
            bad = await client.post("/settings/notifications/test", json={"channel": "webhook"})
            bad_ntfy = await client.post("/settings/notifications/test", json={"channel": "ntfy"})
    assert ok.status_code == 200 and dc.await_count == 1
    assert ok_slack.status_code == 200, ok_slack.text
    slack.assert_awaited_once()
    for r in (bad, bad_ntfy):
        assert r.status_code == 500 and "does not decrypt" in r.json()["message"]

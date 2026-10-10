"""Settings endpoints for the Teams / Slack / ntfy channels."""
from unittest.mock import AsyncMock, patch

import notification_channels as nc
from tests.test_routers.conftest import make_client

TEAMS_URL = "https://prod-01.westeurope.logic.azure.com/workflows/abc?sig=SECRET"

_JSON = {"accept": "application/json"}


async def _setting_row(session_factory, key):
    from database import Setting
    async with session_factory() as db:
        return await db.get(Setting, key)


async def test_save_encrypts_secrets_and_json_only_exposes_flags():
    async with make_client() as (client, sf):
        r = await client.post("/settings/notifications/save", headers=_JSON, data={
            "public_url": "https://ng.example/",
            "teams_enabled": "1", "teams_webhook_url": TEAMS_URL,
            "notify_teams_min_severity": "critical",
            "ntfy_enabled": "1", "ntfy_topic": "alerts", "ntfy_token": "tk_secret",
            "ntfy_server_url": "",
        })
        assert r.status_code == 200, r.text

        row = await _setting_row(sf, "teams_webhook_url")
        assert row.value and TEAMS_URL not in row.value  # stored encrypted
        assert "tk_secret" not in (await _setting_row(sf, "ntfy_token")).value

        s = (await client.get("/settings/json")).json()
        assert s["public_url"] == "https://ng.example"
        assert s["teams_enabled"] == "1" and s["teams_has_url"] is True
        assert s["notify_teams_min_severity"] == "critical"
        assert s["slack_has_url"] is False and s["slack_enabled"] == "0"
        assert s["ntfy_topic"] == "alerts" and s["ntfy_has_token"] is True
        assert s["ntfy_server_url"] == "https://ntfy.sh"
        assert "teams_webhook_url" not in s and "ntfy_token" not in s

        # blank secret keeps the stored value; *_clear removes it
        r = await client.post("/settings/notifications/save", headers=_JSON, data={
            "teams_webhook_url": "", "ntfy_token_clear": "1",
        })
        assert r.status_code == 200
        s = (await client.get("/settings/json")).json()
        assert s["teams_has_url"] is True
        assert s["ntfy_has_token"] is False


async def test_save_rejects_unsafe_urls_and_bad_topics():
    async with make_client() as (client, _sf):
        r = await client.post("/settings/notifications/save", headers=_JSON,
                              data={"slack_webhook_url": "http://localhost/hook"})
        assert r.status_code == 400
        r = await client.post("/settings/notifications/save", headers=_JSON,
                              data={"ntfy_topic": "bad/topic"})
        assert r.status_code == 400
        r = await client.post("/settings/notifications/save", headers=_JSON,
                              data={"public_url": "ng.example"})
        assert r.status_code == 400


async def test_test_endpoint_sends_to_new_channel_and_logs():
    async with make_client() as (client, _sf):
        await client.post("/settings/notifications/save", headers=_JSON, data={
            "teams_webhook_url": TEAMS_URL, "public_url": "https://ng.example",
        })
        with patch.object(nc, "send_teams", new_callable=AsyncMock) as teams, \
             patch("notifications._log_notification", new_callable=AsyncMock) as log:
            r = await client.post("/settings/notifications/test", json={"channel": "teams"})
        assert r.status_code == 200, r.text
        teams.assert_awaited_once()
        assert teams.await_args.args[0] == TEAMS_URL
        assert teams.await_args.args[4] == "https://ng.example"
        assert log.await_args.args[0] == "teams" and log.await_args.args[4] == "sent"


async def test_test_endpoint_reports_channel_error():
    async with make_client() as (client, _sf):
        with patch("notifications._log_notification", new_callable=AsyncMock) as log:
            r = await client.post("/settings/notifications/test", json={"channel": "slack"})
        assert r.status_code == 500
        assert r.json()["message"] == "slack: not configured"
        assert log.await_args.args[4] == "failed"

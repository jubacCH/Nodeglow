"""Tests for the notification service."""
from unittest.mock import AsyncMock, patch

import pytest

import notifications

# The database session is mocked: no tenant keys (services.tenant_keys).
pytestmark = pytest.mark.usefixtures("keyless_tenant_cipher")


def _clear_rate_limit():
    """Clear the rate limit cache between tests."""
    notifications._recent.clear()

def _notify(*args, **kwargs):
    """notify() for tenant 1 (notifications are per tenant since phase 4)."""
    kwargs.setdefault("tenant_id", 1)
    return notifications.notify(*args, **kwargs)



async def test_notify_disabled_skips_all():
    """When notify_enabled is '0', no channels should be called."""
    _clear_rate_limit()
    async def fake_get_setting(db, key, default="", **_kw):
        return {"notify_enabled": "0"}.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Test", "Message")
        mock_tg.assert_not_called()


async def test_notify_sends_telegram():
    """When telegram is configured, it should be called."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC",
        "telegram_chat_id": "456",
        "discord_webhook_url": "",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Host offline", "Server1 is down", severity="critical")
        mock_tg.assert_called_once()
        mock_dc.assert_not_called()


async def test_notify_sends_discord():
    """When discord is configured, it should be called."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "", "telegram_chat_id": "",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Test discord", "Message")
        mock_tg.assert_not_called()
        mock_dc.assert_called_once()


async def test_notify_channel_failure_doesnt_crash():
    """If one channel fails, the others should still work."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    async def failing_telegram(*args, **kwargs):
        raise ConnectionError("Network error")

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", side_effect=failing_telegram), \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Test failure", "Message")
        mock_dc.assert_called_once()


async def test_notify_severity_color():
    """Critical should use red, info should use green for Discord."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "", "telegram_chat_id": "",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _notify("Critical alert", "Down", severity="critical")
        color_critical = mock_dc.call_args[0][3]

        _clear_rate_limit()
        mock_dc.reset_mock()
        await _notify("Info alert", "Up", severity="info")
        color_info = mock_dc.call_args[0][3]

        assert color_critical == 0xe74c3c  # red
        assert color_info == 0x2ecc71  # green


async def test_rate_limiting():
    """Same title should be rate-limited within cooldown window."""
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "discord_webhook_url": "",
        "webhook_url": "", "webhook_secret": "",
        "smtp_host": "", "smtp_port": "587", "smtp_user": "",
        "smtp_password": "", "smtp_from": "", "smtp_to": "",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _notify("Duplicate alert", "First")
        await _notify("Duplicate alert", "Second")  # Should be rate-limited
        assert mock_tg.call_count == 1  # Only first call went through


async def test_every_classic_channel_carries_the_link():
    """Telegram, Discord, webhook and e-mail link to the incident.

    Only Teams, Slack and ntfy used to: an alert at night came without any way
    to open what it was about.
    """
    _clear_rate_limit()
    settings = {
        "notify_enabled": "1", "public_url": "https://monitor.example.com/",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "webhook_url": "https://hooks.example.com/x", "webhook_secret": "",
        "smtp_host": "smtp.example.com", "smtp_port": "587", "smtp_user": "u",
        "smtp_password": "pw", "smtp_from": "", "smtp_to": "ops@example.com",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    link = "https://monitor.example.com/incidents/42"
    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as mock_tg, \
         patch.object(notifications, "_send_discord", new_callable=AsyncMock) as mock_dc, \
         patch.object(notifications, "_send_webhook", new_callable=AsyncMock) as mock_wh, \
         patch.object(notifications, "_send_email", new_callable=AsyncMock) as mock_mail, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Host offline: nas", "nas", link_path="/incidents/42")

    assert link in mock_tg.await_args.args[2]
    assert mock_dc.await_args.args[4] == link
    assert mock_wh.await_args.args[5] == link
    text_body, html_body = mock_mail.await_args.args[7], mock_mail.await_args.args[8]
    assert link in text_body and link in html_body


async def test_blocked_webhook_is_a_failure_not_a_delivery():
    """A webhook URL the SSRF guard refuses must not be logged as sent."""
    with patch.object(notifications, "_url_is_safe", new=AsyncMock(return_value=False)):
        with pytest.raises(ValueError):
            await notifications._send_webhook("http://127.0.0.1/x", "", "t", "m")
        with pytest.raises(ValueError):
            await notifications._send_discord("http://127.0.0.1/x", "t", "m")


async def test_smtp_connections_have_a_timeout():
    """A mail server that never answers must not hold the job for good."""
    with patch("notifications.smtplib.SMTP") as smtp:
        await notifications._send_email(
            "smtp.example.com", 587, "u", "pw", "a@example.com", "b@example.com",
            "s", "t", "<p>t</p>")
    assert smtp.call_args.kwargs["timeout"] == notifications.SMTP_TIMEOUT


async def test_a_failed_delivery_is_retried_and_then_logged_as_sent(monkeypatch):
    """One failed HTTP call is not the end of an alert."""
    _clear_rate_limit()
    monkeypatch.setattr(notifications, "RETRY_DELAYS", (0, 0))
    settings = {
        "notify_enabled": "1", "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
        "smtp_port": "587",
    }

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    attempts = []

    async def flaky(*_args):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("502 Bad Gateway")

    logged = AsyncMock()
    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram", new=flaky), \
         patch.object(notifications, "_log_notification", new=logged):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Host down: nas", "nas")
        for task in list(notifications._retry_tasks):
            await task

    assert len(attempts) == 3
    assert [call.args[4] for call in logged.await_args_list] == ["sent"]


async def test_a_delivery_that_keeps_failing_is_logged_once_with_the_attempts(monkeypatch):
    _clear_rate_limit()
    monkeypatch.setattr(notifications, "RETRY_DELAYS", (0, 0))
    settings = {"notify_enabled": "1", "telegram_bot_token": "123:ABC", "telegram_chat_id": "456",
                "smtp_port": "587"}

    async def fake_get_setting(db, key, default="", **_kw):
        return settings.get(key, default)

    logged = AsyncMock()
    with patch("database.AsyncSessionLocal") as mock_cls, \
         patch("database.get_setting", side_effect=fake_get_setting), \
         patch.object(notifications, "_send_telegram",
                      new=AsyncMock(side_effect=RuntimeError("401 Unauthorized"))), \
         patch.object(notifications, "_log_notification", new=logged):
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await _notify("Host down: nas", "nas")
        for task in list(notifications._retry_tasks):
            await task

    assert logged.await_count == 1
    assert logged.await_args.args[4] == "failed"
    assert "after 3 attempts" in logged.await_args.args[5]

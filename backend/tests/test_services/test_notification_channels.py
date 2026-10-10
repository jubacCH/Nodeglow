"""Teams / Slack / ntfy: payload builders, dispatcher selection, error handling.

Payloads are compared against golden JSON files in ``golden/``. Regenerate
one deliberately with ``NODEGLOW_UPDATE_GOLDEN=1`` after an intended change.
"""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import notification_channels as nc
import notifications

GOLDEN = Path(__file__).parent / "golden" / "notification_channels"
TEAMS_URL = "https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=SECRET"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/SECRETxyz"


def _golden(name: str, actual: dict) -> None:
    path = GOLDEN / f"{name}.json"
    if os.environ.get("NODEGLOW_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


# ── Payload builders ─────────────────────────────────────────────────────────

def test_teams_payload_critical_with_link():
    payload = nc.build_teams_payload(
        "🔴 Incident: Core switch down", "3 hosts unreachable\nsince 12:00", "critical",
        "https://nodeglow.example.ch/incidents/42",
    )
    _golden("teams_critical_link", payload)


def test_teams_payload_info_without_link_has_no_actions():
    payload = nc.build_teams_payload("✅ Resolved: x", "back online", "info")
    _golden("teams_info_nolink", payload)
    assert "actions" not in payload["attachments"][0]["content"]


def test_teams_payload_is_a_workflows_envelope():
    payload = nc.build_teams_payload("t", "m", "warning")
    assert payload["type"] == "message"
    att = payload["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["content"]["type"] == "AdaptiveCard"
    assert att["content"]["version"] == "1.4"
    assert att["content"]["body"][0]["style"] == "warning"


def test_slack_payload_warning_with_link():
    payload = nc.build_slack_payload(
        "Rule: Disk usage", "disk = 93 (> 90) on proxmox/<pve1> & co", "warning",
        "https://nodeglow.example.ch/rules",
    )
    _golden("slack_warning_link", payload)


def test_slack_payload_escapes_and_truncates():
    payload = nc.build_slack_payload("x" * 300, "<!channel> & m", "error")
    header = payload["blocks"][0]["text"]["text"]
    assert len(header) == 150
    assert payload["blocks"][1]["text"]["text"] == "&lt;!channel&gt; &amp; m"
    assert payload["text"].startswith("🟠 [ERROR] ")
    assert [b["type"] for b in payload["blocks"]] == ["header", "section", "context"]


def test_ntfy_payload_critical_with_link():
    payload = nc.build_ntfy_payload(
        "nodeglow-alerts", "🔴 Incident: Core switch down", "3 hosts unreachable",
        "critical", "https://nodeglow.example.ch/incidents/42",
    )
    _golden("ntfy_critical_link", payload)


@pytest.mark.parametrize("severity,priority", [
    ("critical", 5), ("error", 4), ("warning", 4), ("info", 3), ("bogus", 3),
])
def test_ntfy_priority_mapping(severity, priority):
    assert nc.build_ntfy_payload("t", "x", "m", severity)["priority"] == priority


def test_ntfy_resolved_gets_check_mark_and_no_click_without_link():
    payload = nc.build_ntfy_payload("t", "✅ Resolved: x", "ok", "info")
    assert payload["tags"][0] == "white_check_mark"
    assert "click" not in payload


def test_ntfy_auth_header():
    assert nc.ntfy_auth_header("") == {}
    assert nc.ntfy_auth_header("tk_abc") == {"Authorization": "Bearer tk_abc"}
    assert nc.ntfy_auth_header("bob:pw") == {"Authorization": "Basic Ym9iOnB3"}


def test_build_link():
    assert nc.build_link("", "/incidents/1") is None
    assert nc.build_link("https://ng.example/", "/incidents/1") == "https://ng.example/incidents/1"
    assert nc.build_link("https://ng.example", None) == "https://ng.example"


# ── Senders / error handling (httpx MockTransport) ───────────────────────────

def _recorder(status=200, body="ok", headers=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text=body, headers=headers or {})
    return httpx.MockTransport(handler), seen


async def test_send_teams_posts_the_card():
    transport, seen = _recorder(202, "")
    await nc.send_teams(TEAMS_URL, "t", "m", "critical", transport=transport)
    assert len(seen) == 1
    assert str(seen[0].url) == TEAMS_URL
    body = json.loads(seen[0].content)
    assert body == nc.build_teams_payload("t", "m", "critical")


async def test_send_slack_error_never_leaks_the_webhook_url():
    transport, _ = _recorder(404, f"no_service {SLACK_URL}")
    with pytest.raises(nc.ChannelError) as exc:
        await nc.send_slack(SLACK_URL, "t", "m", "info", transport=transport)
    assert "HTTP 404" in str(exc.value)
    assert "SECRETxyz" not in str(exc.value)


async def test_send_ntfy_posts_json_to_server_root_with_token():
    transport, seen = _recorder(200, "{}")
    await nc.send_ntfy("https://ntfy.example.org/", "alerts", "tk_secret", "t", "m",
                       "warning", "https://ng/x", transport=transport)
    req = seen[0]
    assert str(req.url) == "https://ntfy.example.org"
    assert req.headers["authorization"] == "Bearer tk_secret"
    body = json.loads(req.content)
    assert body["topic"] == "alerts" and body["click"] == "https://ng/x"


async def test_send_ntfy_redacts_token_in_errors():
    transport, _ = _recorder(401, "unauthorized tk_secret")
    with pytest.raises(nc.ChannelError) as exc:
        await nc.send_ntfy("https://ntfy.sh", "alerts", "tk_secret", "t", "m", "info",
                           transport=transport)
    assert "tk_secret" not in str(exc.value)


async def test_send_ntfy_rejects_bad_topic():
    with pytest.raises(nc.ChannelError, match="invalid topic"):
        await nc.send_ntfy("https://ntfy.sh", "a/b", "", "t", "m", "info")


async def test_unsafe_url_is_rejected_before_any_request():
    transport, seen = _recorder()
    with pytest.raises(nc.ChannelError, match="safety check"):
        await nc.send_slack("http://127.0.0.1/hook", "t", "m", "info", transport=transport)
    assert seen == []


async def test_safety_check_is_the_shared_one():
    """Hardening of notifications._is_safe_url must apply here too."""
    transport, seen = _recorder()
    with patch.object(notifications, "_is_safe_url", return_value=False):
        with pytest.raises(nc.ChannelError):
            await nc.send_teams(TEAMS_URL, "t", "m", "info", transport=transport)
    assert seen == []


async def test_timeout_becomes_channel_error():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)
    with pytest.raises(nc.ChannelError, match="timed out"):
        await nc.send_teams(TEAMS_URL, "t", "m", "info", transport=httpx.MockTransport(handler))


async def test_connect_error_does_not_leak_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {TEAMS_URL}", request=request)
    with pytest.raises(nc.ChannelError) as exc:
        await nc.send_teams(TEAMS_URL, "t", "m", "info", transport=httpx.MockTransport(handler))
    assert "sig=SECRET" not in str(exc.value)


async def test_429_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")
    with patch.object(nc.asyncio, "sleep", new=AsyncMock()):
        await nc.send_slack(SLACK_URL, "t", "m", "info", transport=httpx.MockTransport(handler))
    assert calls["n"] == 2


async def test_persistent_429_gives_up():
    transport, seen = _recorder(429, "rate limited", {"Retry-After": "0"})
    with patch.object(nc.asyncio, "sleep", new=AsyncMock()):
        with pytest.raises(nc.ChannelError, match="HTTP 429"):
            await nc.send_slack(SLACK_URL, "t", "m", "info", transport=transport)
    assert len(seen) == nc._MAX_RETRIES + 1


# ── Config + selection ───────────────────────────────────────────────────────

def _cfg(**kw):
    base = dict(
        public_url="https://ng.example",
        teams_enabled=True, teams_webhook_url=TEAMS_URL,
        slack_enabled=True, slack_webhook_url=SLACK_URL,
        ntfy_enabled=True, ntfy_topic="alerts",
    )
    base.update(kw)
    return nc.ExtraChannelConfig(**base)


def _names(sends):
    for _, coro in sends:
        coro.close()  # never awaited in these tests
    return [n for n, _ in sends]


def test_build_sends_all_enabled():
    assert _names(nc.build_sends(_cfg(), "t", "m", "critical")) == ["teams", "slack", "ntfy"]


def test_build_sends_respects_rule_channel_selection():
    sends = nc.build_sends(_cfg(), "t", "m", "critical", channels=["telegram", "slack"])
    assert _names(sends) == ["slack"]


def test_build_sends_skips_disabled_and_unconfigured():
    cfg = _cfg(teams_enabled=False, slack_webhook_url="", ntfy_topic="")
    assert _names(nc.build_sends(cfg, "t", "m", "critical")) == []


def test_build_sends_respects_min_severity():
    cfg = _cfg(teams_min_severity="critical", slack_min_severity="warning")
    assert _names(nc.build_sends(cfg, "t", "m", "warning")) == ["slack", "ntfy"]
    assert _names(nc.build_sends(cfg, "t", "m", "info")) == ["ntfy"]


async def test_load_config_decrypts_secrets():
    from models.base import decrypt_value, encrypt_value
    stored = {
        "public_url": "https://ng.example/",
        "teams_enabled": "1", "teams_webhook_url": encrypt_value(TEAMS_URL),
        "ntfy_enabled": "1", "ntfy_topic": "alerts", "ntfy_token": encrypt_value("tk_x"),
        "slack_webhook_url": "https://legacy-plaintext.example/hook",
    }

    async def get_setting(db, key, default=""):
        return stored.get(key, default)

    cfg = await nc.load_config(None, get_setting, decrypt_value)
    assert cfg.public_url == "https://ng.example"
    assert cfg.teams_enabled and cfg.teams_webhook_url == TEAMS_URL
    assert cfg.ntfy_token == "tk_x"
    assert cfg.ntfy_server_url == nc.DEFAULT_NTFY_SERVER
    assert cfg.slack_webhook_url == "https://legacy-plaintext.example/hook"
    assert not cfg.slack_enabled


# ── Dispatcher integration (notifications.notify) ────────────────────────────

def _patched_notify_env(settings):
    async def fake_get_setting(db, key, default=""):
        return settings.get(key, default)

    mock_cls = patch("database.AsyncSessionLocal")
    return mock_cls, patch("database.get_setting", side_effect=fake_get_setting)


async def _run_notify(settings, **kwargs):
    notifications._recent.clear()
    cls_patch, gs_patch = _patched_notify_env(settings)
    with cls_patch as mock_cls, gs_patch, \
         patch.object(nc, "send_teams", new_callable=AsyncMock) as teams, \
         patch.object(nc, "send_slack", new_callable=AsyncMock) as slack, \
         patch.object(nc, "send_ntfy", new_callable=AsyncMock) as ntfy, \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock) as tg, \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock) as log:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify(**kwargs)
        return {"teams": teams, "slack": slack, "ntfy": ntfy, "telegram": tg, "log": log}


_ALL = {
    "notify_enabled": "1",
    "telegram_bot_token": "1:A", "telegram_chat_id": "2",
    "public_url": "https://ng.example",
    "teams_enabled": "1", "teams_webhook_url": TEAMS_URL,
    "slack_enabled": "1", "slack_webhook_url": SLACK_URL,
    "ntfy_enabled": "1", "ntfy_topic": "alerts",
}


async def test_notify_reaches_new_channels_with_link():
    m = await _run_notify(_ALL, title="Incident A", message="m", severity="critical",
                          link_path="/incidents/7")
    m["teams"].assert_awaited_once_with(TEAMS_URL, "Incident A", "m", "critical",
                                        "https://ng.example/incidents/7")
    m["slack"].assert_awaited_once()
    m["ntfy"].assert_awaited_once()
    logged = sorted(c.args[0] for c in m["log"].await_args_list)
    assert logged == ["ntfy", "slack", "teams", "telegram"]
    assert all(c.args[4] == "sent" for c in m["log"].await_args_list)


async def test_notify_rule_channel_selection():
    m = await _run_notify(_ALL, title="Rule B", message="m", severity="warning",
                          channels=["ntfy"])
    m["ntfy"].assert_awaited_once()
    m["teams"].assert_not_called()
    m["slack"].assert_not_called()
    m["telegram"].assert_not_awaited()  # built, then dropped by the channel filter


async def test_notify_disabled_toggle_skips_channel():
    settings = dict(_ALL, slack_enabled="0")
    m = await _run_notify(settings, title="C", message="m")
    m["slack"].assert_not_called()
    m["teams"].assert_awaited_once()


async def test_notify_failure_is_logged_and_others_still_send():
    notifications._recent.clear()
    cls_patch, gs_patch = _patched_notify_env(_ALL)
    with cls_patch as mock_cls, gs_patch, \
         patch.object(nc, "send_teams", side_effect=nc.ChannelError("Teams: HTTP 400")), \
         patch.object(nc, "send_slack", new_callable=AsyncMock) as slack, \
         patch.object(nc, "send_ntfy", new_callable=AsyncMock), \
         patch.object(notifications, "_send_telegram", new_callable=AsyncMock), \
         patch.object(notifications, "_log_notification", new_callable=AsyncMock) as log:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await notifications.notify("D", "m")
    slack.assert_awaited_once()
    teams_logs = [c for c in log.await_args_list if c.args[0] == "teams"]
    assert teams_logs[0].args[4] == "failed"
    assert teams_logs[0].args[5] == "Teams: HTTP 400"


async def test_notify_rate_limit_covers_new_channels():
    m = await _run_notify(_ALL, title="Same", message="m")
    m["teams"].assert_awaited_once()
    # second call with the same title inside the cooldown: nothing at all
    cls_patch, gs_patch = _patched_notify_env(_ALL)
    with cls_patch, gs_patch, patch.object(nc, "send_teams", new_callable=AsyncMock) as teams:
        await notifications.notify("Same", "m")
    teams.assert_not_called()

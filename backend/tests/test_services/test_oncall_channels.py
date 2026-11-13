"""PagerDuty / Opsgenie / Jira Service Management: the stateful channels.

What sets them apart from Teams/Slack/ntfy is tested here: one stable key per
incident, a resolve that closes what the trigger opened, and a resolve that
is never held back by the channel's severity filter.
"""
import json

import httpx
import pytest

import notification_channels as nc
from tests.test_routers.conftest import make_client

ROUTING_KEY = "R0UTINGKEYSECRET0123456789abcdef"
GENIE_KEY = "11111111-2222-3333-4444-555555555555"
_JSON = {"accept": "application/json"}


def _recorder(status=202, body="{}"):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text=body)
    return httpx.MockTransport(handler), seen


def _cfg(**kw):
    base = dict(
        public_url="https://ng.example",
        pagerduty_enabled=True, pagerduty_routing_key=ROUTING_KEY,
        opsgenie_enabled=True, opsgenie_api_key=GENIE_KEY,
        jsm_enabled=True, jsm_api_key=GENIE_KEY,
    )
    base.update(kw)
    return nc.ExtraChannelConfig(**base)


def _names(sends):
    for _, coro in sends:
        coro.close()  # never awaited in these tests
    return [n for n, _ in sends]


# ── Incident identity ────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("✅ Resolved: Host down: router", True),
    ("Resolved: disk full", True),
    ("resolved : x", True),
    ("🔴 Incident: Host down: router", False),
    ("Issue resolved: later in the title", False),
    ("", False),
])
def test_is_resolve(title, expected):
    assert nc.is_resolve(title) is expected


def test_trigger_and_resolve_of_an_incident_share_one_key():
    trigger = nc.dedup_key("https://ng.example", "/incidents/42", "🔴 Incident: Host down: router")
    resolve = nc.dedup_key("https://ng.example", "/incidents/42", "✅ Resolved: Host down: router")
    assert trigger == resolve
    assert trigger.endswith("-incident-42")
    assert nc.dedup_key("https://ng.example", "/incidents/43", "x") != trigger


def test_key_is_scoped_by_installation():
    """Two installations paging the same service both have an incident 42."""
    a = nc.dedup_key("https://a.example", "/incidents/42", "t")
    b = nc.dedup_key("https://b.example", "/incidents/42", "t")
    assert a != b


def test_without_incident_link_the_title_is_the_identity():
    a = nc.dedup_key("", "/rules", "🔴 Incident: CPU high on web01")
    assert a == nc.dedup_key("", "/rules", "✅ Resolved: CPU high on web01")
    assert a == nc.dedup_key("", None, "cpu HIGH on web01")
    assert a != nc.dedup_key("", "/rules", "CPU high on web02")


# ── Payloads ─────────────────────────────────────────────────────────────────

def test_pagerduty_trigger_payload():
    p = nc.build_pagerduty_payload(ROUTING_KEY, "Host down", "router is down", "critical",
                                   "https://ng.example/incidents/7", key="k1")
    assert p["event_action"] == "trigger" and p["dedup_key"] == "k1"
    assert p["routing_key"] == ROUTING_KEY
    assert p["payload"] == {
        "summary": "Host down", "source": "Nodeglow", "severity": "critical",
        "custom_details": {"message": "router is down"},
    }
    assert p["links"] == [{"href": "https://ng.example/incidents/7", "text": "Open in Nodeglow"}]


@pytest.mark.parametrize("severity,expected", [
    ("critical", "critical"), ("error", "error"), ("warning", "warning"),
    ("info", "info"), ("nonsense", "info"), ("", "info"),
])
def test_pagerduty_severity_is_always_one_the_api_accepts(severity, expected):
    p = nc.build_pagerduty_payload("k", "t", "m", severity, key="k1")
    assert p["payload"]["severity"] == expected


def test_pagerduty_resolve_payload_is_minimal():
    p = nc.build_pagerduty_payload(ROUTING_KEY, "t", "m", "info", key="k1", resolve=True)
    assert p == {"routing_key": ROUTING_KEY, "event_action": "resolve", "dedup_key": "k1"}


def test_ops_alert_payload_respects_the_message_limit():
    p = nc.build_ops_alert_payload("x" * 500, "body", "warning", "https://ng.example/i/1", key="k1")
    assert len(p["message"]) == 130
    assert p["alias"] == "k1" and p["priority"] == "P3"
    assert p["description"].endswith("https://ng.example/i/1")
    assert p["details"] == {"severity": "warning", "link": "https://ng.example/i/1"}


# ── Senders ──────────────────────────────────────────────────────────────────

async def test_pagerduty_trigger_then_resolve():
    transport, seen = _recorder()
    await nc.send_pagerduty(ROUTING_KEY, "🔴 Incident: Host down", "m", "critical",
                            key="k1", transport=transport)
    await nc.send_pagerduty(ROUTING_KEY, "✅ Resolved: Host down", "back", "info",
                            key="k1", transport=transport)
    assert [str(r.url) for r in seen] == [nc.PAGERDUTY_EVENTS_URL] * 2
    first, second = (json.loads(r.content) for r in seen)
    assert (first["event_action"], second["event_action"]) == ("trigger", "resolve")
    assert first["dedup_key"] == second["dedup_key"] == "k1"


async def test_pagerduty_error_never_leaks_the_routing_key():
    transport, _ = _recorder(400, f'{{"message":"invalid routing key {ROUTING_KEY}"}}')
    with pytest.raises(nc.ChannelError) as exc:
        await nc.send_pagerduty(ROUTING_KEY, "t", "m", "critical", key="k1", transport=transport)
    assert "HTTP 400" in str(exc.value)
    assert ROUTING_KEY not in str(exc.value)


async def test_opsgenie_create_and_close_by_alias():
    transport, seen = _recorder()
    base = nc.OPSGENIE_BASES["eu"]
    await nc.send_ops_alert(base, GENIE_KEY, "🔴 Incident: x", "m", "critical",
                            key="nodeglow-ab-incident-7", label="Opsgenie", transport=transport)
    await nc.send_ops_alert(base, GENIE_KEY, "✅ Resolved: x", "back", "info",
                            key="nodeglow-ab-incident-7", label="Opsgenie", transport=transport)
    assert str(seen[0].url) == "https://api.eu.opsgenie.com/v2/alerts"
    assert str(seen[1].url) == ("https://api.eu.opsgenie.com/v2/alerts/"
                                "nodeglow-ab-incident-7/close?identifierType=alias")
    assert all(r.headers["authorization"] == f"GenieKey {GENIE_KEY}" for r in seen)
    assert json.loads(seen[0].content)["alias"] == "nodeglow-ab-incident-7"
    assert GENIE_KEY not in str(seen[0].url) and GENIE_KEY not in str(seen[1].url)


async def test_closing_an_alert_that_does_not_exist_is_not_a_failure():
    """A resolve for an incident whose trigger the severity filter held back."""
    transport, seen = _recorder(404, '{"message":"Alert does not exist"}')
    await nc.send_ops_alert(nc.JSM_BASE, GENIE_KEY, "✅ Resolved: x", "m", "info",
                            key="k1", label="Jira Service Management", transport=transport)
    assert len(seen) == 1


async def test_a_404_on_create_is_a_failure():
    transport, _ = _recorder(404, "not found")
    with pytest.raises(nc.ChannelError, match="HTTP 404"):
        await nc.send_ops_alert(nc.JSM_BASE, GENIE_KEY, "🔴 Incident: x", "m", "critical",
                                key="k1", label="Jira Service Management", transport=transport)


async def test_ops_alert_error_never_leaks_the_api_key():
    transport, _ = _recorder(403, f"key {GENIE_KEY} is not allowed")
    with pytest.raises(nc.ChannelError) as exc:
        await nc.send_ops_alert(nc.JSM_BASE, GENIE_KEY, "t", "m", "critical",
                                key="k1", label="Jira Service Management", transport=transport)
    assert GENIE_KEY not in str(exc.value)


# ── Selection ────────────────────────────────────────────────────────────────

def test_build_sends_includes_the_on_call_channels():
    assert _names(nc.build_sends(_cfg(), "t", "m", "critical")) == ["pagerduty", "opsgenie", "jsm"]


def test_a_resolve_passes_the_severity_filter_a_trigger_does_not():
    cfg = _cfg(pagerduty_min_severity="critical", opsgenie_min_severity="critical",
               jsm_min_severity="critical")
    # an "info" trigger is filtered …
    assert _names(nc.build_sends(cfg, "🔴 Incident: x", "m", "info")) == []
    # … the resolve (always sent as "info") still goes out.
    assert _names(nc.build_sends(cfg, "✅ Resolved: x", "m", "info",
                                 link_path="/incidents/7")) == ["pagerduty", "opsgenie", "jsm"]


def test_the_resolve_bypass_is_limited_to_stateful_channels():
    cfg = _cfg(teams_enabled=True, teams_webhook_url="https://example.com/hook",
               teams_min_severity="critical", pagerduty_min_severity="critical",
               opsgenie_enabled=False, jsm_enabled=False)
    assert _names(nc.build_sends(cfg, "✅ Resolved: x", "m", "info")) == ["pagerduty"]


def test_disabled_or_unconfigured_on_call_channels_are_skipped():
    cfg = _cfg(pagerduty_enabled=False, opsgenie_api_key="", jsm_enabled=False)
    assert _names(nc.build_sends(cfg, "✅ Resolved: x", "m", "info")) == []


def test_unknown_opsgenie_region_fails_loudly():
    with pytest.raises(nc.ChannelError, match="unknown region"):
        nc.make_send(_cfg(opsgenie_region="mars"), "opsgenie", "t", "m", "critical", None)


async def test_the_daily_summary_never_pages():
    results = await nc.send_to_selected(_cfg(), {"pagerduty", "opsgenie", "jsm"}, "Summary", "m")
    assert results == []


# ── Settings endpoints ───────────────────────────────────────────────────────

async def _setting_row(session_factory, key):
    from tests.conftest import setting_row
    async with session_factory() as db:
        return await setting_row(db, key)


async def test_save_encrypts_keys_and_json_only_exposes_flags():
    async with make_client() as (client, sf):
        r = await client.post("/settings/notifications/save", headers=_JSON, data={
            "pagerduty_enabled": "1", "pagerduty_routing_key": ROUTING_KEY,
            "notify_pagerduty_min_severity": "critical",
            "opsgenie_enabled": "1", "opsgenie_region": "EU", "opsgenie_api_key": GENIE_KEY,
        })
        assert r.status_code == 200, r.text

        row = await _setting_row(sf, "pagerduty_routing_key")
        assert row.value and ROUTING_KEY not in row.value  # stored encrypted
        assert GENIE_KEY not in (await _setting_row(sf, "opsgenie_api_key")).value

        s = (await client.get("/settings/json")).json()
        assert s["pagerduty_enabled"] == "1" and s["pagerduty_has_key"] is True
        assert s["notify_pagerduty_min_severity"] == "critical"
        assert s["opsgenie_region"] == "eu" and s["opsgenie_has_key"] is True
        assert s["jsm_enabled"] == "0" and s["jsm_has_key"] is False
        assert "pagerduty_routing_key" not in s and "opsgenie_api_key" not in s

        # A save that does not mention the on-call fields keeps them …
        r = await client.post("/settings/notifications/save", headers=_JSON,
                              data={"teams_enabled": "0"})
        assert r.status_code == 200
        s = (await client.get("/settings/json")).json()
        assert s["pagerduty_enabled"] == "1" and s["pagerduty_has_key"] is True
        assert s["opsgenie_region"] == "eu"

        # … a blank key keeps the stored one, *_clear removes it.
        r = await client.post("/settings/notifications/save", headers=_JSON, data={
            "pagerduty_routing_key": "", "opsgenie_api_key_clear": "1",
        })
        assert r.status_code == 200
        s = (await client.get("/settings/json")).json()
        assert s["pagerduty_has_key"] is True and s["opsgenie_has_key"] is False


async def test_save_rejects_an_unknown_opsgenie_region():
    async with make_client() as (client, _sf):
        r = await client.post("/settings/notifications/save", headers=_JSON,
                              data={"opsgenie_region": "mars"})
        assert r.status_code == 400

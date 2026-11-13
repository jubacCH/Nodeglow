"""Follow-ups to the integrations wave: a configuration is validated when it
is saved, a manual resolve notifies, and Synology errors carry no session id.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from models.incident import Incident
from models.integration import IntegrationConfig
from services.tenancy import tenant_scope
from tests.test_routers.conftest import make_client


class AdminUser:
    id = 1
    username = "admin"
    role = "admin"


PROM = {"name": "node", "url": "https://203.0.113.10:9100/metrics", "verify_ssl": True}


async def _configs(sf):
    async with sf() as db:
        with tenant_scope(1):
            return (await db.execute(select(IntegrationConfig))).scalars().all()


# ── validate_config on save ──────────────────────────────────────────────────

async def test_a_malformed_rule_is_refused_when_the_config_is_saved():
    async with make_client(fake_user=AdminUser()) as (client, sf):
        r = await client.post("/api/integration/prometheus/create", json={
            **PROM, "rules": "critical up == 0\nthis is not a rule\nwarning up >> 1"})
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["code"] == "invalid_config"
        assert len(body["problems"]) == 2
        assert all("line" in p.lower() for p in body["problems"])
        assert await _configs(sf) == []          # nothing was stored


async def test_a_valid_config_is_saved_and_an_edit_is_validated_too():
    async with make_client(fake_user=AdminUser()) as (client, sf):
        r = await client.post("/api/integration/prometheus/create", json={
            **PROM, "rules": "critical up == 0 # exporter down"})
        assert r.status_code == 200, r.text
        cfg_id = r.json()["id"]

        r = await client.patch(f"/api/integration/prometheus/{cfg_id}",
                               json={"rules": "critical up === 0"})
        assert r.status_code == 400 and r.json()["code"] == "invalid_config"

        from services import integration as int_svc
        async with sf() as db:
            with tenant_scope(1):
                cfg = await int_svc.get_config(db, cfg_id)
                stored = await int_svc.decrypt_config(db, cfg)
        assert stored["rules"] == "critical up == 0 # exporter down"   # the bad edit changed nothing


async def test_integrations_without_a_validator_save_as_before():
    async with make_client(fake_user=AdminUser()) as (client, _sf):
        r = await client.post("/api/integration/gitea/create", json={
            "name": "git", "host": "https://203.0.113.11", "verify_ssl": True})
        assert r.status_code == 200, r.text


async def test_a_validator_that_raises_does_not_block_the_save():
    from integrations.prometheus import PrometheusIntegration

    async with make_client(fake_user=AdminUser()) as (client, _sf):
        with patch.object(PrometheusIntegration, "validate_config", side_effect=RuntimeError("boom")):
            r = await client.post("/api/integration/prometheus/create", json={**PROM, "rules": ""})
        assert r.status_code == 200, r.text


# ── Manual resolve notifies ──────────────────────────────────────────────────

async def _incident(sf, status="open") -> int:
    async with sf() as db:
        with tenant_scope(1):
            inc = Incident(rule="multi_host_down", title="Three hosts down",
                           severity="critical", status=status, host_ids_hash="h")
            db.add(inc)
            await db.commit()
            return inc.id


async def test_manual_resolve_sends_the_resolve_notification():
    """Otherwise an on-call channel keeps its alert open after the incident
    was closed by hand."""
    async with make_client() as (client, sf):
        incident_id = await _incident(sf)
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            r = await client.post(f"/api/v1/incidents/{incident_id}/resolve")
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        notify.assert_awaited_once()
        args, kwargs = notify.await_args
        assert args[0] == "✅ Resolved: Three hosts down"
        assert kwargs["link_path"] == f"/incidents/{incident_id}"

        # The title and link are what the stateful channels key on.
        import notification_channels as nc
        assert nc.is_resolve(args[0])
        assert nc.dedup_key("https://ng.example", kwargs["link_path"], args[0]) == \
            nc.dedup_key("https://ng.example", f"/incidents/{incident_id}", "🔴 Incident: Three hosts down")


async def test_resolving_twice_notifies_once():
    async with make_client() as (client, sf):
        incident_id = await _incident(sf)
        with patch("notifications.notify", new_callable=AsyncMock) as notify:
            await client.post(f"/api/v1/incidents/{incident_id}/resolve")
            r = await client.post(f"/api/v1/incidents/{incident_id}/resolve")
        assert r.status_code == 200
        assert notify.await_count == 1


async def test_a_failing_notification_does_not_fail_the_resolve():
    async with make_client() as (client, sf):
        incident_id = await _incident(sf)
        with patch("notifications.notify", new_callable=AsyncMock, side_effect=RuntimeError("smtp")):
            r = await client.post(f"/api/v1/incidents/{incident_id}/resolve")
        assert r.status_code == 200
        async with sf() as db:
            with tenant_scope(1):
                assert (await db.get(Incident, incident_id)).status == "resolved"


# ── Synology: the session id stays out of errors ─────────────────────────────

SID = "SECRETSESSIONID123"


def _synology_client(monkeypatch, entry_status=200, entry_body=None):
    import integrations.synology as syn

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth.cgi"):
            if request.method == "POST":
                return httpx.Response(200, json={"success": True, "data": {"sid": SID}})
            return httpx.Response(200, json={"success": True})
        if entry_body is not None:
            return httpx.Response(entry_status, text=entry_body)
        return httpx.Response(entry_status, json={"success": True, "data": {}})

    monkeypatch.setattr(syn, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return syn.SynologyIntegration(config={
        "host": "https://203.0.113.20", "port": 5001, "username": "u", "password": "p"})


@pytest.mark.parametrize("status,body", [(500, None), (403, None), (200, "<html>not json</html>")])
async def test_synology_error_text_has_no_session_id(monkeypatch, status, body):
    plugin = _synology_client(monkeypatch, entry_status=status, entry_body=body)
    result = await plugin.collect()
    assert result.success is False
    assert result.error and SID not in result.error
    assert "_sid" not in result.error
    assert "SYNO." in result.error      # still says which call failed

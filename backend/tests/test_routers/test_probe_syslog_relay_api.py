"""Probe syslog relay through the HTTP surface (multi-tenancy phase 10).

A probe forwards syslog it received inside its customer's network to
``POST /api/agent/logs`` (entries with ``raw`` and ``source_ip``). Tenant and
site come from the probe's token — a probe of tenant A can never write into
tenant B, whatever its entries claim — and the device is resolved inside the
probe's site. The heartbeat carries the relay's config out and its counters
back; the agent API switches it on (admin, probes only).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models import PingHost, Site, Tenant
from models.agent import Agent
from services import syslog
from services.tenancy import system_scope
from tests.test_routers.conftest import make_client

A, B = 1, 2
TOKENS = {"probe-a": "relay-probe-a-token", "probe-b": "relay-probe-b-token",
          "plain-a": "relay-plain-a-token"}


async def _seed(sf) -> dict:
    """Tenant B with site 2; in both tenants a host at 192.168.1.1 and a probe."""
    from routers.agents import _hash_agent_token

    async with sf() as s:
        with system_scope("test: seed relay tenants", audit=False):
            s.add(Tenant(id=B, provider_id=1, name="CANARY-B-tenant", slug="b", status="active"))
            await s.flush()
            s.add(Site(id=2, tenant_id=B, name="B"))
            await s.flush()
            s.add(PingHost(id=100, tenant_id=A, name="gw-a", hostname="192.168.1.1", site_id=1))
            s.add(PingHost(id=101, tenant_id=A, name="plain-a", hostname="plain-a.example",
                           site_id=1, source="agent"))
            s.add(PingHost(id=900, tenant_id=B, name="CANARY-B-gw", hostname="192.168.1.1",
                           site_id=2))
            s.add(PingHost(id=901, tenant_id=B, name="CANARY-B-srv", hostname="10.0.0.50",
                           site_id=2))
            s.add(Agent(id=10, tenant_id=A, site_id=1, name="probe-a", hostname="probe-a",
                        token=_hash_agent_token(TOKENS["probe-a"]), is_probe=True,
                        syslog_relay_enabled=True))
            s.add(Agent(id=20, tenant_id=B, site_id=2, name="probe-b", hostname="probe-b",
                        token=_hash_agent_token(TOKENS["probe-b"]), is_probe=True,
                        syslog_relay_enabled=True, syslog_relay_port=1514))
            s.add(Agent(id=11, tenant_id=A, site_id=1, name="plain-a", hostname="plain-a.example",
                        token=_hash_agent_token(TOKENS["plain-a"])))
            await s.commit()
    return {}


@asynccontextmanager
async def _env():
    async with make_client() as (client, sf):
        await _seed(sf)

        @asynccontextmanager
        async def factory():
            async with sf() as s:
                yield s

        captured: list[dict] = []

        async def capture(parsed):
            captured.append(dict(parsed))

        with patch.object(syslog, "AsyncSessionLocal", factory), \
             patch.object(syslog, "_resolve_many", new=AsyncMock(return_value={})), \
             patch("services.tenancy.SingleTenancy.active_tenant_ids",
                   new=AsyncMock(return_value=[A, B])), \
             patch.object(syslog, "_enqueue", side_effect=capture):
            syslog._host_cache_ts = 0.0
            yield client, sf, captured


def _relay(entries, who="probe-a", client=None, hostname=None):
    return client.post("/api/agent/logs", json={"hostname": hostname or who, "logs": entries},
                       headers={"Authorization": f"Bearer {TOKENS[who]}"})


CLAIMS_B = {"tenant_id": B, "site_id": 2, "_tenant_id": B, "host_id": 900, "site": 2}


async def test_a_probe_of_a_never_writes_into_b(caplog):
    caplog.set_level("WARNING")
    async with _env() as (client, sf, captured):
        entries = [
            {"raw": "<13>Oct 11 10:00:00 CANARY-B-gw sshd: hello", "source_ip": "192.168.1.1",
             "received_at": "2026-10-11T10:00:01Z", **CLAIMS_B},
            {"raw": "<11>Oct 11 10:00:02 srv kernel: oops", "source_ip": "10.0.0.50", **CLAIMS_B},
        ]
        resp = await _relay(entries, client=client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 2
        assert [m[syslog.TENANT_KEY] for m in captured] == [A, A]
        assert [m["site_id"] for m in captured] == [1, 1]
        # A's own device at the shared address; B's names and ids mean nothing here.
        assert captured[0]["host_id"] == 100
        assert captured[1]["host_id"] is None  # 10.0.0.50 is a device of B only
        assert all(m["source_ip"] in ("192.168.1.1", "10.0.0.50") for m in captured)
    # The host cache refreshed for real (no "Failed to refresh" warning).
    assert [r.getMessage() for r in caplog.records if r.levelno >= 30] == []


async def test_a_probe_of_b_lands_in_b_and_its_site():
    async with _env() as (client, sf, captured):
        resp = await _relay([{"raw": "<13>Oct 11 10:00:00 gw sshd: hello",
                              "source_ip": "192.168.1.1", "tenant_id": A, "site_id": 1}],
                            who="probe-b", client=client)
        assert resp.status_code == 200, resp.text
        assert [(m[syslog.TENANT_KEY], m["site_id"], m["host_id"]) for m in captured] == [(B, 2, 900)]


async def test_a_plain_agent_cannot_relay_for_other_devices(caplog):
    caplog.set_level("WARNING")
    async with _env() as (client, sf, captured):
        resp = await _relay([{"raw": "<13>spoofed", "source_ip": "192.168.1.1"}],
                            who="plain-a", client=client, hostname="plain-a.example")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "count": 0, "dropped": 1}
        assert captured == []
    assert any("relay_not_a_probe" in r.getMessage() for r in caplog.records)


async def test_invalid_relay_entries_are_dropped_not_guessed():
    async with _env() as (client, sf, captured):
        resp = await _relay([{"raw": "<13>a", "source_ip": "gw.example"},
                             {"raw": "<13>b"}, "not-an-object",
                             {"raw": "<13>c", "source_ip": "192.168.1.1"}], client=client)
        assert resp.json()["count"] == 1 and resp.json()["dropped"] == 3
        assert [m["message"] for m in captured] == ["c"]


async def test_an_agents_own_logs_go_to_its_own_host_not_by_address():
    async with _env() as (client, sf, captured):
        # The request comes from 127.0.0.1 (a NAT address in real life): the
        # agent's host is found by its name inside its tenant and site.
        resp = await _relay([{"timestamp": "2026-10-11T10:00:00Z", "severity": 3,
                              "app_name": "nginx", "message": "own log"}],
                            who="plain-a", client=client, hostname="plain-a.example")
        assert resp.status_code == 200
        assert [(m[syslog.TENANT_KEY], m["host_id"], m["site_id"]) for m in captured] == [(A, 101, 1)]

        captured.clear()
        # The probe's own logs: likewise; "192.168.1.1" as its name is not a
        # device of a foreign tenant either.
        resp = await _relay([{"message": "probe log", "severity": 4}], client=client,
                            hostname="CANARY-B-gw")
        assert [(m[syslog.TENANT_KEY], m["host_id"]) for m in captured] == [(A, None)]


async def test_relayed_rows_reach_clickhouse_stamped_with_the_probes_tenant_and_site():
    async with make_client() as (client, sf):
        await _seed(sf)

        @asynccontextmanager
        async def factory():
            async with sf() as s:
                yield s

        saved = syslog._buffer
        syslog._buffer = []
        try:
            with patch.object(syslog, "AsyncSessionLocal", factory), \
                 patch.object(syslog, "_resolve_many", new=AsyncMock(return_value={})), \
                 patch("services.clickhouse_client.insert_batch", new_callable=AsyncMock) as ins:
                syslog._host_cache_ts = 0.0
                for who in ("probe-a", "probe-b"):
                    resp = await _relay([{"raw": f"<13>Oct 11 10:00:00 gw app: from {who}",
                                          "source_ip": "192.168.1.1", **CLAIMS_B}],
                                        who=who, client=client)
                    assert resp.status_code == 200
                await syslog._flush_buffer()
            rows = [r for call in ins.call_args_list for r in call.args[0]]
        finally:
            syslog._buffer = saved
        by_msg = {r["message"]: r for r in rows}
        assert (by_msg["from probe-a"]["tenant_id"], by_msg["from probe-a"]["site_id"],
                by_msg["from probe-a"]["host_id"]) == (A, 1, 100)
        assert (by_msg["from probe-b"]["tenant_id"], by_msg["from probe-b"]["site_id"],
                by_msg["from probe-b"]["host_id"]) == (B, 2, 900)
        assert all(syslog.TENANT_KEY not in r and syslog.RELAYED_KEY not in r for r in rows)


# ── heartbeat: config out, counters back ────────────────────────────────────

async def _report(client, who, **extra):
    body = {"hostname": who, "platform": "linux", "arch": "x86_64", "cpu_pct": 1.0, **extra}
    return await client.post("/api/agent/report", json=body,
                             headers={"Authorization": f"Bearer {TOKENS[who]}"})


async def test_heartbeat_carries_the_relay_config_to_probes_only():
    async with _env() as (client, sf, _):
        a = (await _report(client, "probe-a")).json()
        assert a["config"]["syslog_relay"] == {"enabled": True, "port": 514}
        b = (await _report(client, "probe-b")).json()
        assert b["config"]["syslog_relay"] == {"enabled": True, "port": 1514}
        plain = (await _report(client, "plain-a")).json()
        assert "syslog_relay" not in plain["config"]


async def test_heartbeat_stores_only_sane_relay_counters():
    async with _env() as (client, sf, _):
        resp = await _report(client, "probe-a", syslog_relay={
            "listening": True, "port": 514, "received": 12, "forwarded": 10, "dropped": 2,
            "buffered": -5, "send_failures": "many", "error": "x" * 500, "evil": {"a": 1},
        })
        assert resp.status_code == 200
        await _report(client, "plain-a", syslog_relay={"received": 1})
        async with sf() as s:
            with system_scope("test: read agents"):
                agents = {a.name: a for a in (await s.execute(select(Agent))).scalars()}
        status = json.loads(agents["probe-a"].syslog_relay_status)
        assert status == {"listening": True, "port": 514, "received": 12, "forwarded": 10,
                          "dropped": 2, "buffered": 0, "error": "x" * 300}
        assert agents["plain-a"].syslog_relay_status is None

        detail = (await client.get("/api/v1/agents/10")).json()
        assert detail["syslog_relay"]["enabled"] is True
        assert detail["syslog_relay"]["status"]["received"] == 12
        assert (await client.get("/api/v1/agents/11")).json()["syslog_relay"] is None


# ── agent API: switching the relay ──────────────────────────────────────────

async def test_relay_is_switched_on_for_probes_only_and_audited():
    async with _env() as (client, sf, _):
        r = await client.patch("/api/v1/agents/11", json={"syslog_relay_enabled": True})
        assert r.status_code == 400
        r = await client.patch("/api/v1/agents/11", json={"is_probe": True,
                                                          "syslog_relay_enabled": True,
                                                          "syslog_relay_port": 5514})
        assert r.status_code == 200, r.text
        assert r.json()["syslog_relay"]["enabled"] is True
        assert r.json()["syslog_relay"]["port"] == 5514
        for bad in (0, 70000, "514", True):
            r = await client.patch("/api/v1/agents/11", json={"syslog_relay_port": bad})
            assert r.status_code == 400, bad
        r = await client.patch("/api/v1/agents/11", json={"syslog_relay_enabled": "yes"})
        assert r.status_code == 400
        # Dropping the probe role switches the relay off with it.
        r = await client.patch("/api/v1/agents/11", json={"is_probe": False})
        assert r.status_code == 200
        async with sf() as s:
            agent = await s.get(Agent, 11)
            assert agent.syslog_relay_enabled is False
            from models import AuditLog
            actions = [(a.action, json.loads(a.details or "{}")) for a in
                       (await s.execute(select(AuditLog).where(AuditLog.action == "agent.probe")))
                       .scalars()]
        assert any("syslog_relay_enabled" in d.get("changes", {}) for _, d in actions)


async def test_relay_switch_needs_an_admin():
    class Editor:
        id = 1
        username = "editor"
        role = "editor"

    async with make_client(fake_user=Editor()) as (client, sf):
        await _seed(sf)
        r = await client.patch("/api/v1/agents/10", json={"syslog_relay_enabled": False})
        assert r.status_code == 403


async def test_a_foreign_probe_cannot_be_switched():
    async with _env() as (client, sf, _):
        r = await client.patch("/api/v1/agents/20", json={"syslog_relay_enabled": False})
        assert r.status_code == 404
        async with sf() as s:
            with system_scope("test: read B's probe"):
                assert (await s.get(Agent, 20)).syslog_relay_enabled is True

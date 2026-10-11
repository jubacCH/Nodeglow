"""Job isolation (multi-tenancy phase 8): every scheduled job, with data in
tenants A and B, keeps what it writes, sends and asks the AI in its tenant.

Both tenants hold one row of every tenant-owned table (the seeds of the route
suite, ``CANARY-A-…`` / ``CANARY-B-…``) plus triggers that make the jobs act:
an alert rule that fires, hosts that are down, a Proxmox cluster called
``pve`` in both, a failed backup. Each job runs for both tenants, then:

* every tenant-owned row mentioning a tenant's canary belongs to that tenant
  (incidents, events, notification logs, templates, baselines, snapshots…);
* every message sent to tenant A's channel (its own Telegram chat) mentions
  nothing of B, and the other way round; every AI prompt likewise;
* the job raised nothing and logged no ERROR.

A job missing from ``JOBS`` fails ``test_every_job_is_covered``. ClickHouse
is a recording fake: ``test_job_clickhouse_calls_are_tenant_scoped`` runs as
``xfail(strict=True, reason="phase 6")`` for the jobs whose queries do not
carry the tenant yet (``PHASE6_CH_JOBS``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import encrypt_value
from models.base import Base
from models.settings import Setting
from models.tenant import Provider, Site, Tenant, TenantMembership, TenantSetting
from services.tenancy import system_scope, tenant_tables
from tests.test_routers.test_tenant_isolation import (
    BASE, TAG, FakeClickHouse, _model_for, _seeds, ch_call_is_scoped,
)

A, B = 1, 2


# Every per-tenant job of scheduler.py. The ee daily AI summary has the same
# test in ee/backend/tests/test_daily_summary_isolation.py.
JOBS = [
    "run_ping_checks", "run_integration_checks", "run_correlation", "run_alert_rules",
    "run_log_intelligence", "run_log_analytics", "run_snmp_polls", "run_port_discovery",
    "run_backup_compliance", "resolve_host_dns", "cleanup_old_results", "update_ssl_expiry",
    "run_self_check_job", "refresh_disk_predictions", "run_weekly_digest", "check_disk_space",
]
# Installation jobs: no tenant data (system scope by design).
INSTALLATION_JOBS = {"cleanup_clickhouse_logs", "cleanup_legacy_api_keys", "update_geoip"}

# Jobs whose ClickHouse queries do not carry the tenant yet (phase 6).
PHASE6_CH_JOBS = {
    "run_ping_checks", "run_correlation", "run_alert_rules", "run_log_analytics",
    "run_self_check_job", "refresh_disk_predictions", "run_weekly_digest",
}


def test_every_job_is_covered():
    import scheduler
    from tests.test_services.test_scheduler_jobs_smoke import JOBS as SCHEDULED

    missing = set(SCHEDULED) - set(JOBS) - INSTALLATION_JOBS
    assert not missing, f"jobs without an isolation test: {sorted(missing)}"
    for name in INSTALLATION_JOBS:
        assert getattr(scheduler, name).__tenancy__ == "system", name


# ── Environment ──────────────────────────────────────────────────────────────

@pytest.fixture
async def sf():
    from database import Base as DbBase
    from models.settings import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(DbBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        with system_scope("test: seed two tenants", audit=False):
            s.add(Provider(id=1, name="P", slug="p"))
            s.add_all([Tenant(id=A, provider_id=1, name="A", slug="a"),
                       Tenant(id=B, provider_id=1, name="B", slug="b")])
            s.add_all([Site(id=1, tenant_id=A, name="A"), Site(id=2, tenant_id=B, name="B")])
            for tid in (A, B):
                s.add(User(id=BASE[tid], username=f"u{tid}", role="admin", password_hash="x"))
            await s.flush()
            for tid in (A, B):
                s.add(TenantMembership(user_id=BASE[tid], tenant_id=tid, role="admin"))
                for table, rows in _seeds(tid).items():
                    model = _model_for(table)
                    for row in rows:
                        await s.merge(model(tenant_id=tid, **row))
                    await s.flush()
                for key, value in _triggers(tid).items():
                    await s.merge(TenantSetting(tenant_id=tid, key=key, value=value))
            # The installation's AI provider: postmortems and summaries run
            # (against the fake completion below) instead of logging errors.
            s.add(Setting(key="claude_api_key", value=encrypt_value("sk-test"), encrypted=True))
            await s.commit()

    @asynccontextmanager
    async def factory():
        async with maker() as session:
            yield session

    factory.maker = maker
    yield factory
    await engine.dispose()


def _triggers(tid: int) -> dict[str, str]:
    """Tenant settings that make the jobs act and report."""
    return {
        "notify_enabled": "1", "notify_grace_minutes": "0",
        "telegram_bot_token": f"bot-{tid}", "telegram_chat_id": f"chat-{tid}",
        "digest_enabled": "1", "smtp_host": "smtp", "smtp_user": "u", "smtp_password": "p",
        "smtp_to": f"to-{tid}@example.org",
        "daily_ai_summary_enabled": "1", "daily_ai_summary_channels": "telegram",
        "ai_enabled": "1",
    }


class Outbox:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []   # (channel address, text)
        self.prompts: list[tuple[int | None, str]] = []


@pytest.fixture
def boundaries(sf):
    """Fake only what leaves the process, record what is sent."""
    import notifications
    from services import tenancy

    out = Outbox()
    fake_ch = FakeClickHouse()

    async def telegram(token, chat, text):
        out.sent.append((chat, text))

    async def email(host, port, user, pw, from_addr, to_addr, subject, text, html):
        out.sent.append((to_addr, f"{subject}\n{text}\n{html}"))

    async def completion(system_prompt=None, user_message=None, *a, **kw):
        prompt = str(user_message)
        ctx = tenancy.current()
        out.prompts.append((ctx.tenant_id if ctx else None, prompt))
        return ("summary", {"input_tokens": 1, "output_tokens": 1}) if kw.get("return_usage") \
            else "summary"

    class Result(tuple):
        errors = {}

    async def host_down(host):
        return Result((False, False, None, {"icmp": False}))

    from integrations.proxmox import ProxmoxIntegration

    class FakeProxmox(ProxmoxIntegration):
        """Both tenants' clusters are called "pve"."""

        async def collect(self):
            from integrations._base import CollectorResult
            return CollectorResult(success=True, data={"cluster_name": "pve", "nodes": []})

        async def on_snapshot(self, data, config, db):
            return None

    holders = [m for m in list(sys.modules.values())
               if m is not None and getattr(m, "__name__", "").startswith(
                   ("services.", "routers.", "models", "database", "scheduler", "notifications",
                    "nodeglow_ee")) and hasattr(m, "AsyncSessionLocal")]
    with ExitStack() as stack:
        for mod in holders:
            stack.enter_context(patch.object(mod, "AsyncSessionLocal", sf))
        stack.enter_context(patch("services.tenancy.SingleTenancy.active_tenant_ids",
                                  new=AsyncMock(return_value=[A, B])))
        stack.enter_context(patch("services.clickhouse_client.get_client",
                                  new=AsyncMock(return_value=fake_ch)))
        stack.enter_context(patch.object(notifications, "_send_telegram", telegram))
        stack.enter_context(patch.object(notifications, "_send_email", email))
        from services import ai_client

        try:  # bound at import time there; load it now so the patch reaches it
            import nodeglow_ee.ai.postmortem  # noqa: F401
        except ImportError:
            pass
        real_completion = ai_client.generate_completion
        stack.enter_context(patch.object(ai_client, "generate_completion", completion))
        for mod in [m for m in list(sys.modules.values()) if m is not None]:
            if getattr(mod, "generate_completion", None) is real_completion:
                stack.enter_context(patch.object(mod, "generate_completion", completion))
        # Nothing may leave the process, whatever a job imports late.
        stack.enter_context(patch("httpx.AsyncHTTPTransport.handle_async_request",
                                  side_effect=RuntimeError("network blocked in tests")))
        stack.enter_context(patch("utils.ping.check_host", host_down))
        stack.enter_context(patch("utils.ping.get_ssl_expiry_days", AsyncMock(return_value=10)))
        stack.enter_context(patch("socket.gethostbyaddr", side_effect=OSError("no network")))
        stack.enter_context(patch("socket.gethostbyname", side_effect=OSError("no network")))
        stack.enter_context(patch("integrations.get_registry", return_value={"proxmox": FakeProxmox}))
        stack.enter_context(patch("services.snmp.poll_host", AsyncMock(return_value=None)))
        stack.enter_context(patch("services.port_discovery.scan_host_ports",
                                  AsyncMock(return_value=[]), create=True))
        notifications._recent.clear()
        out.ch = fake_ch
        yield out
        notifications._recent.clear()
    import scheduler

    scheduler._offline_since.clear()
    scheduler._grace_notified.clear()
    scheduler._inflight_cfgs.clear()


async def _run(job_name: str):
    import scheduler

    if job_name == "daily_ai_summary":  # ee (ee/backend/tests)
        from nodeglow_ee import license_runtime
        from nodeglow_ee.ai import daily_summary as ee

        with patch.object(license_runtime, "is_active", AsyncMock(return_value=True)), \
                patch("services.ai_config.load_ai_config", AsyncMock(return_value=_AiCfg())), \
                patch.object(ee, "load_ai_config", AsyncMock(return_value=_AiCfg()), create=True):
            await ee.run_daily_ai_summary(force=True)
        await _settle()
        return
    if job_name == "run_weekly_digest":
        await scheduler.run_weekly_digest(force=True)
    elif job_name == "check_disk_space":
        await scheduler._disk_space_incident(95.0, 1.0)
    else:
        await getattr(scheduler, job_name)()
    await _settle()


async def _settle():
    """Let the tasks a job spawned (notifications, broadcasts) finish."""
    for _ in range(5):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                   and not t.done()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=2)


class _AiCfg:
    enabled = True
    configured = True


async def _rows_by_tenant(sf) -> list[tuple[str, int | None, str]]:
    out = []
    async with sf.maker() as s:
        with system_scope("test: read every tenant", audit=False):
            for t in tenant_tables():
                for row in (await s.execute(select(t))).mappings():
                    out.append((t.name, row.get("tenant_id"), json.dumps(
                        {k: v for k, v in row.items()}, default=str)))
    return out


def _foreign_tag(tid: int) -> str:
    return TAG[B if tid == A else A]


@pytest.mark.parametrize("job_name", JOBS)
async def test_job_keeps_each_tenants_data_in_its_tenant(job_name, sf, boundaries, caplog):
    await assert_job_isolated(job_name, sf, boundaries, caplog)


async def assert_job_isolated(job_name, sf, boundaries, caplog):
    with caplog.at_level(logging.ERROR):
        await _run(job_name)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"{job_name} logged errors: {errors}"

    for table, tid, text in await _rows_by_tenant(sf):
        if tid is None:
            continue
        assert _foreign_tag(tid) not in text, f"{job_name}: {table} row of tenant {tid} holds " \
                                              f"the other tenant's data: {text[:300]}"
    for address, text in boundaries.sent:
        tid = A if address.endswith(("-1", "-1@example.org")) else B
        assert _foreign_tag(tid) not in text, f"{job_name}: message to {address} mentions the " \
                                              f"other tenant: {text[:300]}"
    for tid, prompt in boundaries.prompts:
        assert tid in (A, B), f"{job_name}: AI prompt built outside a tenant"
        assert _foreign_tag(tid) not in prompt, f"{job_name}: AI prompt of tenant {tid} " \
                                                f"mentions the other tenant"


async def test_alert_rules_fire_in_both_tenants_to_their_own_channel(sf, boundaries):
    await _run("run_alert_rules")
    await _run("run_alert_rules")  # required_consecutive defaults to 2
    to = {addr: text for addr, text in boundaries.sent}
    assert "CANARY-A-alert_rules" in to.get("chat-1", "")
    assert "CANARY-B-alert_rules" in to.get("chat-2", "")


async def test_hosts_down_in_both_tenants_notify_both(sf, boundaries):
    await _run("run_ping_checks")
    chats = {addr for addr, _ in boundaries.sent}
    assert {"chat-1", "chat-2"} <= chats, boundaries.sent


async def test_same_cluster_name_in_both_tenants_collects_both(sf, boundaries):
    """Two customers' Proxmox clusters named "pve": both are primaries of
    their own tenant, neither is the other's standby."""
    from models.integration import Snapshot

    await _run("run_integration_checks")
    async with sf.maker() as s:
        with system_scope("test: read snapshots", audit=False):
            snaps = (await s.execute(select(Snapshot).where(
                Snapshot.timestamp > datetime.utcnow() - timedelta(minutes=5),
                Snapshot.entity_id.in_([BASE[A], BASE[B]])).order_by(Snapshot.id))).scalars().all()
    fresh = {(s.tenant_id, s.entity_id): s for s in snaps if s.id not in (BASE[A], BASE[B])}
    assert set(fresh) == {(A, BASE[A]), (B, BASE[B])}, fresh
    assert all(s.ok and not s.error for s in fresh.values()), [s.error for s in fresh.values()]


async def test_disk_incident_is_raised_in_every_tenant(sf, boundaries):
    from models.incident import Incident

    await _run("check_disk_space")
    async with sf.maker() as s:
        with system_scope("test: read incidents", audit=False):
            tenants = (await s.execute(select(Incident.tenant_id).where(
                Incident.rule == "disk_space"))).scalars().all()
    assert sorted(tenants) == [A, B]


async def test_digest_goes_to_each_tenants_recipients(sf, boundaries):
    await _run("run_weekly_digest")
    assert {addr for addr, _ in boundaries.sent} == {"to-1@example.org", "to-2@example.org"}


# ── ClickHouse (phase 6) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("job_name", sorted(set(JOBS) - PHASE6_CH_JOBS))
async def test_job_clickhouse_calls_are_tenant_scoped(job_name, sf, boundaries):
    await _run(job_name)
    assert_ch_scoped(job_name, boundaries)


@pytest.mark.parametrize("job_name", sorted(PHASE6_CH_JOBS))
@pytest.mark.xfail(strict=True, reason="phase 6")
async def test_phase6_job_clickhouse_calls_are_tenant_scoped(job_name, sf, boundaries):
    await _run(job_name)
    assert_ch_scoped(job_name, boundaries)


def assert_ch_scoped(job_name, boundaries):
    calls = boundaries.ch.calls
    # Every call must name the tenant it ran for; with two tenants we accept
    # either id but require one of them.
    bad = [c for c in calls if not (ch_call_is_scoped(c, A) or ch_call_is_scoped(c, B))]
    assert not bad, f"{job_name}: unscoped ClickHouse calls: " + \
        "; ".join((c.sql or f"insert {c.table}").strip()[:100] for c in bad[:5])

"""Tests for the scheduled ping check job.

Regression cover for the ClickHouse write path: ``run_ping_checks`` imported
``insert_ping_checks`` inside the ``if agent_hosts:`` branch but called it again
further down for the ICMP rows. On a fleet with no agent-sourced hosts that
branch never runs, so the name stayed unbound and every ICMP result was lost
with only a log line to show for it.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakePingHost:
    def __init__(self, host_id: int, name: str, source: str = "icmp"):
        self.id = host_id
        self.name = name
        self.source = source
        self.enabled = True
        self.maintenance = False
        self.maintenance_until = None
        self.port_error = False
        self.check_detail = None
        self.port_error_streak = 0
        self.port_ok_streak = 0
        self.check_port = None
        self.notify = False


def _session_factory(hosts):
    """Fake AsyncSessionLocal yielding a session that serves `hosts`."""

    def make_session():
        session = AsyncMock()
        scalars = MagicMock()
        scalars.all.return_value = hosts
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars
        session.execute = AsyncMock(return_value=execute_result)
        session.commit = AsyncMock()
        session.get = AsyncMock(side_effect=lambda model, hid: next(
            (h for h in hosts if h.id == hid), None
        ))
        return session

    @asynccontextmanager
    async def factory():
        yield make_session()

    return factory


@pytest.fixture
def icmp_only_hosts():
    """Two ICMP hosts and no agent-sourced host — the production shape."""
    return [FakePingHost(1, "gw"), FakePingHost(2, "nas")]


async def test_icmp_results_reach_clickhouse_without_agent_hosts(icmp_only_hosts):
    """The ICMP rows must be inserted even though no agent host exists.

    This is the exact production condition: every ping host is ICMP-sourced,
    so the agent branch is skipped entirely.
    """
    import scheduler

    insert_mock = AsyncMock()

    async def fake_check_host(host):
        return True, False, 12.5, {"icmp": True}

    with patch.object(scheduler, "AsyncSessionLocal", _session_factory(icmp_only_hosts)), \
         patch("utils.ping.check_host", new=fake_check_host), \
         patch("services.clickhouse_client.insert_ping_checks", new=insert_mock), \
         patch("services.clickhouse_client.get_latest_ping_per_host",
               new=AsyncMock(return_value={})):
        await scheduler.run_ping_checks()

    assert insert_mock.await_count == 1, (
        "ICMP rows were never handed to ClickHouse"
    )
    rows = insert_mock.await_args.args[0]
    assert {r["host_id"] for r in rows} == {1, 2}
    assert all(r["success"] is True for r in rows)
    assert all(r["latency_ms"] == 12.5 for r in rows)


async def test_ping_job_reports_failures_as_rows(icmp_only_hosts):
    """A host that does not answer is still recorded, with success=False."""
    import scheduler

    insert_mock = AsyncMock()

    async def fake_check_host(host):
        if host.id == 2:
            return False, False, None, {"icmp": False}
        return True, False, 5.0, {"icmp": True}

    with patch.object(scheduler, "AsyncSessionLocal", _session_factory(icmp_only_hosts)), \
         patch("utils.ping.check_host", new=fake_check_host), \
         patch("services.clickhouse_client.insert_ping_checks", new=insert_mock), \
         patch("services.clickhouse_client.get_latest_ping_per_host",
               new=AsyncMock(return_value={})):
        await scheduler.run_ping_checks()

    rows = {r["host_id"]: r for r in insert_mock.await_args.args[0]}
    assert rows[1]["success"] is True
    assert rows[2]["success"] is False
    assert rows[2]["latency_ms"] is None


async def test_port_error_clears_when_service_check_is_removed():
    """Unmonitoring the last service check must let the port error go.

    Regression: the hysteresis streaks live in module-level dicts. Taking the
    port out of monitoring cleared host.port_error, but left the fail streak
    behind, and the latch below evaluated that streak on every cycle whether or
    not a service check had actually run. The next cycle — within 60s — put the
    flag straight back. Production showed a host on check_type=icmp, detail
    {"icmp": true}, still flagged port_error.
    """
    import scheduler

    host = FakePingHost(1, "enshrouded")
    # The host was failing its port check before the user unmonitored it.
    scheduler._port_fail_streak[host.id] = scheduler.PORT_ERROR_SET_THRESHOLD
    scheduler._port_pass_streak.pop(host.id, None)

    async def fake_check_host(_host):
        # ICMP only — no service check is configured any more.
        return True, False, 5.0, {"icmp": True}

    try:
        with patch.object(scheduler, "AsyncSessionLocal", _session_factory([host])), \
             patch("utils.ping.check_host", new=fake_check_host), \
             patch("services.clickhouse_client.insert_ping_checks", new=AsyncMock()), \
             patch("services.clickhouse_client.get_latest_ping_per_host",
                   new=AsyncMock(return_value={})):
            await scheduler.run_ping_checks()

        assert host.port_error is False, (
            "a stale fail streak re-latched port_error even though no service "
            "check is configured"
        )
        assert scheduler._port_fail_streak.get(host.id, 0) == 0, (
            "the stale streak must be dropped, or re-enabling the port would "
            "flag it as failing immediately"
        )
    finally:
        scheduler._port_fail_streak.pop(host.id, None)
        scheduler._port_pass_streak.pop(host.id, None)

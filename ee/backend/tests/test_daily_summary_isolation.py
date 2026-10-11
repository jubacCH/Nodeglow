"""The daily AI summary with data in two tenants (multi-tenancy phase 8):
each tenant's prompt and message carry only its own data, sent to its own
channel. Same harness as backend/tests/test_tenant_job_isolation.py."""
from __future__ import annotations

import pytest

from tests.test_tenant_job_isolation import (  # noqa: F401 — fixtures
    _run, assert_ch_scoped, assert_job_isolated, boundaries, sf,
)


async def test_daily_summary_keeps_each_tenants_data_in_its_tenant(sf, boundaries, caplog):
    await assert_job_isolated("daily_ai_summary", sf, boundaries, caplog)
    assert {t for t, _p in boundaries.prompts} == {1, 2}
    assert {addr for addr, _t in boundaries.sent} == {"chat-1", "chat-2"}


@pytest.mark.xfail(strict=True, reason="phase 6")
async def test_daily_summary_clickhouse_calls_are_tenant_scoped(sf, boundaries):
    await _run("daily_ai_summary")
    assert_ch_scoped("daily_ai_summary", boundaries)

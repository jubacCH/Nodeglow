"""Incremental log-volume baselines and the anomaly rules that consume them.

The old baseline pass recomputed every slot every 30 s from seven days of raw
syslog, grouped by (weekday, hour). That gives each slot at most one or two
samples, while every consumer requires ``sample_count >= 3`` — so log_anomaly,
the baseline anomaly API and content anomalies could never fire. These tests
pin the replacement: one sample per completed hour, accumulated over time, with
an hour-of-day fallback slot that becomes usable after three days.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from models.log_template import HostBaseline, LogTemplate
from services import log_intelligence as li


@pytest.fixture(autouse=True)
def _reset_progress():
    li._baseline_done_through = None
    yield
    li._baseline_done_through = None


def _epoch(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


class FakeSyslog:
    """Answers the hourly GROUP BY like ClickHouse would, from a rate table."""

    def __init__(self, rate_per_hour: dict[str, int]):
        self.rate = rate_per_hour
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, sql, params=None):
        self.calls.append((sql, dict(params or {})))
        start, end = params["start"], params["end"]
        rows = []
        h = start
        while h < end:
            for ip, cnt in self.rate.items():
                rows.append({"source_ip": ip, "hour_ts": _epoch(h), "cnt": cnt})
            h += timedelta(hours=1)
        return rows


# ── pure helpers ────────────────────────────────────────────────────────────


def test_ewma_update_is_exact_running_mean_and_variance_at_first():
    samples = [10.0, 20.0, 30.0]
    mean, var, n = 0.0, 0.0, 0
    for x in samples:
        mean, var, n = li.ewma_update(mean, var, n, x, alpha_min=0.0)
    assert n == 3
    assert mean == pytest.approx(20.0)
    # population variance of 10, 20, 30
    assert var == pytest.approx(200.0 / 3)


def test_ewma_update_weight_is_capped_after_warmup():
    mean, var, n = 100.0, 0.0, 50
    mean, var, n = li.ewma_update(mean, var, n, 200.0, alpha_min=0.2)
    # 1/51 is below the floor, so the new sample weighs 0.2
    assert mean == pytest.approx(120.0)
    assert var > 0


def test_baseline_hours_due_backfills_then_steps_one_hour():
    now = datetime(2026, 10, 10, 12, 5)
    first = li.baseline_hours_due(None, now)
    assert len(first) == li.BASELINE_BACKFILL_HOURS
    assert first[-1] == datetime(2026, 10, 10, 11, 0)  # last completed hour

    assert li.baseline_hours_due(first[-1], now) == []
    assert li.baseline_hours_due(first[-1], now + timedelta(hours=1)) == [
        datetime(2026, 10, 10, 12, 0)
    ]


def test_baseline_hours_due_waits_for_late_messages():
    # 12:01 — the 11:00 hour ended a minute ago, inside the settle time.
    now = datetime(2026, 10, 10, 12, 1)
    assert li.baseline_hours_due(None, now)[-1] == datetime(2026, 10, 10, 10, 0)


def test_baseline_hours_due_caps_catch_up_after_downtime():
    now = datetime(2026, 10, 10, 12, 5)
    hours = li.baseline_hours_due(now - timedelta(days=5), now)
    assert len(hours) == li.BASELINE_BACKFILL_HOURS


def test_pick_baseline_prefers_weekday_slot_and_falls_back():
    weekday = HostBaseline(host_key="a", hour_of_day=1, day_of_week=2, sample_count=1)
    any_day = HostBaseline(host_key="a", hour_of_day=1, day_of_week=7, sample_count=4)
    assert li.pick_baseline(weekday, any_day) is any_day
    weekday.sample_count = 3
    assert li.pick_baseline(weekday, any_day) is weekday
    any_day.sample_count = 2
    weekday.sample_count = 2
    assert li.pick_baseline(weekday, any_day) is None


# ── compute_baselines against a real schema ────────────────────────────────


async def test_compute_baselines_accumulates_samples_across_days(db):
    fake = FakeSyslog({"10.0.0.1": 100})
    start = datetime(2026, 10, 5, 0, 5)  # a Monday

    with patch("services.clickhouse_client.query", new=fake):
        # Backfill, then advance hour by hour for two more days.
        for step in range(0, 49):
            await li.compute_baselines(db, now=start + timedelta(hours=step))

    rows = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.host_key == "10.0.0.1",
            HostBaseline.hour_of_day == 10,
        )
    )).scalars().all()
    by_dow = {r.day_of_week: r for r in rows}

    any_day = by_dow[li.BASELINE_ANY_DAY]
    assert any_day.sample_count >= li.BASELINE_MIN_SAMPLES
    assert any_day.avg_rate == pytest.approx(100.0)
    assert any_day.std_rate == pytest.approx(0.0)
    # Weekday slots get one sample per such day.
    assert all(r.sample_count == 1 for d, r in by_dow.items() if d != li.BASELINE_ANY_DAY)

    # The ClickHouse query buckets on ingest time and still prunes on timestamp.
    sql = fake.calls[0][0]
    assert "toStartOfHour(received_at)" in sql
    assert "timestamp >= {start_prune:DateTime64(3)}" in sql


async def test_compute_baselines_is_idempotent_within_an_hour(db):
    fake = FakeSyslog({"10.0.0.1": 50})
    now = datetime(2026, 10, 5, 12, 5)

    with patch("services.clickhouse_client.query", new=fake):
        assert await li.compute_baselines(db, now=now) == li.BASELINE_BACKFILL_HOURS
        assert await li.compute_baselines(db, now=now + timedelta(minutes=20)) == 0
        # Forget the in-memory mirror, as a restart would: the persisted
        # progress must still prevent a second fold of the same hours.
        li._baseline_done_through = None
        assert await li.compute_baselines(db, now=now + timedelta(minutes=30)) == 0

    assert len(fake.calls) == 1
    counts = {r.sample_count for r in (await db.execute(select(HostBaseline))).scalars()}
    assert counts == {1}


async def test_compute_baselines_records_silence_for_known_sources(db):
    now = datetime(2026, 10, 5, 12, 5)
    with patch("services.clickhouse_client.query", new=FakeSyslog({"10.0.0.1": 80})):
        await li.compute_baselines(db, now=now)
    # Next hour the source sends nothing at all.
    with patch("services.clickhouse_client.query", new=FakeSyslog({})):
        await li.compute_baselines(db, now=now + timedelta(hours=1))

    row = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.host_key == "10.0.0.1",
            HostBaseline.hour_of_day == 12,
            HostBaseline.day_of_week == li.BASELINE_ANY_DAY,
        )
    )).scalar_one()
    # Yesterday 12:00 (80, from the backfill) and today's silent 12:00 (0):
    # the silent hour is a real sample, not a gap.
    assert row.sample_count == 2
    assert row.avg_rate == pytest.approx(40.0)


async def test_first_run_resets_statistics_from_the_old_algorithm(db):
    db.add(HostBaseline(
        host_key="10.0.0.9", hour_of_day=3, day_of_week=0,
        avg_rate=999.0, std_rate=50.0, sample_count=2,
        avg_template_count=4.0, std_template_count=1.0,
    ))
    await db.commit()

    with patch("services.clickhouse_client.query", new=FakeSyslog({})):
        await li.compute_baselines(db, now=datetime(2026, 10, 6, 12, 5))

    row = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.host_key == "10.0.0.9",
            HostBaseline.hour_of_day == 3,
            HostBaseline.day_of_week == 0,
        )
    )).scalar_one()
    await db.refresh(row)
    assert row.sample_count == 0
    assert row.avg_rate == 0.0
    # Template diversity is not the baseline pass's to reset.
    assert row.avg_template_count == 4.0


# ── consumers can now fire ──────────────────────────────────────────────────


async def _learn(db, ip: str | dict, rate: int = 0, days: int = 3) -> datetime:
    rates = ip if isinstance(ip, dict) else {ip: rate}
    start = datetime.utcnow().replace(minute=5, second=0, microsecond=0) - timedelta(days=days)
    with patch("services.clickhouse_client.query", new=FakeSyslog(rates)):
        for step in range(0, days * 24 + 1):
            await li.compute_baselines(db, now=start + timedelta(hours=step))
    return start


async def test_log_anomaly_rule_fires_on_learned_baselines(db):
    """End to end: three days of ~100 msg/h, then a burst -> incident."""
    from models.incident import Incident
    from services import correlation as corr

    await _learn(db, "10.0.0.1", 100)
    corr._rule_hit_counts.clear()
    corr._current_cycle_hits.clear()

    # 60 messages in 10 minutes = 360/h against a ~100/h baseline.
    counts = AsyncMock(return_value={"10.0.0.1": 60})
    with patch("services.clickhouse_client.count_syslog_received_by_source", new=counts), \
         patch("notifications.notify", new=AsyncMock()):
        await corr._rule_log_anomaly(db, min_cycles=1)
        await db.commit()

    incidents = (await db.execute(
        select(Incident).where(Incident.rule == "log_anomaly")
    )).scalars().all()
    assert len(incidents) == 1
    assert "10.0.0.1" in incidents[0].title
    # One grouped count for all sources.
    assert counts.await_count == 1


async def test_log_anomaly_rule_stays_quiet_at_normal_volume(db):
    from models.incident import Incident
    from services import correlation as corr

    await _learn(db, "10.0.0.1", 100)
    corr._rule_hit_counts.clear()
    corr._current_cycle_hits.clear()

    with patch("services.clickhouse_client.count_syslog_received_by_source",
               new=AsyncMock(return_value={"10.0.0.1": 17})), \
         patch("notifications.notify", new=AsyncMock()):
        await corr._rule_log_anomaly(db, min_cycles=1)
        await db.commit()

    assert (await db.execute(select(Incident))).scalars().all() == []


async def test_detect_baseline_anomalies_reports_spike_and_silence(db):
    await _learn(db, {"10.0.0.1": 100, "10.0.0.2": 100})

    # Make the variance non-zero so the z-score path is used for .1.
    for b in (await db.execute(select(HostBaseline))).scalars():
        b.std_rate = 10.0
    await db.commit()

    with patch("services.clickhouse_client.count_syslog_received_by_source",
               new=AsyncMock(return_value={"10.0.0.1": 500})):
        anomalies = await li.detect_baseline_anomalies(db)

    kinds = {(a["source_ip"], a["type"]) for a in anomalies}
    assert ("10.0.0.1", "rate_spike") in kinds
    # .2 sent nothing this hour — silence must be reported even though it is
    # absent from the current counts.
    assert ("10.0.0.2", "silent") in kinds


async def test_content_anomalies_fire_on_fallback_slot(db):
    """Diversity spike on a stable host + severity upgrade."""
    now = datetime.utcnow()
    db.add(HostBaseline(
        host_key="10.0.0.5", hour_of_day=now.hour, day_of_week=li.BASELINE_ANY_DAY,
        avg_rate=10.0, std_rate=1.0, sample_count=3,
        avg_template_count=4.0, std_template_count=1.0,
    ))
    db.add(LogTemplate(template_hash="sevup00000000001", template="link <*> flapped",
                       count=50, severity_mode=6))
    db.add(LogTemplate(template_hash="normal0000000001", template="error <*>",
                       count=50, severity_mode=3))
    await db.commit()

    async def fake_query(sql, params=None):
        if "uniqExact" in sql:
            assert params["ips"] == ["10.0.0.5"]
            return [{"source_ip": "10.0.0.5", "diversity": 25}]
        assert "HAVING cnt >= {min_cnt:UInt32}" in sql
        return [
            {"template_hash": "sevup00000000001", "min_sev": 2, "cnt": 7},
            {"template_hash": "normal0000000001", "min_sev": 3, "cnt": 9},
        ]

    with patch("services.clickhouse_client.query", new=fake_query):
        anomalies = await li.detect_content_anomalies(db)

    types = {a["type"]: a for a in anomalies}
    assert types["template_diversity_spike"]["source_ip"] == "10.0.0.5"
    # Only the template that is normally info-level counts as an upgrade.
    upgrades = [a for a in anomalies if a["type"] == "severity_upgrade"]
    assert [a["template_hash"] for a in upgrades] == ["sevup00000000001"]


# ── template flush ──────────────────────────────────────────────────────────


async def test_flush_templates_bulk_upserts(db):
    li._template_cache.clear()
    li._template_counts.clear()
    li._new_templates.clear()

    db.add(LogTemplate(template_hash="known00000000001", template="known <*>",
                       count=10, last_seen=datetime(2026, 1, 1)))
    # Already in the DB but (say, after a cache reload race) queued as new:
    db.add(LogTemplate(template_hash="raced00000000001", template="raced <*>",
                       count=5, first_seen=datetime(2026, 1, 1),
                       last_seen=datetime(2026, 1, 1)))
    await db.commit()

    li._template_counts.update({
        "known00000000001": 3, "raced00000000001": 2, "fresh00000000001": 4,
    })
    li._new_templates.update({
        "raced00000000001": ("raced <*>", "raced x", []),
        "fresh00000000001": ("fresh <*>", "fresh y", ["network"]),
    })

    await li.flush_templates(db)

    rows = {t.template_hash: t for t in (await db.execute(select(LogTemplate))).scalars()}
    for t in rows.values():
        await db.refresh(t)
    assert rows["known00000000001"].count == 13
    assert rows["raced00000000001"].count == 7  # added, not overwritten
    assert rows["raced00000000001"].first_seen == datetime(2026, 1, 1)
    assert rows["fresh00000000001"].count == 4
    assert rows["fresh00000000001"].tags == "network"
    assert rows["fresh00000000001"].noise_score == 10
    # Ids land in the cache so the next message is not "new" again.
    assert li._template_cache["fresh00000000001"] == rows["fresh00000000001"].id
    assert li._template_cache["raced00000000001"] == rows["raced00000000001"].id
    assert not li._template_counts and not li._new_templates


async def test_flush_templates_keeps_counts_when_the_write_fails(db):
    li._template_counts.clear()
    li._new_templates.clear()
    li._template_counts["abc"] = 5

    with patch.object(li, "_write_template_batch", new=AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(RuntimeError):
            await li.flush_templates(db)

    assert li._template_counts["abc"] == 5
    li._template_counts.clear()


# ── severity trends ─────────────────────────────────────────────────────────


async def test_severity_trends_aggregate_in_clickhouse_first(db):
    db.add(LogTemplate(template_hash="rising0000000001", template="r", count=100))
    db.add(LogTemplate(template_hash="tiny000000000001", template="t", count=2))
    db.add(LogTemplate(template_hash="stale00000000001", template="s", count=100,
                       trend_direction="rising", trend_score=0.5))
    await db.commit()

    base = datetime(2026, 10, 10, 0, 0)
    points = [(base + timedelta(hours=i), c, 3.0) for i, c in enumerate([10, 20, 40, 80])]

    async def fake_query(sql, params=None):
        assert "HAVING count() >= {min_points:UInt32}" in sql
        assert "IN (" not in sql  # no giant hash lists
        return [
            # deliberately unordered: groupArray guarantees no order
            {"template_hash": "rising0000000001", "points": list(reversed(points))},
            {"template_hash": "tiny000000000001", "points": points},
        ]

    with patch("services.clickhouse_client.query", new=fake_query):
        await li.compute_severity_trends(db)

    rows = {t.template_hash: t for t in (await db.execute(select(LogTemplate))).scalars()}
    for t in rows.values():
        await db.refresh(t)
    assert rows["rising0000000001"].trend_direction == "rising"
    assert rows["rising0000000001"].severity_mode == 3
    # count < 5 is not trended
    assert rows["tiny000000000001"].trend_direction in (None, "stable")
    # no data any more -> no longer rising
    assert rows["stale00000000001"].trend_direction == "stable"


def test_trend_from_counts():
    assert li.trend_from_counts([10, 20, 30, 40])[0] == "rising"
    assert li.trend_from_counts([40, 30, 20, 10])[0] == "falling"
    assert li.trend_from_counts([10, 10, 10, 10])[0] == "stable"

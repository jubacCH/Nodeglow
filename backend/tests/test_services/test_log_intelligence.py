"""Tests for the log intelligence engine (template extraction, tagging, noise scoring)."""
from datetime import datetime, timedelta

from services.log_intelligence import (
    auto_tag,
    compute_noise_score,
    extract_template,
    process_message,
)


# ── Template Extraction ───────────────────────────────────────────────────────

def test_extract_template_replaces_ips():
    tpl, h = extract_template("Connection from 192.168.1.100 port 22345")
    assert "<IP>" in tpl
    assert "<NUM>" in tpl or "<PORT>" in tpl
    assert "192.168.1.100" not in tpl


def test_extract_template_replaces_timestamps():
    tpl, _ = extract_template("Event at 2026-03-07T10:30:15.123Z completed")
    assert "<TS>" in tpl
    assert "2026" not in tpl


def test_extract_template_replaces_uuids():
    tpl, _ = extract_template("Session 550e8400-e29b-41d4-a716-446655440000 started")
    assert "<UUID>" in tpl


def test_extract_template_replaces_paths():
    tpl, _ = extract_template("Error reading /var/log/syslog.1")
    assert "<PATH>" in tpl


def test_extract_template_replaces_hex():
    tpl, _ = extract_template("Memory at 0xDEADBEEF corrupted")
    assert "<HEX>" in tpl


def test_extract_template_deterministic():
    """Same message structure = same hash."""
    _, h1 = extract_template("Failed password for root from 10.0.0.1 port 22345")
    _, h2 = extract_template("Failed password for root from 10.0.0.2 port 54321")
    assert h1 == h2


def test_extract_template_different_structure():
    """Different message structure = different hash."""
    _, h1 = extract_template("Failed password for root from 10.0.0.1")
    _, h2 = extract_template("Disk I/O error on sda1 sector 12345")
    assert h1 != h2


def test_extract_template_empty():
    tpl, h = extract_template("")
    assert tpl == ""
    assert h is not None


def test_extract_template_mac_address():
    tpl, _ = extract_template("Device 00:11:22:33:44:55 connected")
    assert "<MAC>" in tpl


def test_extract_template_small_numbers_do_not_split_templates():
    """1-2 digit counters used to stay literal: one template per value."""
    _, h1 = extract_template("retry 3 of 10 failed after 5 attempts")
    _, h2 = extract_template("retry 7 of 10 failed after 12 attempts")
    assert h1 == h2
    tpl, _ = extract_template("queue depth 42, load 0.75")
    assert "42" not in tpl and "0.75" not in tpl
    assert tpl.count("<NUM>") == 2


def test_extract_template_numbers_with_units():
    _, h1 = extract_template("request took 5ms, payload 512MB")
    _, h2 = extract_template("request took 1.5s, payload 3GB")
    tpl, _ = extract_template("request took 5ms, payload 512MB")
    assert "<NUM>ms" in tpl and "<NUM>MB" in tpl
    # The unit stays, so milliseconds and seconds remain distinguishable.
    assert h1 != h2


def test_extract_template_keeps_identifiers_with_digits():
    """Interface / device names are structure, not values."""
    tpl, _ = extract_template("eth0: link up on vlan10 via sda1 and ipv6")
    for ident in ("eth0", "vlan10", "sda1", "ipv6"):
        assert ident in tpl
    _, h_eth0 = extract_template("eth0: link down")
    _, h_eth1 = extract_template("eth1: link down")
    assert h_eth0 != h_eth1


def test_extract_template_port_and_pid_keep_their_labels():
    tpl, _ = extract_template("sshd pid=42 listening on port 22")
    assert "pid=<PID>" in tpl
    assert "port <PORT>" in tpl


# ── Auto-Tagging ─────────────────────────────────────────────────────────────

def test_auto_tag_security():
    tags = auto_tag("Failed password for root from 10.0.0.1")
    assert "security" in tags


def test_auto_tag_hardware():
    tags = auto_tag("disk I/O error on sda1 sector 12345")
    assert "hardware" in tags


def test_auto_tag_network():
    tags = auto_tag("eth0: link down")
    assert "network" in tags


def test_auto_tag_service():
    tags = auto_tag("systemd: Started nginx.service")
    assert "service" in tags


def test_auto_tag_auth():
    tags = auto_tag("Accepted publickey for admin from 10.0.0.1")
    assert "auth" in tags


def test_auto_tag_storage():
    tags = auto_tag("ZFS pool tank scrub completed with 0 errors")
    assert "storage" in tags


def test_auto_tag_multiple():
    tags = auto_tag("Failed SSH login from 10.0.0.1 denied by firewall")
    assert "security" in tags
    assert "network" in tags or "auth" in tags


def test_auto_tag_no_match():
    tags = auto_tag("Just a regular message about nothing special")
    assert len(tags) == 0


# ── Noise Score ───────────────────────────────────────────────────────────────

def test_noise_score_new_template():
    """Brand new templates should have low noise score (= interesting)."""
    score = compute_noise_score(
        count=1, hours_active=0.1,
        first_seen=datetime.utcnow(),
    )
    assert score < 30


def test_noise_score_high_frequency():
    """High frequency messages should be noisy."""
    score = compute_noise_score(
        count=10000, hours_active=10,
        first_seen=datetime.utcnow() - timedelta(days=7),
    )
    assert score > 60


def test_noise_score_critical_severity():
    """Critical severity should reduce noise score."""
    score = compute_noise_score(
        count=100, hours_active=10,
        first_seen=datetime.utcnow() - timedelta(days=7),
        severity=2,  # critical
    )
    score_info = compute_noise_score(
        count=100, hours_active=10,
        first_seen=datetime.utcnow() - timedelta(days=7),
        severity=6,  # informational
    )
    assert score < score_info


def test_noise_score_security_tag():
    """Security-tagged messages should be less noisy."""
    score_with = compute_noise_score(
        count=100, hours_active=10,
        first_seen=datetime.utcnow() - timedelta(days=3),
        tags=["security"],
    )
    score_without = compute_noise_score(
        count=100, hours_active=10,
        first_seen=datetime.utcnow() - timedelta(days=3),
    )
    assert score_with < score_without


def test_noise_score_bounds():
    """Score should always be 0-100."""
    score_low = compute_noise_score(
        count=1, hours_active=0.01,
        first_seen=datetime.utcnow(),
        severity=0,
        tags=["security", "hardware"],
    )
    score_high = compute_noise_score(
        count=1000000, hours_active=100,
        first_seen=datetime.utcnow() - timedelta(days=30),
        severity=7,
    )
    assert 0 <= score_low <= 100
    assert 0 <= score_high <= 100


# ── Process Message (integration) ────────────────────────────────────────────

def test_process_message_returns_enrichment():
    result = process_message("Failed password for root from 10.0.0.1 port 22345", severity=4)
    assert "template_hash" in result
    assert "tags" in result
    assert "noise_score" in result
    assert "is_new_template" in result
    assert isinstance(result["tags"], list)


def test_process_message_detects_new_template():
    """First time seeing a template = is_new_template."""
    # Clear caches for test isolation
    from services.log_intelligence import _template_cache, _new_templates
    _template_cache.clear()
    _new_templates.clear()

    result = process_message("A very unique message 12345678 that nobody has ever seen", severity=6)
    assert result["is_new_template"] is True

    # Second time = not new
    result2 = process_message("A very unique message 87654321 that nobody has ever seen", severity=6)
    assert result2["is_new_template"] is False
    assert result2["template_hash"] == result["template_hash"]


# ── Predictor: blacklist + Wilson ───────────────────────────────────────────

from datetime import datetime
from unittest.mock import AsyncMock, patch

from models.log_template import LogTemplate, PrecursorPattern
from services.log_intelligence import _learn_precursors_for_event, _template_cache
from sqlalchemy import select


async def _seed_template(db, template_text: str, template_hash: str) -> int:
    tpl = LogTemplate(template_hash=template_hash, template=template_text, example=template_text)
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    _template_cache[template_hash] = tpl.id
    return tpl.id


async def test_learn_skips_blacklisted_template(db):
    tpl_id = await _seed_template(db, "udhcpc[1234]: sending renew to server <*>", "h_udhcpc")

    # Mock ClickHouse to return that template right before 5 host_down events.
    fake_msgs = [{"host_id": 1, "message": "udhcpc[1234]: sending renew to server 10.0.0.1",
                  "timestamp": datetime(2026, 5, 20, 12, 0, 0)}]
    with patch("services.log_intelligence.ch_query", new=AsyncMock(return_value=fake_msgs)), \
         patch("services.log_intelligence.extract_template",
               return_value=("udhcpc[<*>]: sending renew to server <*>", "h_udhcpc")):
        events = [(1, datetime(2026, 5, 20, 12, 2, 30))] * 5
        await _learn_precursors_for_event(db, "host_down", events, datetime(2026, 5, 20, 12, 5, 0))

    rows = (await db.execute(
        select(PrecursorPattern).where(PrecursorPattern.template_id == tpl_id)
    )).scalars().all()
    assert rows == []  # blacklist suppressed creation


async def test_learn_uses_wilson_not_naive_ratio(db):
    tpl_id = await _seed_template(db, "kernel: <*> oom-killer invoked", "h_oom")

    fake_msgs = [{"host_id": 1, "message": "kernel: oom-killer invoked",
                  "timestamp": datetime(2026, 5, 20, 12, 0, 0)}]
    with patch("services.log_intelligence.ch_query", new=AsyncMock(return_value=fake_msgs)), \
         patch("services.log_intelligence.extract_template",
               return_value=("kernel: <*> oom-killer invoked", "h_oom")):
        events = [(1, datetime(2026, 5, 20, 12, 2, 30))] * 5
        await _learn_precursors_for_event(db, "host_down", events, datetime(2026, 5, 20, 12, 5, 0))

    pp = (await db.execute(
        select(PrecursorPattern).where(PrecursorPattern.template_id == tpl_id)
    )).scalar_one()
    # Naive ratio would be 1.0; Wilson at 5/5 is ~0.566.
    assert 0.55 < pp.confidence < 0.58
    assert pp.occurrence_count == 5
    assert pp.total_checked == 5


async def test_cleanup_removes_blacklisted_patterns(db):
    from services.log_intelligence import cleanup_precursor_patterns

    tpl_bad = LogTemplate(template_hash="hb", template="udhcpc[<*>]: sending renew", example="x")
    tpl_good = LogTemplate(template_hash="hg", template="kernel: panic", example="x")
    db.add_all([tpl_bad, tpl_good])
    await db.commit()
    await db.refresh(tpl_bad)
    await db.refresh(tpl_good)

    db.add_all([
        PrecursorPattern(template_id=tpl_bad.id, precedes_event="host_down",
                         confidence=0.95, occurrence_count=5, total_checked=5,
                         updated_at=datetime.utcnow()),
        PrecursorPattern(template_id=tpl_good.id, precedes_event="host_down",
                         confidence=1.0, occurrence_count=5, total_checked=5,
                         updated_at=datetime.utcnow()),
    ])
    await db.commit()

    await cleanup_precursor_patterns(db)
    await db.commit()

    remaining = (await db.execute(select(PrecursorPattern))).scalars().all()
    # Blacklisted one gone, kernel-panic one re-projected to Wilson 5/5 ~0.566.
    assert len(remaining) == 1
    assert remaining[0].template_id == tpl_good.id
    assert 0.55 < remaining[0].confidence < 0.58


# ── Noise Score Refresh (bulk path) ──────────────────────────────────────────

async def _seed_noise_template(db, template, count, hours_ago, noise_score=50, rate=0.0):
    """Insert a LogTemplate and return it."""
    from models.log_template import LogTemplate

    tpl = LogTemplate(
        template_hash=f"h{abs(hash(template)) % 10**8:08d}",
        template=template,
        count=count,
        first_seen=datetime.utcnow() - timedelta(hours=hours_ago),
        last_seen=datetime.utcnow(),
        noise_score=noise_score,
        avg_rate_per_hour=rate,
        tags="",
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def test_refresh_noise_scores_updates_changed_templates(db):
    """Templates whose computed score differs are written back."""
    from services.log_intelligence import refresh_noise_scores

    tpl = await _seed_noise_template(db, "Noisy <*> message", count=100_000, hours_ago=10)

    changed = await refresh_noise_scores(db)

    assert changed == 1
    await db.refresh(tpl)
    assert tpl.noise_score > 50          # high rate => noisy
    assert tpl.avg_rate_per_hour > 0


async def test_refresh_noise_scores_skips_unchanged_templates(db):
    """A second pass with no new data must not write anything.

    This is the hot-loop guard: the job runs on a schedule over the whole
    template table, so re-writing unchanged rows is what made it expensive.
    """
    from services.log_intelligence import refresh_noise_scores

    await _seed_noise_template(db, "Stable <*> line", count=42, hours_ago=5)

    first = await refresh_noise_scores(db)
    second = await refresh_noise_scores(db)

    assert first == 1
    assert second == 0


async def test_refresh_noise_scores_handles_empty_table(db):
    """No templates at all is not an error."""
    from services.log_intelligence import refresh_noise_scores

    assert await refresh_noise_scores(db) == 0


async def test_refresh_noise_scores_respects_precursor_patterns(db):
    """A template linked to a confident precursor pattern is never noise."""
    from models.log_template import PrecursorPattern
    from services.log_intelligence import refresh_noise_scores

    tpl = await _seed_noise_template(db, "Precursor <*> warning", count=100_000, hours_ago=10)
    db.add(PrecursorPattern(
        template_id=tpl.id,
        precedes_event="host_down",
        confidence=0.9,
        avg_lead_time_sec=300,
        occurrence_count=10,
        total_checked=10,
    ))
    await db.commit()

    await refresh_noise_scores(db)

    await db.refresh(tpl)
    noisy = await _seed_noise_template(db, "Plain <*> warning", count=100_000, hours_ago=10)
    await refresh_noise_scores(db)
    await db.refresh(noisy)
    assert tpl.noise_score < noisy.noise_score


# ── Template retention ───────────────────────────────────────────────────────

async def test_retention_prunes_templates_not_seen_in_window(db):
    """Templates the system no longer observes must not accumulate forever."""
    from services.log_intelligence import cleanup_log_templates

    stale = await _seed_noise_template(db, "Ancient <*> line", count=1, hours_ago=24 * 200)
    stale.last_seen = datetime.utcnow() - timedelta(days=120)
    fresh = await _seed_noise_template(db, "Current <*> line", count=5, hours_ago=2)
    await db.commit()

    deleted = await cleanup_log_templates(db, retention_days=90)

    assert deleted == 1
    remaining = (await db.execute(select(LogTemplate.id))).scalars().all()
    assert fresh.id in remaining
    assert stale.id not in remaining


async def test_retention_keeps_templates_backing_a_precursor(db):
    """Predictor history must survive retention — and the FK requires it."""
    from services.log_intelligence import cleanup_log_templates

    tpl = await _seed_noise_template(db, "Precursor <*> line", count=1, hours_ago=24 * 200)
    tpl.last_seen = datetime.utcnow() - timedelta(days=120)
    db.add(PrecursorPattern(
        template_id=tpl.id,
        precedes_event="host_down",
        confidence=0.8,
        avg_lead_time_sec=120,
        occurrence_count=5,
        total_checked=5,
    ))
    await db.commit()

    deleted = await cleanup_log_templates(db, retention_days=90)

    assert deleted == 0
    remaining = (await db.execute(select(LogTemplate.id))).scalars().all()
    assert tpl.id in remaining


async def test_retention_prunes_one_off_templates_early(db):
    """Seen once and not again for a week: gone, long before the 90 days."""
    from services.log_intelligence import cleanup_log_templates

    one_off = await _seed_noise_template(db, "Once <*> only", count=1, hours_ago=24 * 10)
    one_off.last_seen = datetime.utcnow() - timedelta(days=10)
    recent_one_off = await _seed_noise_template(db, "Once <*> today", count=1, hours_ago=2)
    repeated = await _seed_noise_template(db, "Often <*> seen", count=3, hours_ago=24 * 10)
    repeated.last_seen = datetime.utcnow() - timedelta(days=10)
    await db.commit()

    deleted = await cleanup_log_templates(db, retention_days=90, singleton_retention_days=7)

    assert deleted == 1
    remaining = set((await db.execute(select(LogTemplate.id))).scalars().all())
    assert one_off.id not in remaining
    assert {recent_one_off.id, repeated.id} <= remaining


async def test_retention_singleton_rule_can_be_disabled(db):
    from services.log_intelligence import cleanup_log_templates

    one_off = await _seed_noise_template(db, "Once <*> kept", count=1, hours_ago=24 * 10)
    one_off.last_seen = datetime.utcnow() - timedelta(days=10)
    await db.commit()

    assert await cleanup_log_templates(db, retention_days=90, singleton_retention_days=0) == 0


async def test_retention_disabled_deletes_nothing(db):
    """A retention of 0 means 'keep everything', not 'delete everything'."""
    from services.log_intelligence import cleanup_log_templates

    tpl = await _seed_noise_template(db, "Old <*> line", count=1, hours_ago=24 * 200)
    tpl.last_seen = datetime.utcnow() - timedelta(days=500)
    await db.commit()

    assert await cleanup_log_templates(db, retention_days=0) == 0
    remaining = (await db.execute(select(LogTemplate.id))).scalars().all()
    assert tpl.id in remaining


# ── Fleet-wide precursor events ──────────────────────────────────────────────

async def test_precursor_query_filters_by_host_when_given_one(db):
    """A host_down event looks only at that host's syslog."""
    from services.log_intelligence import _learn_precursors_for_event

    seen = {}

    async def fake_ch_query(sql, params=None):
        seen["sql"] = sql
        seen["params"] = params
        return []

    with patch("services.log_intelligence.ch_query", new=fake_ch_query):
        await _learn_precursors_for_event(
            db, "host_down", [(7, datetime(2026, 5, 20, 12, 0, 0))],
            datetime(2026, 5, 20, 12, 5, 0),
        )

    assert seen["params"]["h0"] == 7
    assert "host_id = {h0:Int32}" in seen["sql"]


async def test_precursor_query_is_fleet_wide_for_host_id_zero(db):
    """Integration failures and incidents are not tied to one host.

    They are passed as host_id 0, which must mean "any host". Using it as a
    filter matched nothing, so these two event types silently never learned.
    """
    from services.log_intelligence import _learn_precursors_for_event

    seen = {}

    async def fake_ch_query(sql, params=None):
        seen["sql"] = sql
        seen["params"] = params
        return []

    with patch("services.log_intelligence.ch_query", new=fake_ch_query):
        await _learn_precursors_for_event(
            db, "incident", [(0, datetime(2026, 5, 20, 12, 0, 0))],
            datetime(2026, 5, 20, 12, 5, 0),
        )

    # Host 0 must mean "all hosts", not a filter on a host that cannot exist.
    assert "host_id =" not in seen["sql"]
    assert not any(k.startswith("h") for k in seen["params"])
    assert seen["params"]["s0"] == datetime(2026, 5, 20, 11, 55, 0)


# ── Template diversity: batch instead of N+1 ─────────────────────────────────

async def test_template_diversity_loads_baselines_in_one_query(db):
    """Baselines are fetched in a single query, not once per host.

    The per-host lookup made this the second-heaviest index consumer in
    production; the cost grew with fleet size although every row sits in the
    same hour/day slot.
    """
    from models.log_template import HostBaseline
    from services.log_intelligence import compute_template_diversity

    now = datetime.utcnow()
    hosts = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    for host in hosts:
        db.add(HostBaseline(
            host_key=host, hour_of_day=now.hour, day_of_week=now.weekday(),
            avg_rate=1.0, std_rate=1.0, sample_count=5,
            avg_template_count=10.0, std_template_count=2.0,
        ))
    await db.commit()

    ch_rows = [{"source_ip": h, "diversity": 20, "error_diversity": 1} for h in hosts]

    select_calls = 0
    original_execute = db.execute

    async def counting_execute(statement, *args, **kwargs):
        nonlocal select_calls
        if statement.__class__.__name__ == "Select":
            select_calls += 1
        return await original_execute(statement, *args, **kwargs)

    with patch("services.clickhouse_client.query", new=AsyncMock(return_value=ch_rows)), \
         patch.object(db, "execute", new=counting_execute):
        await compute_template_diversity(db)

    assert select_calls == 1, f"expected one batched SELECT, made {select_calls}"

    # And the values were actually updated.
    updated = (await db.execute(
        select(HostBaseline).where(HostBaseline.host_key == "10.0.0.1")
    )).scalar_one()
    assert updated.avg_template_count > 10.0


async def test_template_diversity_ignores_hosts_without_baseline(db):
    """A host seen in syslog but without a baseline row is simply skipped."""
    from services.log_intelligence import compute_template_diversity

    ch_rows = [{"source_ip": "10.9.9.9", "diversity": 5, "error_diversity": 0}]

    with patch("services.clickhouse_client.query", new=AsyncMock(return_value=ch_rows)):
        await compute_template_diversity(db)  # must not raise


# ── Precursor learning: batched candidate fetch ──────────────────────────────

def test_precursor_windows_of_one_outage_merge():
    from services.log_intelligence import _merge_precursor_windows

    base = datetime(2026, 5, 20, 12, 0, 0)
    events = [(3, base + timedelta(minutes=m)) for m in range(60)]  # one failed check a minute
    events.append((3, base + timedelta(hours=5)))                   # a later, separate outage
    events.append((0, base))

    merged = _merge_precursor_windows(events)
    assert merged[3] == [
        (base - timedelta(minutes=5), base + timedelta(minutes=59)),
        (base + timedelta(hours=5, minutes=-5), base + timedelta(hours=5)),
    ]
    assert merged[0] == [(base - timedelta(minutes=5), base)]


async def test_precursor_learning_uses_few_queries_for_many_events(db):
    """An hour-long outage is 60 down events: one query, not sixty."""
    from services.log_intelligence import _learn_precursors_for_event

    tpl_id = await _seed_template(db, "disk <*> failing", "h_disk_fail")
    base = datetime(2026, 5, 20, 12, 0, 0)
    calls = []

    async def fake_ch_query(sql, params=None):
        calls.append((sql, params))
        return [{"host_id": 3, "message": "disk sda failing", "timestamp": base - timedelta(minutes=2)}]

    events = [(3, base + timedelta(minutes=m)) for m in range(60)]
    with patch("services.log_intelligence.ch_query", new=fake_ch_query),          patch("services.log_intelligence.extract_template",
               return_value=("disk <*> failing", "h_disk_fail")):
        await _learn_precursors_for_event(db, "host_down", events, base + timedelta(hours=1))

    assert len(calls) == 1
    assert "LIMIT 50 BY host_id, toStartOfMinute(timestamp)" in calls[0][0]
    pp = (await db.execute(
        select(PrecursorPattern).where(PrecursorPattern.template_id == tpl_id)
    )).scalar_one()
    # 11:58 lies inside the (inclusive) 5-minute window of the 12:00-12:03
    # events only.
    assert pp.total_checked == 4
    assert pp.occurrence_count == 4

"""Tests for the correlation engine helpers."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

from models.log_template import LogTemplate, PrecursorPattern
from services.correlation import (
    _find_or_create_incident,
    _host_ids_hash,
    _rule_precursor_observed,
)


async def test_host_ids_hash_deterministic():
    h1 = _host_ids_hash([1, 2, 3])
    h2 = _host_ids_hash([3, 1, 2])
    assert h1 == h2


async def test_host_ids_hash_different_for_different_ids():
    h1 = _host_ids_hash([1, 2])
    h2 = _host_ids_hash([1, 3])
    assert h1 != h2


async def test_find_or_create_new_incident(db):
    inc = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="created",
        summary="Host 1 is down",
    )
    await db.commit()

    assert inc.id is not None
    assert inc.rule == "test_rule"
    assert inc.status == "open"


async def test_find_or_create_dedup(db):
    inc1 = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="created",
        summary="First trigger",
    )
    await db.commit()

    inc2 = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="host_down",
        summary="Second trigger",
    )
    await db.commit()

    # Same incident should be returned (dedup by rule + host_ids_hash)
    assert inc1.id == inc2.id


async def test_find_or_create_different_hosts_creates_new(db):
    inc1 = await _find_or_create_incident(
        db, rule="r", title="t", severity="warning",
        host_ids=[1], event_type="created", summary="s1",
    )
    await db.commit()

    inc2 = await _find_or_create_incident(
        db, rule="r", title="t", severity="warning",
        host_ids=[2], event_type="created", summary="s2",
    )
    await db.commit()

    assert inc1.id != inc2.id


# ── Predictive precursor rule (Phase 7c learning loop) ──────────────────────


async def test_precursor_rule_creates_predictive_incident(db):
    """High-confidence precursor pattern observed in syslog → predictive incident."""
    # Seed: a learned template + a high-confidence precursor pointing at host_down
    tpl = LogTemplate(
        template_hash="abc123def456abcd",
        template="Failed password for <*> from <*>",
        example="Failed password for root from 1.2.3.4",
        count=100,
    )
    db.add(tpl)
    await db.flush()

    db.add(PrecursorPattern(
        template_id=tpl.id,
        precedes_event="host_down",
        confidence=0.85,
        avg_lead_time_sec=180,  # 3 min
        occurrence_count=12,
        total_checked=14,
    ))
    await db.commit()

    # Mock the CH query to "observe" this template right now
    fake_ch = AsyncMock(return_value=[
        {"template_hash": "abc123def456abcd", "host_id": 5},
    ])
    # Reset rule-hit tracker so the test isn't affected by other tests
    from services import correlation as _corr
    _corr._current_cycle_hits.clear()
    _corr._rule_hit_counts.clear()

    with patch("services.clickhouse_client.query", fake_ch), \
         patch("notifications.notify", new_callable=AsyncMock):
        # First call — gets tracked but doesn't fire (min_cycles=2)
        await _rule_precursor_observed(db, min_cycles=2)
        # Second call — should fire
        await _rule_precursor_observed(db, min_cycles=2)
        await db.commit()

    # An incident should now exist with rule="learned_precursor"
    from models.incident import Incident, IncidentEvent
    from sqlalchemy import select
    incidents = (await db.execute(
        select(Incident).where(Incident.rule == "learned_precursor")
    )).scalars().all()
    assert len(incidents) == 1
    inc = incidents[0]
    assert "Predicted" in inc.title
    assert "85%" in inc.title or "85" in inc.title
    # Summary lives on the IncidentEvent
    events = (await db.execute(
        select(IncidentEvent).where(IncidentEvent.incident_id == inc.id)
    )).scalars().all()
    assert any("Failed password" in (e.summary or "") for e in events)


async def test_precursor_rule_skips_low_confidence(db):
    """Low-confidence patterns should not fire."""
    tpl = LogTemplate(template_hash="lowconf01abcdefa", template="x", count=1)
    db.add(tpl)
    await db.flush()
    db.add(PrecursorPattern(
        template_id=tpl.id,
        precedes_event="host_down",
        confidence=0.3,  # below default 0.7 threshold
        occurrence_count=20,
    ))
    await db.commit()

    fake_ch = AsyncMock(return_value=[
        {"template_hash": "lowconf01abcdefa", "host_id": 1},
    ])
    from services import correlation as _corr
    _corr._current_cycle_hits.clear()
    _corr._rule_hit_counts.clear()

    with patch("services.clickhouse_client.query", fake_ch), \
         patch("notifications.notify", new_callable=AsyncMock):
        await _rule_precursor_observed(db, min_cycles=1)
        await db.commit()

    from models.incident import Incident
    from sqlalchemy import select
    incidents = (await db.execute(
        select(Incident).where(Incident.rule == "learned_precursor")
    )).scalars().all()
    assert len(incidents) == 0


async def test_precursor_rule_skips_few_occurrences(db):
    """High-confidence patterns with too few historical occurrences shouldn't fire."""
    tpl = LogTemplate(template_hash="rareobs01abcdefa", template="x", count=1)
    db.add(tpl)
    await db.flush()
    db.add(PrecursorPattern(
        template_id=tpl.id,
        precedes_event="host_down",
        confidence=0.95,
        occurrence_count=2,  # below default 5 threshold
    ))
    await db.commit()

    fake_ch = AsyncMock(return_value=[
        {"template_hash": "rareobs01abcdefa", "host_id": 1},
    ])
    from services import correlation as _corr
    _corr._current_cycle_hits.clear()
    _corr._rule_hit_counts.clear()

    with patch("services.clickhouse_client.query", fake_ch), \
         patch("notifications.notify", new_callable=AsyncMock):
        await _rule_precursor_observed(db, min_cycles=1)
        await db.commit()

    from models.incident import Incident
    from sqlalchemy import select
    incidents = (await db.execute(
        select(Incident).where(Incident.rule == "learned_precursor")
    )).scalars().all()
    assert len(incidents) == 0

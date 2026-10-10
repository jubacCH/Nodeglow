"""The unified host state: one probe-aware rule for every list and counter."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from services import host_state as hs
from services.probes import ProbeState

NOW = datetime(2026, 10, 10, 14, 32, 0)
EPOCH = datetime(1970, 1, 1)


def _host(**kw):
    base = dict(id=1, name="h1", enabled=True, maintenance=False, maintenance_until=None,
                probe_id=None, port_error=False, check_errors=None, latency_threshold_ms=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _row(success=True, age_s=30, latency=2.0):
    return {"success": success, "latency_ms": latency, "timestamp": NOW - timedelta(seconds=age_s)}


def _probe(silent_s=10, interval=60):
    return ProbeState(probe_id=7, name="probe-bern-01", interval_seconds=interval,
                      last_report=(NOW - EPOCH).total_seconds() - silent_s)


def test_fresh_success_is_up_with_observed_at():
    st = hs.evaluate(_host(), _row(), NOW)
    assert st.state == hs.STATE_UP
    assert st.observed_at == NOW - timedelta(seconds=30)
    assert st.fields()["observed_at"].endswith("Z")


def test_failed_check_is_down():
    assert hs.evaluate(_host(), _row(success=False), NOW).state == hs.STATE_DOWN


def test_no_result_is_unknown_never_up():
    assert hs.evaluate(_host(), None, NOW).state == hs.STATE_UNKNOWN


def test_stale_core_result_is_unknown():
    st = hs.evaluate(_host(), _row(age_s=600), NOW, ping_interval=60)
    assert st.state == hs.STATE_UNKNOWN
    assert "No check result for 10 min" in st.reason


def test_core_window_follows_the_ping_interval():
    # 10 min old is fine when checks run every 5 minutes (window 15 min).
    assert hs.evaluate(_host(), _row(age_s=600), NOW, ping_interval=300).state == hs.STATE_UP


def test_silent_probe_turns_a_green_host_unknown():
    host = _host(probe_id=7)
    st = hs.evaluate(host, _row(age_s=20), NOW, probe=_probe(silent_s=11 * 60))
    assert st.state == hs.STATE_UNKNOWN
    assert "probe-bern-01 silent 11 min" in st.reason
    assert st.reason_group == "probe probe-bern-01 silent 11 min"


def test_probe_that_never_reported_is_unknown():
    probe = ProbeState(probe_id=7, name="p", interval_seconds=60, last_report=None)
    assert hs.evaluate(_host(probe_id=7), _row(), NOW, probe=probe).state == hs.STATE_UNKNOWN


def test_missing_probe_is_unknown():
    st = hs.evaluate(_host(probe_id=7), _row(), NOW, probe=None, probe_missing=True)
    assert st.state == hs.STATE_UNKNOWN


def test_fresh_probe_result_counts():
    assert hs.evaluate(_host(probe_id=7), _row(age_s=30), NOW, probe=_probe()).state == hs.STATE_UP


def test_maintenance_wins_over_everything_but_disabled():
    maint = {"maintenance": True, "maintenance_window": {"name": "NAS firmware",
                                                         "ends_at": "2026-10-10T15:00:00Z"}}
    st = hs.evaluate(_host(probe_id=7), _row(success=False), NOW, maintenance=maint,
                     probe=_probe(silent_s=9999))
    assert st.state == hs.STATE_MAINTENANCE
    assert "NAS firmware" in st.reason and "15:00" in st.reason
    assert hs.evaluate(_host(enabled=False), _row(), NOW, maintenance=maint).state == hs.STATE_DISABLED


def test_check_errors_and_port_error_are_warning():
    st = hs.evaluate(_host(check_errors='{"https": "status 503"}'), _row(), NOW)
    assert st.state == hs.STATE_WARNING
    assert st.reason == "HTTPS check: status 503"
    assert hs.evaluate(_host(port_error=True), _row(), NOW).state == hs.STATE_WARNING


def test_service_incident_is_warning():
    incs = [{"id": 5, "rule": "agent_service", "title": "web01: service nginx not running"}]
    st = hs.evaluate(_host(), _row(), NOW, incidents=incs)
    assert st.state == hs.STATE_WARNING
    assert "nginx" in st.reason


def test_latency_over_threshold_is_degraded():
    assert hs.evaluate(_host(latency_threshold_ms=10), _row(latency=40), NOW).state == hs.STATE_DEGRADED
    # Global threshold applies when the host has none.
    assert hs.evaluate(_host(), _row(latency=40), NOW,
                       global_latency_threshold=20).state == hs.STATE_DEGRADED
    assert hs.evaluate(_host(), _row(latency=40), NOW).state == hs.STATE_UP


def test_other_open_incident_is_degraded_but_predictions_are_not():
    st = hs.evaluate(_host(), _row(), NOW, incidents=[{"id": 9, "rule": "log_anomaly", "title": "x"}])
    assert st.state == hs.STATE_DEGRADED
    st = hs.evaluate(_host(), _row(), NOW,
                     incidents=[{"id": 9, "rule": "learned_precursor", "title": "x"}])
    assert st.state == hs.STATE_UP


def test_down_beats_warning():
    st = hs.evaluate(_host(port_error=True), _row(success=False), NOW)
    assert st.state == hs.STATE_DOWN


def test_counts_and_reasons():
    states = [
        hs.HostState(hs.STATE_DOWN, "x", None, "behind SW-1"),
        hs.HostState(hs.STATE_DOWN, "y", None, "behind SW-1"),
        hs.HostState(hs.STATE_UNKNOWN, "z", None, "probe p silent 11 min"),
        hs.HostState(hs.STATE_UP, "ok", None, None),
    ]
    c = hs.counts(states)
    assert c["down"] == 2 and c["unknown"] == 1 and c["up"] == 1 and c["maintenance"] == 0
    reasons = hs.reasons_by_state(states)
    assert reasons["down"] == [{"text": "behind SW-1", "count": 2}]
    assert "up" not in reasons


def test_legacy_status_mapping():
    assert hs.legacy_status("degraded") == "online"
    assert hs.legacy_status("warning") == "online"
    assert hs.legacy_status("down") == "offline"
    assert hs.legacy_status("unknown") == "unknown"


def test_worst():
    assert hs.worst(["up", "unknown", "warning"]) == "warning"
    assert hs.worst([]) is None


def test_parse_host_ids():
    assert hs.parse_host_ids("[3,1]") == [3, 1]
    assert hs.parse_host_ids(None) is None
    assert hs.parse_host_ids("garbage") is None
    assert hs.parse_host_ids("[0, true, 4]") == [4]


async def test_batch_names_the_down_upstream(db):
    from unittest.mock import AsyncMock, patch

    from models.ping import PingHost

    sw = PingHost(name="SW-CORE", hostname="10.0.0.2")
    db.add(sw)
    await db.flush()
    pc = PingHost(name="pc", hostname="10.0.0.3", parent_id=sw.id)
    db.add(pc)
    await db.flush()
    now = datetime.utcnow()
    latest = {
        sw.id: {"success": 0, "latency_ms": None, "timestamp": now},
        pc.id: {"success": 0, "latency_ms": None, "timestamp": now},
    }
    with patch("services.clickhouse_client.get_latest_ping_per_host",
               new=AsyncMock(return_value=latest)):
        states = await hs.host_states(db, [sw, pc], now, topology={pc.id: sw.id, sw.id: None})
    assert states[sw.id].reason == "Last check failed"
    assert states[pc.id].reason_group == "behind SW-CORE"


async def test_batch_reads_probe_and_incidents(db):
    import json

    from models.agent import Agent
    from models.incident import Incident
    from models.ping import PingHost

    probe = Agent(name="probe-bern-01", token="x" * 64, is_probe=True,
                  last_seen=datetime.utcnow() - timedelta(minutes=11))
    db.add(probe)
    await db.flush()
    behind = PingHost(name="nas-be", hostname="10.2.0.5", probe_id=probe.id)
    web = PingHost(name="web", hostname="10.0.0.9")
    db.add_all([behind, web])
    await db.flush()
    db.add(Incident(rule="agent_service", title="web: service nginx not running",
                    severity="warning", status="open", host_ids=json.dumps([web.id])))
    await db.flush()
    now = datetime.utcnow()
    latest = {h.id: {"success": 1, "latency_ms": 1.0, "timestamp": now - timedelta(seconds=20)}
              for h in (behind, web)}
    states = await hs.host_states(db, [behind, web], now, latest=latest, topology=None)
    assert states[behind.id].state == hs.STATE_UNKNOWN
    assert states[web.id].state == hs.STATE_WARNING

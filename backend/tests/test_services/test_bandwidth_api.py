"""Traffic page data: the shapes and timestamps the page depends on."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from services import bandwidth as bw
from services import clickhouse_client as ch


def _sample(**fields):
    base = dict(timestamp=datetime.utcnow() - timedelta(seconds=30), source_type="unifi",
                source_id="aa", source_name="switch", interface_name="Port 1",
                rx_bytes=1, tx_bytes=1, rx_rate_bps=1000, tx_rate_bps=500)
    return {**base, **fields}


async def test_interface_timestamps_are_utc_with_z():
    """Without the Z a browser reads the time as local: every interface then
    looked an hour or two old and the page said "0 reporting"."""
    with patch.object(ch, "get_latest_bandwidth_per_iface", new=AsyncMock(return_value=[_sample()])):
        interfaces = await bw.get_bandwidth_interfaces()
        summary = await bw.get_bandwidth_summary()
    assert interfaces[0]["last_seen"].endswith("Z")
    assert summary["top_talkers"][0]["timestamp"].endswith("Z")


async def test_total_history_is_one_point_per_bucket():
    rows = [{"timestamp": datetime(2026, 11, 12, 20, 0), "rx_rate_bps": 3000, "tx_rate_bps": 900,
             "interfaces": 3}]
    with patch.object(ch, "get_bandwidth_total_history_ch", new=AsyncMock(return_value=rows)) as q:
        points = await bw.get_bandwidth_total_history(hours=6)
    assert q.await_args.kwargs == {"hours": 6}
    assert points == [{"timestamp": "2026-11-12T20:00:00Z", "rx_rate_bps": 3000,
                       "tx_rate_bps": 900, "interfaces": 3}]


def test_bucket_width_follows_the_range():
    assert [ch.bandwidth_bucket_seconds(h) for h in (1, 6, 24, 168)] == [60, 300, 900, 3600]


async def test_total_history_query_is_tenant_scoped_and_bucketed():
    captured = {}

    async def fake_query(sql, params):
        captured["sql"], captured["params"] = sql, params
        return []

    with patch.object(ch, "query", new=fake_query), \
         patch.object(ch, "tenant_clause", new=lambda params: "tenant_id = 1"):
        await ch.get_bandwidth_total_history_ch(hours=24)
    assert "tenant_id = 1" in captured["sql"]
    assert "toStartOfInterval" in captured["sql"]
    assert captured["params"]["b"] == 900 and captured["params"]["h"] == 24

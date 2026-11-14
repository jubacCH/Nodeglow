"""UniFi gateway speedtest results.

The controller answered oldest first (it ignored _sort while "time" was not
requested), so the "latest" result shown was days old, and every real run had
an all-zero twin a minute later that drew a dip to 0 into the chart.
"""
from integrations.unifi import SPEEDTEST_KEEP, parse_unifi_data, speedtests_from_v2

DAY = 86_400


def _run(ts, down, up, latency=1):
    return {"time": ts * 1000, "xput_download": down, "xput_upload": up, "latency": latency}


def test_latest_is_the_newest_run_and_zero_runs_are_dropped():
    t0 = 1_793_000_000
    raw = [_run(t0, 8117, 7799), _run(t0 + 60, 0, 0, 0),
           _run(t0 + DAY, 8118, 7820), _run(t0 + DAY + 60, 0, 0, 0)]
    data = parse_unifi_data([], [], [], raw_speedtest=raw)

    assert [r["download_mbps"] for r in data["speedtest"]] == [8118.0, 8117.0]
    assert data["speedtest_latest"]["download_mbps"] == 8118.0


def test_no_runs_means_no_latest():
    data = parse_unifi_data([], [], [], raw_speedtest=[_run(1_793_000_000, 0, 0)])
    assert data["speedtest"] == [] and data["speedtest_latest"] is None


def test_the_v2_list_of_current_network_versions_is_read():
    """The old report answers with an empty list there. Rows as the gateway
    sent them: oldest first, and one per WAN, the unused one all zero."""
    payload = [
        {"download_mbps": 8152, "upload_mbps": 7967, "latency_ms": 2, "time": 1_786_942_346_804,
         "interface_name": "eth8", "wan_networkgroup": "WAN", "id": "a"},
        {"download_mbps": 8201, "upload_mbps": 7990, "latency_ms": 2, "time": 1_794_635_500_000,
         "interface_name": "eth8", "wan_networkgroup": "WAN", "id": "b"},
        {"download_mbps": 0, "upload_mbps": 0, "latency_ms": 0, "time": 1_794_635_579_438,
         "interface_name": "eth12", "wan_networkgroup": "WAN2", "id": "c"},
    ]
    data = parse_unifi_data([], [], [], raw_speedtest=speedtests_from_v2(payload))
    assert [r["download_mbps"] for r in data["speedtest"]] == [8201.0, 8152.0]
    assert data["speedtest_latest"] == {"timestamp": "2026-11-14 05:51", "download_mbps": 8201.0,
                                        "upload_mbps": 7990.0, "latency_ms": 2.0}
    # The same list wrapped in an envelope, and something that is not a list.
    assert len(speedtests_from_v2({"data": payload})) == 3
    assert speedtests_from_v2({"error": "nope"}) == [] and speedtests_from_v2(None) == []


def test_only_the_newest_runs_are_kept():
    raw = [_run(1_793_000_000 + i * DAY, 8000 + i, 7000) for i in range(SPEEDTEST_KEEP + 15)]
    data = parse_unifi_data([], [], [], raw_speedtest=raw)
    assert len(data["speedtest"]) == SPEEDTEST_KEEP
    assert data["speedtest"][0]["download_mbps"] == 8000 + SPEEDTEST_KEEP + 14

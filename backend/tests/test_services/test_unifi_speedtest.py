"""UniFi gateway speedtest results.

The controller answered oldest first (it ignored _sort while "time" was not
requested), so the "latest" result shown was days old, and every real run had
an all-zero twin a minute later that drew a dip to 0 into the chart.
"""
from integrations.unifi import parse_unifi_data

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

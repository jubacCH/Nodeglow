"""Prometheus integration: text-format parser, rule parser/evaluator, alerts, collect()."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import httpx
import pytest

from integrations import prometheus as prom
from integrations.prometheus import (
    ExpositionError,
    PrometheusIntegration,
    RuleError,
    build_snapshot,
    config_problems,
    evaluate_scrape,
    parse_exposition,
    parse_rule,
    parse_rules,
    validate_config,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def samples(text):
    return parse_exposition(text).samples


# ── Exposition parser ────────────────────────────────────────────────────────


def test_basic_sample_and_metadata():
    exp = parse_exposition(
        "# HELP up Whether the target is up.\n"
        "# TYPE up gauge\n"
        "up 1\n"
        'http_requests_total{method="get",code="200"} 1027 1395066363000\n'
    )
    assert exp.series == 2
    assert exp.families["up"].type == "gauge"
    assert exp.families["up"].help == "Whether the target is up."
    s = exp.samples[1]
    assert (s.name, s.labels, s.value, s.timestamp) == (
        "http_requests_total", {"method": "get", "code": "200"}, 1027.0, 1395066363000.0)


def test_label_escapes_quotes_backslashes_newlines():
    s = samples(r'm{a="x\"y",b="back\\slash",c="line\nbreak"} 1')[0]
    assert s.labels == {"a": 'x"y', "b": "back\\slash", "c": "line\nbreak"}


def test_label_value_may_contain_braces_commas_and_hash():
    s = samples('m{a="}, b=\\"{#",c="2"} 3')[0]
    assert s.labels == {"a": '}, b="{#', "c": "2"}
    assert s.value == 3.0


def test_unicode_line_separator_inside_label_value_does_not_split_the_line():
    s = samples('m{a="x y"} 1\nn 2')
    assert s[0].labels == {"a": "x y"} and s[1].name == "n"


def test_trailing_comma_and_spaces_in_label_set():
    assert samples('m{ a = "1" , b="2" , } 5')[0].labels == {"a": "1", "b": "2"}
    assert samples("m{} 5")[0].labels == {}


@pytest.mark.parametrize("text,expected", [
    ("m NaN", "nan"), ("m +Inf", math.inf), ("m -Inf", -math.inf), ("m Inf", math.inf),
    ("m 1e3", 1000.0), ("m -2.5E-1", -0.25), ("m .5", 0.5), ("m 3.", 3.0), ("m +7", 7.0),
    ("m nan", "nan"), ("m INF", math.inf),
])
def test_special_and_scientific_values(text, expected):
    v = samples(text)[0].value
    if expected == "nan":
        assert math.isnan(v)
    else:
        assert v == expected


def test_timestamp_seconds_float_and_negative_ms():
    assert samples("m 1 1700000000.5")[0].timestamp == 1700000000.5
    assert samples("m 1 -5")[0].timestamp == -5.0
    assert samples("m 1")[0].timestamp is None


def test_openmetrics_eof_and_exemplar():
    exp = parse_exposition(
        "# TYPE lat histogram\n"
        'lat_bucket{le="0.1"} 5 # {trace_id="abc"} 0.05 1700000000\n'
        'lat_bucket{le="+Inf"} 9\n'
        "lat_count 9\n"
        "lat_sum 1.5\n"
        "# EOF\n"
    )
    assert exp.eof
    assert exp.series == 4
    assert exp.samples[0].value == 5.0 and exp.samples[0].timestamp is None


def test_content_after_eof_is_an_error_but_blank_lines_are_fine():
    parse_exposition("m 1\n# EOF\n\n  \n")
    with pytest.raises(ExpositionError, match="line 3.*EOF"):
        parse_exposition("m 1\n# EOF\nm 2\n")


def test_histogram_and_summary_samples_count_into_their_family():
    exp = parse_exposition(
        "# TYPE lat histogram\n"
        'lat_bucket{le="1"} 1\nlat_bucket{le="+Inf"} 2\nlat_sum 3\nlat_count 2\n'
        "# TYPE rpc summary\n"
        'rpc{quantile="0.5"} 1\nrpc_sum 10\nrpc_count 4\n'
        "# TYPE reqs counter\nreqs_total 5\nreqs_created 1.7e9\n"
        "plain_count 3\n"   # undeclared: its own family, not mistaken for a histogram
    )
    fam = {n: f.samples for n, f in exp.families.items()}
    assert fam["lat"] == 4 and fam["rpc"] == 3 and fam["reqs"] == 2
    assert fam["plain_count"] == 1 and "plain" not in fam
    assert exp.samples[0].family == "lat"


def test_suffix_only_joins_a_family_of_the_right_type():
    exp = parse_exposition("# TYPE x gauge\nx_count 1\n")
    assert exp.families["x_count"].samples == 1 and exp.families["x"].samples == 0


def test_unknown_comments_help_without_text_and_unit():
    exp = parse_exposition("# just a remark\n# HELP m\n# UNIT m seconds\n# TYPE m gauge\nm 1\n")
    assert exp.families["m"].help == "" and exp.series == 1


def test_crlf_line_endings():
    assert parse_exposition("a 1\r\nb 2\r\n").series == 2


def test_streaming_callback_does_not_retain_samples():
    seen = []
    exp = parse_exposition("a 1\nb 2\n", seen.append)
    assert [s.name for s in seen] == ["a", "b"] and exp.samples == []


@pytest.mark.parametrize("text", [
    "<html>not metrics</html>",          # an error page instead of an exporter
    "m",                                  # no value
    "m abc",                              # invalid number
    "m 1_000",                            # python-only number syntax
    "m 1 2 3",                            # too many fields
    "m 1 notatimestamp",
    "m{a=1} 1",                           # unquoted label value
    'm{a="x} 1',                          # unterminated string
    'm{a="x"',                            # unterminated label set
    'm{a="x" b="y"} 1',                   # missing comma
    'm{a="x",a="y"} 1',                   # duplicate label
    r'm{a="bad\q"} 1',                    # invalid escape
    "m{1a=\"x\"} 1",                      # invalid label name
    "1m 1",                               # invalid metric name
    "m{a=\"x\"}1",                        # no whitespace before the value
    "# TYPE m bogus",                     # unknown type
    "# TYPE m",                           # incomplete
    "# TYPE 1m gauge",
    "# HELP",
])
def test_malformed_input_raises_with_line_number(text):
    with pytest.raises(ExpositionError, match=r"^line \d+:"):
        parse_exposition("ok 1\n" + text)


def test_error_reports_the_right_line():
    with pytest.raises(ExpositionError, match="^line 3:"):
        parse_exposition("a 1\nb 2\nbroken line here\n")


def test_overlong_line_is_rejected():
    with pytest.raises(ExpositionError, match="too long"):
        parse_exposition("m{a=\"" + "x" * (prom.MAX_LINE_BYTES + 1) + "\"} 1")


# ── Rule parser ──────────────────────────────────────────────────────────────


def test_rule_with_matchers_and_title():
    r = parse_rule('critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9 # Root disk almost full', 4)
    assert (r.severity, r.name, r.op, r.threshold, r.title, r.line) == (
        "critical", "node_filesystem_avail_bytes", "<", 1e9, "Root disk almost full", 4)
    assert r.matchers == (("mountpoint", "=", "/"),)
    assert r.expr == 'node_filesystem_avail_bytes{mountpoint="/"} < 1e9'


def test_rule_simple_and_case_insensitive_severity():
    r = parse_rule("WARNING up == 0")
    assert (r.severity, r.name, r.matchers, r.op, r.threshold, r.title) == ("warning", "up", (), "==", 0.0, "")
    assert r.expr == "up == 0"


@pytest.mark.parametrize("op", ["<", "<=", ">", ">=", "==", "!="])
def test_every_operator_parses(op):
    assert parse_rule(f"info m {op} 5").op == op
    assert parse_rule(f"info m{op}5").op == op           # no spaces
    assert parse_rule(f'info m{{a="b"}}{op}5').op == op


def test_rule_not_equal_matcher_and_multiple_matchers_and_spaces():
    r = parse_rule('warning m{ a = "1" , b != "x" } >= -2.5')
    assert r.matchers == (("a", "=", "1"), ("b", "!=", "x"))
    assert r.threshold == -2.5
    assert r.expr == 'm{a="1",b!="x"} >= -2.5'


def test_hash_inside_quoted_matcher_is_not_a_title():
    r = parse_rule('warning m{a="x # y"} > 1 # real title')
    assert r.matchers == (("a", "=", "x # y"),) and r.title == "real title"


def test_escaped_quote_in_matcher_value():
    r = parse_rule(r'warning m{a="say \"hi\""} > 1')
    assert r.matchers == (("a", "=", 'say "hi"'),)
    assert r.expr == r'm{a="say \"hi\""} > 1'


def test_infinite_threshold_is_allowed_nan_is_not():
    assert parse_rule("critical m < +Inf").threshold == math.inf
    with pytest.raises(RuleError, match="NaN"):
        parse_rule("critical m < NaN")


@pytest.mark.parametrize("line,fragment", [
    ("up == 0", "severity"),                      # severity missing -> 'up' is read as one
    ("fatal up == 0", "severity"),
    ("warning", "expected"),
    ("warning 9bad == 0", "metric name"),
    ("warning up", "operator"),
    ("warning up = 0", "operator"),               # single '=' is not an operator
    ("warning up ~ 0", "operator"),
    ("warning up ==", "missing number"),
    ("warning up == abc", "invalid number"),
    ("warning up == 1 2", "unexpected text"),
    ("warning up == 1_0", "invalid number"),
    ('warning m{a=~"x.*"} > 1', "regex"),
    ('warning m{a!~"x.*"} > 1', "regex"),
    ('warning m{a="x" > 1', "expected ',' or '}'"),
    ('warning m{a="x"', "expected ',' or '}'"),
    ("warning m{a=x} > 1", "quoted"),
    ('warning m{a>"x"} > 1', "'=' or '!='"),
    ('warning m{1a="x"} > 1', "label name"),
    ('warning m{a="x} > 1', "unterminated"),
])
def test_malformed_rules_raise_rule_error(line, fragment):
    with pytest.raises(RuleError, match=fragment):
        parse_rule(line)


def test_parse_rules_collects_errors_without_dropping_good_rules():
    rules, errors = parse_rules(
        "# comment\n\nwarning up == 0\nbogus line\ncritical m{a=~\"x\"} > 1\ninfo up2 > 1  # t\n")
    assert [r.name for r in rules] == ["up", "up2"]
    assert [(e["line"], e["text"]) for e in errors] == [(4, "bogus line"), (5, 'critical m{a=~"x"} > 1')]
    assert all(e["error"] for e in errors)


def test_duplicate_rule_is_an_error():
    rules, errors = parse_rules("warning up == 0\ncritical up == 0 # same expr\n")
    assert len(rules) == 1 and "duplicate" in errors[0]["error"] and errors[0]["line"] == 2


def test_rule_count_and_size_limits():
    text = "\n".join(f"warning m{i} > 1" for i in range(prom.MAX_RULES + 3))
    rules, errors = parse_rules(text)
    assert len(rules) == prom.MAX_RULES and len(errors) == 3 and "too many" in errors[0]["error"]
    rules, errors = parse_rules("x" * (prom.MAX_RULES_TEXT + 1))
    assert rules == [] and "exceeds" in errors[0]["error"]


def test_rules_none_and_empty():
    assert parse_rules(None) == ([], [])
    assert parse_rules("") == ([], [])


# ── Evaluation ───────────────────────────────────────────────────────────────

SCRAPE = """\
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{mountpoint="/",device="sda1"} 5e8
node_filesystem_avail_bytes{mountpoint="/data",device="sdb1"} 5e12
node_filesystem_avail_bytes{mountpoint="/boot"} 2e8
temp{zone="a"} NaN
temp{zone="b"} 71
no_label_metric 4
"""


def evaluate(rules_text, scrape=SCRAPE):
    rules, errors = parse_rules(rules_text)
    assert errors == []
    return evaluate_scrape(scrape, rules)


def test_matcher_equal_and_not_equal_and_absent_label():
    res = evaluate(
        'critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9\n'
        'warning node_filesystem_avail_bytes{mountpoint!="/"} < 1e9\n'
        'warning no_label_metric{missing=""} == 4\n'
        'warning no_label_metric{missing!=""} == 4\n')
    r = res["rules"]
    assert (r[0]["matched"], r[0]["firing"]) == (1, 1)
    assert r[0]["firing_series"][0]["labels"] == {"mountpoint": "/", "device": "sda1"}
    assert (r[1]["matched"], r[1]["firing"]) == (2, 1)        # /data matches but 5e12 is not < 1e9
    assert r[1]["firing_series"][0]["labels"] == {"mountpoint": "/boot"}
    assert (r[2]["matched"], r[2]["firing"]) == (1, 1)        # absent label == ""
    assert r[3]["matched"] == 0


def test_rule_that_matches_nothing_is_visible():
    res = evaluate("warning up == 0\n")
    assert res["rules"][0]["matched"] == 0 and res["rules"][0]["firing"] == 0


def test_nan_satisfies_no_comparison_but_is_counted():
    res = evaluate("warning temp != 100\nwarning temp > 70\n")
    assert (res["rules"][0]["matched"], res["rules"][0]["nan"], res["rules"][0]["firing"]) == (2, 1, 1)
    assert res["rules"][1]["firing"] == 1


def test_comparison_edges():
    scrape = "m 5\n"
    for op, fires in [("<", False), ("<=", True), (">", False), (">=", True), ("==", True), ("!=", False)]:
        assert evaluate(f"warning m {op} 5", scrape)["rules"][0]["firing"] == int(fires), op


def test_infinite_values_are_json_safe():
    res = evaluate("critical m > 1e300", "m +Inf\nm -Inf\nm NaN\n")
    json.dumps(res, allow_nan=False)
    assert res["rules"][0]["firing_series"][0]["value"] == "+Inf"
    assert [r["value"] for r in res["preview"]] == ["+Inf", "-Inf", "NaN"]


def test_snapshot_is_bounded():
    n = 5000
    text = "# TYPE big gauge\n" + "".join(f'big{{i="{i}"}} {i}\n' for i in range(n))
    text += "".join(f"fam{i} 1\n" for i in range(prom.MAX_FAMILIES + 50))
    res = evaluate("warning big > -1\nwarning big{i=\"7\"} == 7", text)
    assert res["series"] == n + prom.MAX_FAMILIES + 50
    assert len(res["preview"]) == prom.PREVIEW_SERIES
    assert len(res["families"]) == prom.MAX_FAMILIES and res["families_truncated"] == 51
    big = next(f for f in res["families"] if f["name"] == "big")
    assert big["samples"] == n and big["type"] == "gauge"
    r0, r1 = res["rules"]
    assert r0["matched"] == n and r0["firing"] == n
    assert len(r0["firing_series"]) == prom.MAX_ALERTS_PER_RULE
    assert len(r0["sample_series"]) == prom.SAMPLE_ROWS_PER_RULE
    assert r1["matched"] == 1
    assert len(json.dumps(res)) < 400_000


def test_alerted_subset_is_stable_regardless_of_scrape_order():
    lines = [f'm{{i="{i:03d}"}} 1\n' for i in range(120)]
    a = evaluate("warning m > 0", "".join(lines))["rules"][0]["firing_series"]
    b = evaluate("warning m > 0", "".join(reversed(lines)))["rules"][0]["firing_series"]
    assert [r["series"] for r in a] == [r["series"] for r in b]


def test_malformed_scrape_propagates():
    with pytest.raises(ExpositionError):
        evaluate("warning up == 0", "garbage here")


# ── Snapshot building ────────────────────────────────────────────────────────


def test_build_snapshot_shape():
    rules, _ = parse_rules("warning up == 0")
    snap = build_snapshot("up 1\n", "text/plain; version=0.0.4", 5, 12, rules, [], NOW)
    assert snap["collected_at"] == NOW.isoformat()
    assert snap["scrape"] == {"bytes": 5, "duration_ms": 12, "format": "prometheus", "series": 1, "families": 1}
    assert snap["unavailable"] == [] and snap["preview_truncated"] is False


def test_empty_scrape_is_a_failure_not_healthy():
    with pytest.raises(RuntimeError, match="no samples"):
        build_snapshot("# HELP x y\n", "text/plain", 10, 1, [])
    with pytest.raises(RuntimeError, match="no samples"):
        build_snapshot("", "text/plain", 0, 1, [])


def test_openmetrics_without_eof_is_truncated():
    with pytest.raises(RuntimeError, match="truncated"):
        build_snapshot("m 1\n", "application/openmetrics-text; version=1.0.0", 4, 1, [])
    snap = build_snapshot("m 1\n# EOF\n", "application/openmetrics-text; version=1.0.0", 4, 1, [])
    assert snap["scrape"]["format"] == "openmetrics"


def test_invalid_exposition_is_a_failure():
    with pytest.raises(RuntimeError, match="Not a valid Prometheus exposition: line 1"):
        build_snapshot("<html>", "text/html", 6, 1, [])


# ── Alerts ───────────────────────────────────────────────────────────────────


def snapshot(rules_text, scrape=SCRAPE):
    rules, errors = parse_rules(rules_text)
    return build_snapshot(scrape, "text/plain", len(scrape), 1, rules, errors, NOW)


def alerts_for(rules_text, scrape=SCRAPE):
    return PrometheusIntegration({}).parse_alerts(snapshot(rules_text, scrape))


def test_alert_per_matching_series_with_stable_entities():
    out = alerts_for(
        'critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9 # Root disk almost full\n'
        "warning node_filesystem_avail_bytes < 1e9\n")
    assert [(a.severity, a.title) for a in out] == [
        ("critical", "Root disk almost full"), ("warning", "node_filesystem_avail_bytes < 1e9"),
        ("warning", "node_filesystem_avail_bytes < 1e9")]
    assert len({a.entity for a in out}) == 3                   # each series has its own entity
    again = alerts_for(
        'critical node_filesystem_avail_bytes{mountpoint="/"} < 1e9 # Root disk almost full\n'
        "warning node_filesystem_avail_bytes < 1e9\n")
    assert [a.entity for a in out] == [a.entity for a in again]
    assert "5e+08" in out[0].detail or "500000000" in out[0].detail


def test_title_change_does_not_change_the_entity():
    a = alerts_for("critical no_label_metric > 1 # one")[0]
    b = alerts_for("critical no_label_metric > 1 # two")[0]
    assert a.entity == b.entity and a.title != b.title


def test_rule_matching_no_series_warns():
    out = alerts_for("critical up == 0")
    assert len(out) == 1
    assert (out[0].severity, out[0].title) == ("warning", "Rule matches no series")
    assert "up == 0" in out[0].entity


def test_rule_that_matches_but_does_not_fire_is_silent():
    assert alerts_for("critical no_label_metric > 100") == []


def test_malformed_rule_warns_and_is_in_data():
    snap = snapshot("critical up ==\nwarning no_label_metric > 1")
    assert snap["rule_errors"][0]["line"] == 1
    out = PrometheusIntegration({}).parse_alerts(snap)
    titles = sorted(a.title for a in out)
    assert titles == ["Alert rule is malformed", "no_label_metric > 1"]
    bad = next(a for a in out if a.title == "Alert rule is malformed")
    assert bad.severity == "warning" and "Line 1" in bad.detail


def test_nan_series_warn():
    out = alerts_for("critical temp > 100")
    assert [a.title for a in out] == ["Rule series report NaN"]


def test_alerts_capped_per_rule_with_summary():
    n = prom.MAX_ALERTS_PER_RULE + 30
    text = "".join(f'm{{i="{i:04d}"}} 1\n' for i in range(n))
    out = alerts_for("critical m > 0", text)
    assert len(out) == prom.MAX_ALERTS_PER_RULE + 1
    summary = out[-1]
    assert summary.severity == "critical" and "+30 more" in summary.detail
    assert len({a.entity for a in out}) == len(out)


def test_summary_alert_identity_does_not_change_with_the_count():
    def summary(extra):
        n = prom.MAX_ALERTS_PER_RULE + extra
        return alerts_for("critical m > 0", "".join(f'm{{i="{i:04d}"}} 1\n' for i in range(n)))[-1]

    a, b = summary(30), summary(31)
    # Title and entity form the incident key: a changing count must not open a new incident.
    assert (a.title, a.entity) == (b.title, b.entity)


def test_stored_rows_bound_long_series_and_label_values():
    text = f'm{{a="{"x" * 5000}1"}} 1\nm{{a="{"x" * 5000}2"}} 1\n'
    res = evaluate("warning m > 0", text)
    rows = res["rules"][0]["firing_series"] + res["preview"]
    assert all(len(r["series"]) <= prom.MAX_SERIES_TEXT for r in rows)
    assert all(len(v) <= prom.MAX_LABEL_VALUE for r in rows for v in r["labels"].values())
    fs = res["rules"][0]["firing_series"]
    assert fs[0]["series"] != fs[1]["series"]


def test_exactly_at_cap_has_no_summary():
    text = "".join(f'm{{i="{i}"}} 1\n' for i in range(prom.MAX_ALERTS_PER_RULE))
    assert len(alerts_for("warning m > 0", text)) == prom.MAX_ALERTS_PER_RULE


def test_long_entities_stay_bounded_and_distinct():
    text = f'm{{a="{"x" * 400}1"}} 1\nm{{a="{"x" * 400}2"}} 1\n'
    out = alerts_for("warning m > 0", text)
    assert all(len(a.entity) <= 200 for a in out) and out[0].entity != out[1].entity


def test_parse_alerts_tolerates_garbage():
    p = PrometheusIntegration({})
    assert p.parse_alerts({}) == []
    assert p.parse_alerts({"rules": "x", "rule_errors": None}) == []
    assert p.parse_alerts({"rules": [None, {}, {"expr": "m > 1"}, {"expr": "m > 1", "matched": "?"}]}) == []
    assert p.parse_alerts({"rules": [{"expr": "m > 1", "matched": 3, "firing_series": [None, 5], "firing": "x"}]}) == []
    assert p.parse_alerts({"rule_errors": [None, "x", {}]})[0].title == "Alert rule is malformed"


# ── Config validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("cfg,fragment", [
    ({}, "http"),
    ({"url": "ftp://x/metrics"}, "http"),
    ({"url": "http://user:pw@x/metrics"}, "not into the URL"),
    ({"url": "http://x/metrics", "bearer_token": "t", "username": "u", "password": "p"}, "not both"),
    ({"url": "http://x/metrics", "username": "u"}, "both username and password"),
])
def test_config_problems(cfg, fragment):
    assert any(fragment in p for p in config_problems(cfg))


def test_validate_config_rejects_bad_rules_early_but_accepts_good_config():
    ok = {"url": "https://x:9100/metrics", "rules": "warning up == 0"}
    assert validate_config(ok) == []
    bad = dict(ok, rules="warning up == 0\nnonsense up == 0")
    assert validate_config(bad) == ["Rule line 2: unknown severity 'nonsense' (use critical, warning or info)"]


# ── collect() through a mocked transport ─────────────────────────────────────


def _patch_client(monkeypatch, handler, seen=None):
    def factory(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(prom, "safe_async_client", factory)


CFG = {"url": "http://exporter.local:9100/metrics",
       "rules": "critical no_label_metric > 1 # too high\nwarning up == 0\nbogus"}


async def test_collect_success(monkeypatch):
    seen_requests = []

    def handler(request):
        seen_requests.append(request)
        return httpx.Response(200, text=SCRAPE, headers={"content-type": "text/plain; version=0.0.4"})

    _patch_client(monkeypatch, handler)
    res = await PrometheusIntegration(CFG).collect()
    assert res.success, res.error
    d = res.data
    assert d["scrape"]["series"] == 6 and len(d["rules"]) == 2
    assert d["rule_errors"][0]["line"] == 3
    assert "Authorization" not in seen_requests[0].headers
    titles = {a.title for a in PrometheusIntegration(CFG).parse_alerts(d)}
    assert titles == {"too high", "Rule matches no series", "Alert rule is malformed"}


async def test_collect_sends_bearer_or_basic_auth(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, text="up 1\n")

    _patch_client(monkeypatch, handler)
    assert (await PrometheusIntegration(dict(CFG, bearer_token="tok")).collect()).success
    assert (await PrometheusIntegration(dict(CFG, username="u", password="p")).collect()).success
    assert seen[0] == "Bearer tok" and seen[1].startswith("Basic ")


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(401, text="nope"),
    httpx.Response(302, headers={"location": "http://elsewhere/"}),
    httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}),
    httpx.Response(200, text=""),
    httpx.Response(200, text="m 1\n", headers={"content-type": "application/openmetrics-text"}),
    httpx.Response(200, content=b"m 1\n\xff\xfe"),
])
async def test_failing_core_request_is_not_success(monkeypatch, response):
    _patch_client(monkeypatch, lambda request: response)
    res = await PrometheusIntegration(CFG).collect()
    assert res.success is False and res.error and res.data is None


async def test_connection_error_is_failure_and_does_not_leak_secrets(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("cannot reach http://exporter.local:9100/metrics with tok-123 / pw-456")

    _patch_client(monkeypatch, handler)
    cfg = dict(CFG, bearer_token="tok-123", password="pw-456", username="u")
    cfg.pop("bearer_token")
    res = await PrometheusIntegration(dict(cfg)).collect()
    assert res.success is False
    assert "pw-456" not in res.error and "exporter.local" not in res.error
    res = await PrometheusIntegration(dict(CFG, bearer_token="tok-123")).collect()
    assert "tok-123" not in res.error


async def test_oversized_response_fails_with_a_clear_error(monkeypatch):
    monkeypatch.setattr(prom, "MAX_RESPONSE_BYTES", 1000)
    _patch_client(monkeypatch, lambda request: httpx.Response(200, text="m 1\n" * 1000))
    res = await PrometheusIntegration(CFG).collect()
    assert res.success is False and "limit" in res.error


async def test_invalid_config_fails_before_any_request(monkeypatch):
    def handler(request):
        raise AssertionError("no request expected")

    _patch_client(monkeypatch, handler)
    res = await PrometheusIntegration({"url": "not a url"}).collect()
    assert res.success is False and "http" in res.error
    res = await PrometheusIntegration({}).collect()
    assert res.success is False


@pytest.mark.parametrize("status,expected", [(200, True), (401, False)])
async def test_health_check_uses_status_without_reading_the_body(monkeypatch, status, expected):
    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("health check must not read the body")
            yield b""  # pragma: no cover

    _patch_client(monkeypatch, lambda request: httpx.Response(status, stream=Endless()))
    assert await PrometheusIntegration(CFG).health_check() is expected


def test_a_label_set_without_series_is_not_a_broken_rule():
    """Found against a live exporter: `jobs_total{status="error"} > 0` has no
    series until the first error. The metric exists, so the rule is fine and
    must not warn; only a metric that is missing altogether (a typo) does."""
    out = alerts_for('warning node_filesystem_avail_bytes{mountpoint="/does/not/exist"} < 1')
    assert out == []
    rule = snapshot('warning node_filesystem_avail_bytes{mountpoint="/does/not/exist"} < 1', SCRAPE)["rules"][0]
    assert rule["matched"] == 0 and rule["name_seen"] > 0

    typo = alerts_for("warning node_filesystem_avail_bytez < 1")
    assert [(a.severity, a.title) for a in typo] == [("warning", "Rule matches no series")]


def test_snapshots_from_before_name_seen_still_warn():
    out = PrometheusIntegration({}).parse_alerts(
        {"rules": [{"expr": "up == 0", "severity": "critical", "matched": 0, "nan": 0, "firing": 0}]})
    assert [a.title for a in out] == ["Rule matches no series"]

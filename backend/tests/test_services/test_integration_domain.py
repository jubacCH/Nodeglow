"""Domain expiry integration: list parsing, RDAP parsing, alerts, collect()."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from integrations import domain as dom
from integrations.domain import (
    DomainIntegration,
    build_snapshot,
    extract_expiry,
    extract_registrar,
    is_hold_status,
    normalize_domain,
    parse_bootstrap,
    parse_domain_list,
    parse_rdap_datetime,
    parse_rdap_domain,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

BOOTSTRAP = {"services": [
    [["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
    [["uk"], ["http://rdap.nominet.uk/uk/", "https://rdap.nominet.uk/uk/"]],
    [["nl"], ["https://rdap.sidn.nl/"]],
]}


def rdap_body(expiry="2027-08-13T04:00:00Z", status=("client delete prohibited",), registrar="Example Registrar"):
    body = {
        "objectClassName": "domain",
        "status": list(status),
        "events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"}],
        "entities": [{"roles": ["registrar"],
                      "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", registrar]]]}],
    }
    if expiry:
        body["events"].append({"eventAction": "expiration", "eventDate": expiry})
    return body


# ── Domain list ──────────────────────────────────────────────────────────────


def test_normalize_domain_accepts_and_lowercases():
    assert normalize_domain(" Example.COM. ") == "example.com"
    assert normalize_domain("sub.example.co.uk") == "sub.example.co.uk"


def test_normalize_domain_punycodes_idn():
    assert normalize_domain("bücher.example") == "xn--bcher-kva.example"


@pytest.mark.parametrize("bad", ["", "localhost", "example", "http://example.com", "example.com/path",
                                 "-bad.com", "bad-.com", "a b.com", "1.2.3.4", "user@example.com",
                                 "example.com:443", "a..com", "x" * 64 + ".com"])
def test_normalize_domain_rejects(bad):
    assert normalize_domain(bad) is None


def test_parse_domain_list_dedupes_and_reports_invalid():
    valid, invalid = parse_domain_list("example.com\n# comment\nExample.com  # dup\nfoo\nexample.org, example.net")
    assert valid == ["example.com", "example.org", "example.net"]
    assert invalid == ["foo"]


def test_parse_domain_list_empty_and_none():
    assert parse_domain_list("") == ([], [])
    assert parse_domain_list(None) == ([], [])


# ── RDAP parsing ─────────────────────────────────────────────────────────────


def test_parse_bootstrap_prefers_https_and_lowercases():
    m = parse_bootstrap(BOOTSTRAP)
    assert m["uk"] == "https://rdap.nominet.uk/uk/"
    assert m["com"] == m["net"] == "https://rdap.verisign.com/com/v1/"


@pytest.mark.parametrize("bad", [None, [], {}, {"services": "x"}, {"services": []}, {"services": [["x"]]}])
def test_parse_bootstrap_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_bootstrap(bad)


def test_parse_rdap_datetime_variants():
    assert parse_rdap_datetime("2027-08-13T04:00:00Z") == datetime(2027, 8, 13, 4, tzinfo=timezone.utc)
    # nine fractional digits as sent by some registries
    assert parse_rdap_datetime("2026-11-12T21:29:46.448364087Z").year == 2026
    # offsets are converted to UTC
    assert parse_rdap_datetime("2024-12-19T13:33:43+01:00") == datetime(2024, 12, 19, 12, 33, 43, tzinfo=timezone.utc)
    # naive input is taken as UTC
    assert parse_rdap_datetime("2027-01-01T00:00:00").tzinfo is not None
    for bad in (None, "", "soon", 5, "2027-13-45T00:00:00Z"):
        assert parse_rdap_datetime(bad) is None


def test_extract_expiry_takes_earliest_and_ignores_garbage():
    body = {"events": [
        {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
        {"eventAction": "Expiration", "eventDate": "2028-01-01T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "garbage"},
        "not a dict",
        {"eventAction": "registration", "eventDate": "1999-01-01T00:00:00Z"},
    ]}
    assert extract_expiry(body) == datetime(2028, 1, 1, tzinfo=timezone.utc)
    assert extract_expiry({}) is None
    assert extract_expiry({"events": None}) is None


def test_extract_registrar_variants():
    assert extract_registrar(rdap_body()) == "Example Registrar"
    nested = {"entities": [{"roles": ["reseller"], "entities": [{"roles": ["registrar"], "handle": "H-1"}]}]}
    assert extract_registrar(nested) == "H-1"
    ids = {"entities": [{"roles": ["registrar"], "publicIds": [{"type": "IANA", "identifier": "292"}]}]}
    assert extract_registrar(ids) == "292"
    assert extract_registrar({"entities": "x"}) == ""
    assert extract_registrar({}) == ""


@pytest.mark.parametrize("s,expected", [
    ("client hold", True), ("serverHold", True), ("Server-Hold", True), ("clientHold", True),
    ("client delete prohibited", False), ("active", False), ("pending delete", False),
])
def test_is_hold_status(s, expected):
    assert is_hold_status(s) is expected


def test_parse_rdap_domain_ok():
    rec = parse_rdap_domain("example.com", rdap_body(), "https://x/")
    assert rec["kind"] == "ok"
    assert rec["expiry"] == "2027-08-13T04:00:00+00:00"
    assert rec["registrar"] == "Example Registrar"
    assert rec["status"] == ["client delete prohibited"]


def test_parse_rdap_domain_without_expiry_is_not_healthy():
    # .ch / .de / .nl answer RDAP but publish no expiration event.
    rec = parse_rdap_domain("switch.ch", rdap_body(expiry=None))
    assert rec["kind"] == "no_expiry"
    assert rec["expiry"] is None
    assert rec["reason"]


def test_parse_rdap_domain_non_object():
    assert parse_rdap_domain("a.com", ["x"])["kind"] == "error"


# ── Snapshot ─────────────────────────────────────────────────────────────────


def test_build_snapshot_lists_unavailable_sorted_and_orders_by_expiry():
    recs = [
        parse_rdap_domain("late.com", rdap_body("2030-01-01T00:00:00Z")),
        parse_rdap_domain("b.ch", rdap_body(expiry=None)),
        dom._record("a.io", dom.KIND_NO_RDAP, "no rdap"),
        parse_rdap_domain("soon.com", rdap_body("2026-12-01T00:00:00Z")),
        dom._record("gone.com", dom.KIND_NOT_FOUND, "nf"),
    ]
    snap = build_snapshot(recs, now=NOW)
    assert snap["unavailable"] == ["a.io", "b.ch"]
    assert [r["name"] for r in snap["domains"][:2]] == ["soon.com", "late.com"]
    assert snap["totals"] == {"domains": 5, "with_expiry": 2, "unavailable": 2, "not_found": 1}
    assert snap["collected_at"] == NOW.isoformat()


# ── Alerts ───────────────────────────────────────────────────────────────────


def snap_for(*specs):
    recs = [parse_rdap_domain(n, rdap_body(e, status=s)) for n, e, s in specs]
    return build_snapshot(recs, now=NOW)


def alerts(data):
    return DomainIntegration({}).parse_alerts(data)


def test_alert_threshold_edges():
    # defaults: warn 30 days, crit 7 days; both inclusive
    cases = {
        "d31.com": "2026-12-13T12:00:01Z",   # 31 days: nothing
        "d30.com": "2026-12-12T12:00:00Z",   # exactly 30: warning
        "d8.com": "2026-11-20T12:00:01Z",    # just over 8: warning
        "d7.com": "2026-11-19T12:00:00Z",    # exactly 7: critical
        "d0.com": "2026-11-12T12:00:00Z",    # now: expired
        "dm1.com": "2026-11-11T12:00:00Z",   # past: expired
    }
    out = {a.entity: a for a in alerts(snap_for(*[(n, e, ()) for n, e in cases.items()]))}
    assert "domain: d31.com" not in out
    assert out["domain: d30.com"].severity == "warning"
    assert out["domain: d8.com"].severity == "warning"
    assert out["domain: d7.com"].severity == "critical"
    assert out["domain: d0.com"].title == "Domain expired" and out["domain: d0.com"].severity == "critical"
    assert out["domain: dm1.com"].title == "Domain expired"


def test_tenant_thresholds_override():
    plugin = DomainIntegration({})
    plugin.threshold_overrides = {"expiry_warn_days": 90, "expiry_crit_days": 60}
    data = snap_for(("a.com", "2027-01-01T00:00:00Z", ()))  # ~49 days
    assert plugin.parse_alerts(data)[0].severity == "critical"


def test_hold_status_is_critical_with_own_entity():
    data = snap_for(("held.com", "2028-01-01T00:00:00Z", ("server hold", "active")))
    out = alerts(data)
    assert [(a.severity, a.entity) for a in out] == [("critical", "domain hold: held.com")]


def test_expiring_and_hold_have_distinct_entities():
    data = snap_for(("x.com", "2026-11-15T00:00:00Z", ("clientHold",)))
    assert {a.entity for a in alerts(data)} == {"domain: x.com", "domain hold: x.com"}


def test_not_found_is_critical():
    data = build_snapshot([dom._record("nope.com", dom.KIND_NOT_FOUND, "nf")], now=NOW)
    out = alerts(data)
    assert len(out) == 1 and out[0].severity == "critical" and out[0].title == "Domain not found"


def test_unavailable_domains_do_not_alert_but_are_listed():
    data = build_snapshot([parse_rdap_domain("switch.ch", rdap_body(expiry=None))], now=NOW)
    assert alerts(data) == []
    assert data["unavailable"] == ["switch.ch"]


def test_invalid_entries_warn():
    data = build_snapshot([], invalid=["foo"], now=NOW)
    out = alerts(data)
    assert out[0].severity == "warning" and out[0].entity == "invalid domain: foo"


def test_alerts_tolerate_missing_and_broken_data():
    p = DomainIntegration({})
    assert p.parse_alerts({}) == []
    assert p.parse_alerts({"domains": [{"name": "a.com", "expiry": "2026-11-13T00:00:00Z"}]}) == []  # no collected_at
    assert p.parse_alerts({"collected_at": NOW.isoformat(), "domains": [None, {}, {"name": "a.com", "expiry": "bad"}]}) == []
    assert p.parse_alerts({"domains": "x", "invalid_domains": None}) == []


# ── collect() through a mocked transport ─────────────────────────────────────


def _patch_client(monkeypatch, handler):
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=kwargs.get("follow_redirects", False))
    monkeypatch.setattr(dom, "safe_async_client", factory)


async def test_collect_mixed_results(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == dom.BOOTSTRAP_URL:
            return httpx.Response(200, json=BOOTSTRAP)
        if url.endswith("/domain/example.com"):
            return httpx.Response(200, json=rdap_body("2027-08-13T04:00:00Z"))
        if url.endswith("/domain/sidn.nl"):
            return httpx.Response(200, json=rdap_body(expiry=None))
        if url.endswith("/domain/gone.com"):
            return httpx.Response(404, json={"errorCode": 404})
        raise AssertionError(url)

    _patch_client(monkeypatch, handler)
    res = await DomainIntegration({"domains": "example.com\nsidn.nl\nswitch.ch\ngone.com\nbad entry!"}).collect()
    assert res.success
    by = {r["name"]: r for r in res.data["domains"]}
    assert by["example.com"]["kind"] == "ok" and by["example.com"]["expiry"]
    assert by["sidn.nl"]["kind"] == "no_expiry" and by["sidn.nl"]["expiry"] is None
    assert by["switch.ch"]["kind"] == "no_rdap" and by["switch.ch"]["reason"]
    assert by["gone.com"]["kind"] == "not_found"
    assert res.data["unavailable"] == ["sidn.nl", "switch.ch"]
    assert res.data["invalid_domains"] == ["bad", "entry!"]


async def test_collect_fails_when_bootstrap_fails(monkeypatch):
    _patch_client(monkeypatch, lambda request: httpx.Response(503))
    res = await DomainIntegration({"domains": "example.com"}).collect()
    assert res.success is False and res.error


async def test_collect_fails_when_every_rdap_query_fails(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == dom.BOOTSTRAP_URL:
            return httpx.Response(200, json=BOOTSTRAP)
        raise httpx.ConnectError("boom")

    _patch_client(monkeypatch, handler)
    res = await DomainIntegration({"domains": "a.com\nb.net"}).collect()
    assert res.success is False
    assert "RDAP" in res.error


async def test_collect_partial_query_failure_is_unavailable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == dom.BOOTSTRAP_URL:
            return httpx.Response(200, json=BOOTSTRAP)
        if url.endswith("/a.com"):
            return httpx.Response(200, json=rdap_body())
        return httpx.Response(429)

    _patch_client(monkeypatch, handler)
    res = await DomainIntegration({"domains": "a.com\nb.com"}).collect()
    assert res.success and res.data["unavailable"] == ["b.com"]
    assert {r["name"]: r["kind"] for r in res.data["domains"]}["b.com"] == "error"


async def test_collect_without_domains_fails():
    res = await DomainIntegration({}).collect()
    assert res.success is False and "No valid domains" in res.error
    res = await DomainIntegration({"domains": "nonsense"}).collect()
    assert res.success is False


async def test_collect_rejects_too_many_domains():
    text = "\n".join(f"d{i}.com" for i in range(dom.MAX_DOMAINS + 1))
    res = await DomainIntegration({"domains": text}).collect()
    assert res.success is False and "Too many" in res.error


async def test_oversized_rdap_answer_stops_reading_and_is_an_error(monkeypatch):
    read = []

    class Huge(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):  # 100 MiB if read to the end
                read.append(1)
                yield b"x" * (1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == dom.BOOTSTRAP_URL:
            return httpx.Response(200, json=BOOTSTRAP)
        if str(request.url).endswith("/a.com"):
            return httpx.Response(200, json=rdap_body())
        return httpx.Response(200, stream=Huge())

    _patch_client(monkeypatch, handler)
    res = await DomainIntegration({"domains": "a.com\nb.com"}).collect()
    assert res.success
    b = {r["name"]: r for r in res.data["domains"]}["b.com"]
    assert b["kind"] == "error" and "too large" in b["reason"]
    assert len(read) <= dom.MAX_BODY_BYTES // (1024 * 1024) + 1

"""Microsoft 365 integration: parsers, alerts and collect() through a mocked transport."""
from datetime import datetime, timezone

import httpx
import pytest

from integrations import m365
from integrations.m365 import (
    M365Integration,
    build_snapshot,
    classify_status,
    parse_applications,
    parse_health_overviews,
    parse_issues,
    parse_skus,
)

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = {"tenant_id": "contoso.onmicrosoft.com", "client_id": "app-id", "client_secret": "s3cret-value"}


def _plugin() -> M365Integration:
    return M365Integration(CONFIG)


# ── Parsers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,expected", [
    ("serviceOperational", "ok"),
    ("ServiceOperational", "ok"),
    ("serviceInterruption", "critical"),
    ("serviceDegradation", "warning"),
    ("investigating", "warning"),
    ("restoringService", "warning"),
    ("extendedRecovery", "warning"),
    ("serviceRestored", "recovered"),
    ("falsePositive", "recovered"),
    ("unknownFutureValue", "warning"),
    ("somethingNew", "warning"),
    (None, "warning"),
    ("", "warning"),
])
def test_status_mapping(status, expected):
    assert classify_status(status) == expected


def test_health_overviews_parsed_and_sorted():
    out = parse_health_overviews([
        {"id": "OrgLiveID", "service": "Identity Service", "status": "ServiceRestored"},
        {"id": "Exchange", "service": "Exchange Online", "status": "serviceOperational"},
        "garbage",
    ])
    assert [s["id"] for s in out] == ["Exchange", "OrgLiveID"]
    assert out[0]["state"] == "ok" and out[1]["state"] == "recovered"


def test_issues_only_open_ones():
    out = parse_issues([
        {"id": "EX1", "title": "Mail delays", "service": "Exchange Online", "status": "serviceDegradation",
         "classification": "incident", "isResolved": False, "startDateTime": "2026-11-12T08:00:00.1234567Z"},
        {"id": "EX2", "title": "Old", "isResolved": True},
        {"id": "EX3", "title": "No flag"},
    ])
    assert [i["id"] for i in out] == ["EX1"]
    assert out[0]["start"] == "2026-11-12T08:00:00.123456+00:00"
    assert out[0]["classification"] == "incident"


def test_skus_percent_and_missing_fields():
    out = parse_skus([
        {"skuPartNumber": "ENTERPRISEPREMIUM", "skuId": "a", "capabilityStatus": "Enabled",
         "consumedUnits": 14, "prepaidUnits": {"enabled": 25, "suspended": 0, "warning": 0, "lockedOut": 0}},
        {"skuPartNumber": "FLOW_FREE", "consumedUnits": 3},
        {"skuPartNumber": "EMPTY", "consumedUnits": 0, "prepaidUnits": {"enabled": 0}},
    ])
    by = {s["sku"]: s for s in out}
    assert by["ENTERPRISEPREMIUM"]["pct"] == 56.0
    assert by["FLOW_FREE"]["pct"] is None and by["FLOW_FREE"]["enabled"] is None
    assert by["EMPTY"]["pct"] is None


def test_applications_flattened_with_days_left():
    out = parse_applications([
        {"id": "o1", "appId": "a1", "displayName": "Backup",
         "passwordCredentials": [{"displayName": "prod", "keyId": "k1", "endDateTime": "2026-12-12T12:00:00Z"}],
         "keyCredentials": [{"keyId": "k2", "endDateTime": "2026-11-01T00:00:00Z"}]},
        {"id": "o2", "appId": "a2", "displayName": "NoCreds", "passwordCredentials": [], "keyCredentials": []},
        {"id": "o3", "displayName": "NoEnd", "passwordCredentials": [{"keyId": "k3"}]},
        {"id": "o4", "displayName": "Bad", "passwordCredentials": None},
    ], NOW)
    by = {c["key_id"]: c for c in out}
    assert by["k1"]["days_left"] == 30 and by["k1"]["kind"] == "secret"
    assert by["k2"]["days_left"] == -12 and by["k2"]["kind"] == "certificate"
    assert by["k3"]["days_left"] is None and by["k3"]["end"] is None
    assert len(out) == 3


# ── Alerts ────────────────────────────────────────────────────────────────────


def _snap(**kw):
    base = {"collected_at": NOW.isoformat(), "services": [], "issues": [], "skus": [], "credentials": []}
    base.update(kw)
    return base


def test_alerts_empty_and_missing_data():
    p = _plugin()
    assert p.parse_alerts({}) == []
    assert p.parse_alerts({"services": None, "skus": None, "credentials": None}) == []
    assert p.parse_alerts(_snap()) == []
    assert p.parse_alerts({"credentials": [{"app": "x", "end": "2020-01-01T00:00:00Z"}]}) == []  # no collected_at


def test_service_alerts():
    services = parse_health_overviews([
        {"id": "Exchange", "service": "Exchange Online", "status": "serviceOperational"},
        {"id": "SPO", "service": "SharePoint", "status": "serviceDegradation"},
        {"id": "Teams", "service": "Teams", "status": "serviceInterruption"},
        {"id": "ID", "service": "Identity", "status": "serviceRestored"},
        {"id": "X", "service": "Unknown", "status": "brandNewStatus"},
        {"id": "NoStatus", "service": "None"},
    ])
    alerts = {a.entity: a.severity for a in _plugin().parse_alerts(_snap(services=services))}
    assert alerts == {"service: SPO": "warning", "service: Teams": "critical",
                      "service: ID": "info", "service: X": "warning"}


def test_issue_alerts():
    issues = parse_issues([
        {"id": "A", "title": "Outage", "service": "Teams", "status": "serviceInterruption",
         "classification": "incident", "isResolved": False},
        {"id": "B", "title": "Slow", "service": "Exchange", "status": "investigating",
         "classification": "incident", "isResolved": False},
        {"id": "C", "title": "Heads-up", "service": "Exchange", "status": "serviceDegradation",
         "classification": "advisory", "isResolved": False},
    ])
    sev = {a.entity: a.severity for a in _plugin().parse_alerts(_snap(issues=issues))}
    assert sev == {"issue: A": "critical", "issue: B": "warning", "issue: C": "info"}


def test_alert_identity_survives_status_and_title_changes():
    def keys(services, issues):
        return {(a.title, a.entity) for a in _plugin().parse_alerts(_snap(services=services, issues=issues))}

    def snap(status, title):
        services = parse_health_overviews([{"id": "SPO", "service": "SharePoint", "status": status}])
        issues = parse_issues([{"id": "SP1", "title": title, "service": "SharePoint", "status": status,
                                "classification": "incident", "isResolved": False}])
        return services, issues

    first = keys(*snap("investigating", "Users can't open files"))
    later = keys(*snap("serviceDegradation", "Some users can't open files in SharePoint"))
    assert first == later and len(first) == 2
    detail = [a.detail for a in _plugin().parse_alerts(_snap(issues=snap("investigating", "Files fail")[1]))]
    assert detail and detail[0].startswith("Files fail")


def test_licence_alerts_and_threshold_edges():
    def sku(name, consumed, enabled, cap="Enabled"):
        return {"sku": name, "capability_status": cap, "consumed": consumed, "enabled": enabled}

    skus = [sku("AT89", 89, 100), sku("AT90", 90, 100), sku("FULL", 100, 100), sku("OVER", 105, 100),
            sku("FREE", 5, 0), sku("NONE", None, None), sku("SUSP", 0, 0, cap="Suspended")]
    got = {a.entity: a.severity for a in _plugin().parse_alerts(_snap(skus=skus))}
    assert got == {"licence: AT90": "warning", "licence: FULL": "warning", "licence: OVER": "critical",
                   "licence-status: SUSP": "warning"}


def test_licence_threshold_override():
    p = _plugin()
    p.threshold_overrides = {"license_warn_pct": 50}
    got = p.parse_alerts(_snap(skus=[{"sku": "S", "capability_status": "Enabled", "consumed": 50, "enabled": 100}]))
    assert [a.severity for a in got] == ["warning"]


def _creds(*specs):
    out = []
    for i, (end, obj) in enumerate(specs):
        out.append({"app": "App", "object_id": obj, "kind": "secret", "key_id": f"k{i}", "name": None,
                    "end": end})
    return out


def test_credential_threshold_edges():
    # exactly 30 d and 7 d remaining, one second beyond, and a few days
    creds = _creds(
        ("2026-12-12T12:00:00Z", "a"),   # 30 d  -> warning
        ("2026-12-12T12:00:01Z", "b"),   # just beyond 30 d -> nothing
        ("2026-11-19T12:00:00Z", "c"),   # 7 d   -> critical
        ("2026-11-19T12:00:01Z", "d"),   # just beyond 7 d -> warning
    )
    got = {a.entity.split()[-1]: a.severity for a in _plugin().parse_alerts(_snap(credentials=creds))}
    assert got == {"k0": "warning", "k2": "critical", "k3": "warning"}


def test_expired_credential_critical_unless_superseded():
    creds = _creds(("2026-11-12T11:59:59Z", "solo"))
    a = _plugin().parse_alerts(_snap(credentials=creds))
    assert [x.severity for x in a] == ["critical"] and "expired" in a[0].title

    creds = _creds(("2026-01-01T00:00:00Z", "rot"), ("2027-06-01T00:00:00Z", "rot"))
    a = _plugin().parse_alerts(_snap(credentials=creds))
    assert [x.severity for x in a] == ["info"]


def test_credential_without_end_is_ignored_and_entity_stable():
    creds = [{"app": "A", "object_id": "o", "kind": "certificate", "key_id": "kk", "end": None}]
    assert _plugin().parse_alerts(_snap(credentials=creds)) == []
    creds = _creds(("2026-11-15T00:00:00Z", "o"))
    e1 = _plugin().parse_alerts(_snap(credentials=creds))[0].entity
    e2 = _plugin().parse_alerts(_snap(credentials=creds, collected_at="2026-11-13T00:00:00+00:00"))[0].entity
    assert e1 == e2


def test_credential_thresholds_configurable():
    p = _plugin()
    p.threshold_overrides = {"cred_warn_days": 90, "cred_crit_days": 60}
    got = p.parse_alerts(_snap(credentials=_creds(("2027-01-01T12:00:00Z", "x"))))  # 50 d
    assert [a.severity for a in got] == ["critical"]


# ── collect() through a mocked transport ──────────────────────────────────────

OVERVIEWS = {"value": [{"id": "Exchange", "service": "Exchange Online", "status": "serviceOperational"}]}
ISSUES = {"value": [{"id": "EX1", "title": "T", "service": "Exchange", "status": "investigating",
                     "classification": "incident", "isResolved": False}]}
SKUS = {"value": [{"skuPartNumber": "E5", "skuId": "s", "capabilityStatus": "Enabled", "consumedUnits": 5,
                   "prepaidUnits": {"enabled": 10}}]}


def _install(monkeypatch, routes, token_status=200, token_body=None):
    """routes maps a Graph path suffix to a callable(request) -> Response or a (status, body) tuple."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "login.microsoftonline.com":
            assert request.url.path == "/contoso.onmicrosoft.com/oauth2/v2.0/token"
            body = token_body if token_body is not None else {"access_token": "tok"}
            return httpx.Response(token_status, json=body)
        assert request.headers["authorization"] == "Bearer tok"
        for suffix, spec in routes.items():
            if request.url.path.endswith(suffix):
                if callable(spec):
                    return spec(request)
                status, body = spec
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})

    monkeypatch.setattr(m365, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


APPS = {"value": [{"id": "o1", "appId": "a1", "displayName": "Backup",
                   "passwordCredentials": [{"keyId": "k1", "endDateTime": "2099-12-01T00:00:00Z"}],
                   "keyCredentials": []}]}


async def test_collect_success(monkeypatch):
    _install(monkeypatch, {
        "/healthOverviews": (200, OVERVIEWS), "/issues": (200, ISSUES),
        "/subscribedSkus": (200, SKUS), "/applications": (200, APPS)})
    res = await _plugin().collect()
    assert res.success, res.error
    d = res.data
    assert d["unavailable"] == []
    assert d["services"][0]["state"] == "ok"
    assert d["issues"][0]["id"] == "EX1"
    assert d["skus"][0]["pct"] == 50.0
    assert d["credentials"][0]["key_id"] == "k1"
    assert d["totals"]["apps_total"] == 1
    assert datetime.fromisoformat(d["collected_at"]).tzinfo is not None


async def test_token_failure_fails_without_echoing_secret(monkeypatch):
    _install(monkeypatch, {}, token_status=401, token_body={
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided. Trace ID: x"})
    res = await _plugin().collect()
    assert not res.success
    assert "AADSTS7000215" in res.error and "invalid_client" in res.error
    assert "s3cret-value" not in res.error


async def test_token_without_access_token_fails(monkeypatch):
    _install(monkeypatch, {}, token_body={"token_type": "Bearer"})
    res = await _plugin().collect()
    assert not res.success


async def test_missing_config_fails_cleanly():
    res = await M365Integration({}).collect()
    assert not res.success and res.error


async def test_one_permission_missing_goes_to_unavailable(monkeypatch):
    _install(monkeypatch, {
        "/healthOverviews": (403, {"error": {"code": "Authorization_RequestDenied"}}),
        "/subscribedSkus": (200, SKUS), "/applications": (200, APPS)})
    res = await _plugin().collect()
    assert res.success
    assert res.data["services"] is None and res.data["issues"] is None
    assert res.data["totals"]["services_total"] is None
    assert res.data["unavailable"] == ["service health (missing permission ServiceHealth.Read.All)"]
    assert res.data["skus"] and res.data["credentials"]
    # Missing data never turns into alerts.
    assert _plugin().parse_alerts(res.data) == []


async def test_issues_failure_alone_is_named(monkeypatch):
    _install(monkeypatch, {
        "/healthOverviews": (200, OVERVIEWS), "/issues": (500, {}),
        "/subscribedSkus": (200, SKUS), "/applications": (200, APPS)})
    res = await _plugin().collect()
    assert res.success
    assert res.data["issues"] is None and res.data["services"] is not None
    assert res.data["unavailable"] == ["service issues (HTTP 500)"]


async def test_all_parts_failing_fails(monkeypatch):
    _install(monkeypatch, {
        "/healthOverviews": (403, {}), "/subscribedSkus": (403, {}), "/applications": (403, {})})
    res = await _plugin().collect()
    assert not res.success
    assert "ServiceHealth.Read.All" in res.error and "Application.Read.All" in res.error


async def test_applications_follow_next_link_with_cap(monkeypatch):
    calls = {"n": 0}

    def apps(request):
        calls["n"] += 1
        return httpx.Response(200, json={
            "value": [{"id": f"o{calls['n']}", "displayName": f"A{calls['n']}",
                       "passwordCredentials": [{"keyId": f"k{calls['n']}", "endDateTime": "2027-01-01T00:00:00Z"}]}],
            "@odata.nextLink": f"{m365.GRAPH_BASE}/applications?$skiptoken=p{calls['n']}"})

    _install(monkeypatch, {"/healthOverviews": (200, OVERVIEWS), "/issues": (200, ISSUES),
                           "/subscribedSkus": (200, SKUS), "/applications": apps})
    res = await _plugin().collect()
    assert res.success
    assert calls["n"] == m365.MAX_PAGES_APPS
    assert len(res.data["credentials"]) == m365.MAX_PAGES_APPS
    assert any("truncated" in u for u in res.data["unavailable"])


async def test_foreign_next_link_is_not_followed(monkeypatch):
    def apps(request):
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://evil.example/steal"})

    seen = _install(monkeypatch, {"/healthOverviews": (200, OVERVIEWS), "/issues": (200, ISSUES),
                                  "/subscribedSkus": (200, SKUS), "/applications": apps})
    res = await _plugin().collect()
    assert res.success
    assert all(r.url.host != "evil.example" for r in seen)
    assert res.data["credentials"] is None
    assert any(u.startswith("app credentials") for u in res.data["unavailable"])


def test_build_snapshot_totals():
    snap = build_snapshot(NOW, None, None, [], [], 0, ["x"])
    assert snap["totals"]["services_total"] is None
    assert snap["totals"]["skus_total"] == 0
    assert snap["collected_at"] == NOW.isoformat()


def test_metadata():
    p = M365Integration
    assert p.name == "m365" and p.network_scope == "cloud" and p.default_interval_seconds >= 300
    secret = next(f for f in p.config_fields if f.key == "client_secret")
    assert secret.encrypted and secret.field_type == "password"
    text = " ".join(f.placeholder for f in p.config_fields) + p.description
    for perm in ("ServiceHealth.Read.All", "Application.Read.All", "LicenseAssignment.Read.All"):
        assert perm in text

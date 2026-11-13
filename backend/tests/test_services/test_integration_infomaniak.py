"""Infomaniak integration: parsers, expiry alerts and collect() over a mocked transport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from integrations import infomaniak
from integrations.infomaniak import InfomaniakIntegration, parse_product, product_label, to_utc

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


def ts(days: float) -> int:
    return int((NOW + timedelta(days=days)).timestamp())


def product(pid=1, days=200, **extra):
    raw = {
        "id": pid, "account_id": 10, "service_id": 2, "service_name": "domain",
        "customer_name": f"example{pid}.ch", "internal_name": "ex", "created_at": ts(-400),
        "expired_at": ts(days) if days is not None else None,
        "is_locked": False, "has_maintenance": False, "is_trial": False, "tags": [],
    }
    raw.update(extra)
    return raw


def snapshot(*raws):
    return {"collected_at": NOW.isoformat(), "products": [parse_product(r) for r in raws]}


def plugin(**thresholds):
    p = InfomaniakIntegration({"api_token": "t"})
    p.threshold_overrides = dict(thresholds)
    return p


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_parse_product_converts_timestamps_to_utc_iso():
    p = parse_product(product(days=10))
    assert p["expires_at"] == (NOW + timedelta(days=10)).isoformat()
    assert p["expires_at"].endswith("+00:00")
    assert p["locked"] is False and p["maintenance"] is False and p["trial"] is False


def test_parse_product_missing_fields():
    p = parse_product({"id": 5})
    assert p["expires_at"] is None and p["created_at"] is None
    assert p["locked"] is None and p["maintenance"] is None and p["trial"] is None
    assert p["customer_name"] == "" and p["tags"] == []


def test_parse_product_alternative_maintenance_key():
    assert parse_product({"in_maintenance": True})["maintenance"] is True


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("garbage", None), (True, None),
    (1_700_000_000, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
    ("1700000000", datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
    (1_700_000_000_000, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
    ("2026-01-01T00:00:00Z", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ("2026-01-01T02:00:00+02:00", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ("2026-01-01T00:00:00", datetime(2026, 1, 1, tzinfo=timezone.utc)),
])
def test_to_utc(value, expected):
    assert to_utc(value) == expected


def test_product_label_never_empty():
    assert product_label({}) == "product"
    assert product_label({"id": 9}) == "product 9"
    assert product_label({"customer_name": "a.ch", "service_name": "domain"}) == "a.ch (domain)"


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_far_expiry_has_no_alert():
    assert plugin().parse_alerts(snapshot(product(days=200))) == []


def test_no_expiry_date_has_no_alert():
    assert plugin().parse_alerts(snapshot(product(days=None))) == []


@pytest.mark.parametrize("days,severity", [
    (31, None), (30.5, None), (30, "warning"), (8, "warning"), (7.5, "warning"),
    (7, "critical"), (1, "critical"), (0.5, "critical"),
])
def test_expiry_threshold_edges(days, severity):
    alerts = plugin().parse_alerts(snapshot(product(days=days)))
    assert [a.severity for a in alerts] == ([severity] if severity else [])


def test_expired_is_critical_and_says_so():
    for days in (0, -1, -400):
        alerts = plugin().parse_alerts(snapshot(product(days=days)))
        assert len(alerts) == 1 and alerts[0].severity == "critical"
        assert "expired" in alerts[0].title


def test_expiry_title_and_entity_are_stable_when_severity_escalates():
    warn = plugin().parse_alerts(snapshot(product(days=20)))[0]
    crit = plugin().parse_alerts(snapshot(product(days=5)))[0]
    assert (warn.title, warn.entity) == (crit.title, crit.entity)
    assert warn.severity == "warning" and crit.severity == "critical"


def test_thresholds_are_tenant_adjustable():
    alerts = plugin(expiry_warn_days=60, expiry_crit_days=45).parse_alerts(snapshot(product(days=50)))
    assert [a.severity for a in alerts] == ["warning"]


def test_expiry_is_relative_to_collected_at_not_the_wall_clock():
    data = snapshot(product(days=20))
    data["collected_at"] = (NOW + timedelta(days=15)).isoformat()  # 5 days left then
    assert [a.severity for a in plugin().parse_alerts(data)] == ["critical"]


def test_no_expiry_alert_without_collected_at():
    data = snapshot(product(days=1))
    del data["collected_at"]
    assert plugin().parse_alerts(data) == []
    data["collected_at"] = "not a date"
    assert plugin().parse_alerts(data) == []


def test_locked_and_maintenance_alerts():
    alerts = plugin().parse_alerts(snapshot(product(is_locked=True, has_maintenance=True)))
    assert sorted(a.entity for a in alerts) == ["locked:1", "maintenance:1"]
    assert all(a.severity == "warning" for a in alerts)


def test_unknown_flags_are_no_alert():
    assert plugin().parse_alerts(snapshot({"id": 1, "customer_name": "x"})) == []


def test_trial_is_marked():
    alerts = plugin().parse_alerts(snapshot(product(days=3, is_trial=True)))
    assert "(trial)" in alerts[0].title


@pytest.mark.parametrize("data", [{}, [], {"products": None}, {"products": [None, 3, {}]},
                                  {"collected_at": "x", "products": [{"expires_at": 5}]}])
def test_parse_alerts_tolerates_garbage(data):
    assert isinstance(plugin().parse_alerts(data), list)


def test_dashboard_summary():
    summary = plugin().get_dashboard_summary(snapshot(product(1, days=40), product(2, days=12)))
    assert summary == {"products": 2, "next_expiry_days": 12}
    assert plugin().get_dashboard_summary(snapshot())["next_expiry_days"] is None
    assert plugin().get_dashboard_summary({}) is None


# ── collect() over a mocked transport ─────────────────────────────────────────


def install(monkeypatch, handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        infomaniak, "safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


def ok(data, **meta):
    return httpx.Response(200, json={"result": "success", "data": data, **meta})


async def test_collect_success(monkeypatch):
    seen = install(monkeypatch, lambda r: ok([product()], page=1, pages=1, total=1))
    result = await InfomaniakIntegration({"api_token": "secret-token"}).collect()
    assert result.success, result.error
    assert result.data["product_count"] == 1 and result.data["unavailable"] == []
    assert result.data["collected_at"].endswith("+00:00")
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(seen[0].url)
    assert "account_id" not in seen[0].url.params


async def test_collect_passes_account_id(monkeypatch):
    seen = install(monkeypatch, lambda r: ok([], pages=1))
    await InfomaniakIntegration({"api_token": "t", "account_id": " 77 "}).collect()
    assert seen[0].url.params["account_id"] == "77"


async def test_collect_follows_pages(monkeypatch):
    def handler(request):
        p = int(request.url.params["page"])
        return ok([product(pid=p)], page=p, pages=3, total=3)

    seen = install(monkeypatch, handler)
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert [p["id"] for p in result.data["products"]] == [1, 2, 3]
    assert len(seen) == 3


async def test_collect_follows_pages_by_total_when_pages_missing(monkeypatch):
    def handler(request):
        p = int(request.url.params["page"])
        return ok([product(pid=p)], total=2)

    seen = install(monkeypatch, handler)
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert [p["id"] for p in result.data["products"]] == [1, 2] and len(seen) == 2


async def test_collect_stops_when_api_ignores_page(monkeypatch):
    seen = install(monkeypatch, lambda r: ok([product(pid=1)]))
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert result.success and result.data["product_count"] == 1
    assert len(seen) == 2  # the repeated page ends the loop


async def test_collect_retries_without_per_page_when_rejected(monkeypatch):
    def handler(request):
        if "per_page" in request.url.params:
            return httpx.Response(422, json={"result": "error", "error": {"description": "bad param"}})
        return ok([product()], pages=1)

    seen = install(monkeypatch, handler)
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert result.success and result.data["product_count"] == 1 and len(seen) == 2


@pytest.mark.parametrize("status,needle", [(401, "rejected"), (403, "rejected"),
                                           (429, "rate limit"), (500, "HTTP 500")])
async def test_http_failure_fails_collection(monkeypatch, status, needle):
    install(monkeypatch, lambda r: httpx.Response(status, json={"result": "error"}))
    result = await InfomaniakIntegration({"api_token": "secret-token"}).collect()
    assert result.success is False and needle in result.error
    assert "secret-token" not in result.error


async def test_rate_limit_is_not_retried(monkeypatch):
    seen = install(monkeypatch, lambda r: httpx.Response(429))
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert result.success is False and len(seen) == 1


async def test_error_result_in_a_200_body_fails(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(
        200, json={"result": "error", "error": {"code": "x", "description": "no access"}}))
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert result.success is False and "no access" in result.error


async def test_malformed_body_fails(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(200, text="<html>"))
    assert (await InfomaniakIntegration({"api_token": "t"}).collect()).success is False
    install(monkeypatch, lambda r: httpx.Response(200, json={"result": "success", "data": {"a": 1}}))
    assert (await InfomaniakIntegration({"api_token": "t"}).collect()).success is False


async def test_network_error_fails(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")

    install(monkeypatch, boom)
    result = await InfomaniakIntegration({"api_token": "t"}).collect()
    assert result.success is False and "ConnectError" in result.error


async def test_missing_token_fails_without_network(monkeypatch):
    seen = install(monkeypatch, lambda r: ok([]))
    assert (await InfomaniakIntegration({}).collect()).success is False
    assert seen == []

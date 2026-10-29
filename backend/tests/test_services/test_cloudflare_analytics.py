"""Cloudflare analytics from the GraphQL API.

The REST endpoint /zones/{id}/analytics/dashboard was retired by Cloudflare;
it answered 404, which used to be swallowed and shown as "0 requests".
"""
import pytest

from integrations.cloudflare import CloudflareIntegration, parse_graphql_analytics


def _body(*sums):
    return {"data": {"viewer": {"zones": [{"httpRequests1hGroups": [{"sum": s} for s in sums]}]}},
            "errors": None}


def test_hourly_groups_are_summed():
    data = parse_graphql_analytics(_body(
        {"requests": 10, "cachedRequests": 2, "bytes": 1000, "cachedBytes": 100, "threats": 1},
        {"requests": 5, "cachedRequests": 1, "bytes": 500, "cachedBytes": None, "threats": 0},
    ))
    assert data == {"requests": 15, "cachedRequests": 3, "bytes": 1500, "cachedBytes": 100, "threats": 1}


def test_a_zone_without_traffic_is_zero_not_missing():
    assert parse_graphql_analytics(_body())["requests"] == 0


def test_no_zone_in_the_answer_is_missing():
    assert parse_graphql_analytics({"data": {"viewer": {"zones": []}}}) is None


def test_graphql_errors_raise():
    """A token without Analytics:Read answers 200 with errors."""
    with pytest.raises(ValueError, match="not authorized"):
        parse_graphql_analytics({"data": None, "errors": [{"message": "not authorized"}]})


def test_blocked_threats_are_not_an_incident():
    zone = {"name": "example.com", "status": "active", "analytics": {"threats": 500}}
    alerts = CloudflareIntegration().parse_alerts({"zones": [zone]})
    assert [a.severity for a in alerts] == ["info"]


def test_a_paused_zone_is_a_warning():
    zone = {"name": "example.com", "status": "paused", "analytics": {"threats": None}}
    alerts = CloudflareIntegration().parse_alerts({"zones": [zone]})
    assert [a.severity for a in alerts] == ["warning"]

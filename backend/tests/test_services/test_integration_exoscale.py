"""Exoscale integration: request signing, parsers, alerts and collect() over a mocked transport."""
import base64
import hashlib
import hmac
import re

import httpx
import pytest

from integrations import exoscale
from integrations.exoscale import (
    ZONES,
    ExoscaleAPI,
    ExoscaleError,
    ExoscaleIntegration,
    authorization_header,
    parse_dbaas_service,
    parse_instance,
    parse_load_balancer,
    parse_sks_cluster,
    signature_message,
)

# ── Signing ───────────────────────────────────────────────────────────────────
#
# Algorithm (Exoscale "API Request Signature"): the message is five segments
# joined by "\n": "<METHOD> <path>", body, query values (sorted by name,
# concatenated, no separator), signed headers (none: an empty line), expiry in
# unix seconds. The signature is base64(HMAC-SHA256(secret, message)).
# The expected values below were derived from that description with an
# independent computation, not by calling the code under test.


def test_message_without_body_or_query():
    message, args = signature_message("get", "/v2/instance", "", {}, 1600000000)
    assert message == "GET /v2/instance\n\n\n\n1600000000"
    assert args == ""


def test_message_sorts_query_by_name_and_joins_values():
    message, args = signature_message("POST", "/v2/instance", '{"a":1}', {"b": "v2", "a": "v1"}, 1600000000)
    assert message == 'POST /v2/instance\n{"a":1}\nv1v2\n\n1600000000'
    assert args == "a;b"


def test_signature_vector_get():
    header = authorization_header("EXOabc", "secret", "GET", "/v2/instance", 1600000000)
    assert header == (
        "EXO2-HMAC-SHA256 credential=EXOabc,expires=1600000000,"
        "signature=zRX4tKvSqOFwHLhXGVtnCWlYpZOJL0xgQRTSLYiegeo="
    )


def test_signature_vector_post_with_query():
    header = authorization_header("EXOabc", "secret", "POST", "/v2/instance", 1600000000,
                                  body='{"a":1}', query={"b": "v2", "a": "v1"})
    assert header == (
        "EXO2-HMAC-SHA256 credential=EXOabc,signed-query-args=a;b,expires=1600000000,"
        "signature=hgZZgnIwgBHJwk6+6nU65HXgCYvzvmDXYPLoUFv5KYs="
    )


def test_signature_changes_with_every_input():
    base = authorization_header("K", "s", "GET", "/v2/instance", 100)
    assert base != authorization_header("K", "s2", "GET", "/v2/instance", 100)
    assert base != authorization_header("K", "s", "GET", "/v2/sks-cluster", 100)
    assert base != authorization_header("K", "s", "GET", "/v2/instance", 101)
    assert base != authorization_header("K", "s", "DELETE", "/v2/instance", 100)


def test_signature_matches_manual_hmac():
    message = "GET /v2/dbaas\n\n\n\n42"
    expected = base64.b64encode(hmac.new(b"sek", message.encode(), hashlib.sha256).digest()).decode()
    assert authorization_header("K", "sek", "GET", "/v2/dbaas", 42).endswith(f"signature={expected}")


# ── Parsers ───────────────────────────────────────────────────────────────────


def lbs(*, svc_status=("success",), state="running", name="nlb-1"):
    return {
        "id": "lb-1", "name": name, "state": state, "ip": "198.51.100.7",
        "services": [{
            "id": "svc-1", "name": "web", "state": "running", "protocol": "tcp", "port": 443,
            "healthcheck-status": [{"public-ip": f"203.0.113.{i}", "status": s}
                                   for i, s in enumerate(svc_status, 1)],
        }],
    }


def test_parse_instance():
    i = parse_instance({"id": "i-1", "name": "web", "state": "running", "public-ip": "203.0.113.2",
                        "instance-type": {"family": "standard", "size": "medium"}})
    assert i["state"] == "running" and i["public_ip"] == "203.0.113.2" and i["type"] == "standard.medium"


def test_parse_instance_missing_fields():
    i = parse_instance({"id": "i-2"})
    assert i["state"] == "unknown" and i["public_ip"] == "" and i["type"] == ""


def test_parse_sks_and_dbaas():
    c = parse_sks_cluster({"id": "c", "name": "k8s", "state": "running", "version": "1.31.4", "nodepools": [{}, {}]})
    assert c["version"] == "1.31.4" and c["nodepool_count"] == 2
    assert parse_sks_cluster({"id": "c"})["nodepool_count"] is None
    d = parse_dbaas_service({"name": "pg", "type": "pg", "state": "rebuilding", "termination-protection": True})
    assert d["state"] == "rebuilding" and d["termination_protection"] is True
    assert parse_dbaas_service({"name": "x"})["termination_protection"] is None


def test_parse_load_balancer_counts_health():
    lb = parse_load_balancer(lbs(svc_status=("success", "failure", "weird")))
    svc = lb["services"][0]
    assert (svc["healthy"], svc["unhealthy"], svc["unknown"]) == (1, 1, 1)
    assert svc["unhealthy_targets"] == ["203.0.113.2"]


def test_parse_load_balancer_without_health_data_is_unknown_not_healthy():
    raw = lbs()
    del raw["services"][0]["healthcheck-status"]
    svc = parse_load_balancer(raw)["services"][0]
    assert (svc["healthy"], svc["unhealthy"], svc["unknown"]) == (0, 0, 0)


# ── Alerts ────────────────────────────────────────────────────────────────────


def snapshot(**parts):
    return {"instances": [], "sks_clusters": [], "dbaas_services": [], "load_balancers": [], **parts}


def plugin():
    return ExoscaleIntegration({"api_key": "k", "api_secret": "s", "zone": "ch-gva-2"})


def test_healthy_estate_has_no_alerts():
    data = snapshot(
        instances=[parse_instance({"id": "i", "name": "web", "state": "running"})],
        sks_clusters=[parse_sks_cluster({"id": "c", "name": "k", "state": "running"})],
        dbaas_services=[parse_dbaas_service({"name": "pg", "state": "running"})],
        load_balancers=[parse_load_balancer(lbs())],
    )
    assert plugin().parse_alerts(data) == []


def test_stopped_instance_warns_on_its_host_and_error_is_critical():
    data = snapshot(instances=[parse_instance({"id": "i1", "name": "a", "state": "stopped"}),
                               parse_instance({"id": "i2", "name": "b", "state": "error"})])
    alerts = plugin().parse_alerts(data)
    assert [(a.severity, a.host, a.entity) for a in alerts] == [
        ("warning", "a", "instance:i1"), ("critical", "b", "instance:i2")]


def test_sks_and_dbaas_not_running():
    data = snapshot(
        sks_clusters=[parse_sks_cluster({"id": "c", "name": "k8s", "state": "updating"})],
        dbaas_services=[parse_dbaas_service({"name": "pg", "state": "poweroff"})],
    )
    alerts = plugin().parse_alerts(data)
    assert {a.entity for a in alerts} == {"sks cluster:c", "dbaas service:pg"}
    assert all(a.severity == "warning" for a in alerts)


def test_missing_state_is_no_alert():
    data = snapshot(instances=[{"id": "i", "name": "x"}, {"id": "j", "name": "y", "state": ""}])
    assert plugin().parse_alerts(data) == []


def test_parsed_item_without_state_is_no_alert():
    # parse_instance stores a missing state as "unknown": that is no data, not an outage.
    data = snapshot(instances=[parse_instance({"id": "i", "name": "x"})],
                    dbaas_services=[parse_dbaas_service({"name": "pg"})])
    assert plugin().parse_alerts(data) == []


def test_state_change_keeps_one_incident():
    # Dedup key is (title, entity): the title must not name the changing state.
    a = plugin().parse_alerts(snapshot(instances=[parse_instance({"id": "i", "name": "a", "state": "stopping"})]))
    b = plugin().parse_alerts(snapshot(instances=[parse_instance({"id": "i", "name": "a", "state": "stopped"})]))
    assert (a[0].title, a[0].entity) == (b[0].title, b[0].entity) == ("Instance a is not running", "instance:i")
    assert "stopped" in b[0].detail


def test_nlb_service_with_unhealthy_target_warns_when_peers_are_healthy():
    alerts = plugin().parse_alerts(snapshot(load_balancers=[parse_load_balancer(lbs(svc_status=("success", "failure")))]))
    assert [a.severity for a in alerts] == ["warning"]
    assert "203.0.113.2" in alerts[0].detail
    assert alerts[0].entity == "nlb-service:lb-1:svc-1"


def test_nlb_service_with_no_healthy_target_is_critical():
    alerts = plugin().parse_alerts(snapshot(load_balancers=[parse_load_balancer(lbs(svc_status=("failure", "failure")))]))
    assert [a.severity for a in alerts] == ["critical"]


def test_nlb_unknown_health_is_no_alert():
    assert plugin().parse_alerts(snapshot(load_balancers=[parse_load_balancer(lbs(svc_status=("???",)))])) == []


@pytest.mark.parametrize("data", [{}, [], {"instances": None}, {"instances": [None, 1, {}]},
                                  {"load_balancers": [{"services": "x"}, {"services": [None, {"unhealthy": True}]}]}])
def test_parse_alerts_tolerates_garbage(data):
    assert isinstance(plugin().parse_alerts(data), list)


def test_dashboard_summary_does_not_count_unavailable_parts():
    data = snapshot(instances=[parse_instance({"id": "i", "state": "running"})])
    data["unavailable"] = ["SKS clusters"]
    s = plugin().get_dashboard_summary(data)
    assert s["instances"] == 1 and s["running"] == 1
    assert s["sks_clusters"] is None and s["dbaas_services"] == 0
    assert plugin().get_dashboard_summary({}) is None


# ── collect() over a mocked transport ─────────────────────────────────────────


def install(monkeypatch, handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        exoscale, "safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


def happy(request):
    path = request.url.path
    if path == "/v2/instance":
        return httpx.Response(200, json={"instances": [{"id": "i", "name": "web", "state": "running"}]})
    if path == "/v2/sks-cluster":
        return httpx.Response(200, json={"sks-clusters": [{"id": "c", "name": "k", "state": "running"}]})
    if path == "/v2/dbaas-service":
        return httpx.Response(200, json={"dbaas-services": []})
    if path == "/v2/load-balancer":
        return httpx.Response(200, json={"load-balancers": [lbs()]})
    return httpx.Response(404)


def cfg(**extra):
    return ExoscaleIntegration({"api_key": "EXOkey", "api_secret": "very-secret", "zone": "ch-dk-2", **extra})


async def test_collect_success_is_signed_and_zone_scoped(monkeypatch):
    seen = install(monkeypatch, happy)
    result = await cfg().collect()
    assert result.success, result.error
    assert result.data["zone"] == "ch-dk-2" and result.data["unavailable"] == []
    assert result.data["collected_at"].endswith("+00:00")
    assert len(result.data["instances"]) == 1 and len(result.data["load_balancers"]) == 1
    assert {r.url.host for r in seen} == {"api-ch-dk-2.exoscale.com"}
    for r in seen:
        auth = r.headers["authorization"]
        m = re.fullmatch(r"EXO2-HMAC-SHA256 credential=EXOkey,expires=(\d+),signature=(.+)", auth)
        assert m, auth
        expected = authorization_header("EXOkey", "very-secret", "GET", r.url.path, int(m.group(1)))
        assert auth == expected
        assert "very-secret" not in str(r.url)


async def test_requests_use_the_documented_list_paths(monkeypatch):
    # Exoscale API v2: list-instances, list-sks-clusters, list-dbaas-services, list-load-balancers.
    seen = install(monkeypatch, happy)
    await cfg().collect()
    assert sorted(r.url.path for r in seen) == [
        "/v2/dbaas-service", "/v2/instance", "/v2/load-balancer", "/v2/sks-cluster"]


async def test_core_failure_fails_collection(monkeypatch):
    for status, needle in [(401, "rejected"), (403, "rejected"), (429, "rate limit"), (500, "HTTP 500")]:
        install(monkeypatch, lambda r, s=status: httpx.Response(s))
        result = await cfg().collect()
        assert result.success is False and needle in result.error
        assert "very-secret" not in result.error


async def test_rate_limit_is_not_retried(monkeypatch):
    seen = install(monkeypatch, lambda r: httpx.Response(429))
    assert (await cfg().collect()).success is False
    assert len(seen) == 1


async def test_malformed_core_response_fails(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(200, json={"nope": []}))
    assert (await cfg().collect()).success is False
    install(monkeypatch, lambda r: httpx.Response(200, text="<html>"))
    assert (await cfg().collect()).success is False


async def test_network_error_fails_collection(monkeypatch):
    def boom(request):
        raise httpx.ReadTimeout("slow")

    install(monkeypatch, boom)
    result = await cfg().collect()
    assert result.success is False and "ReadTimeout" in result.error


async def test_failing_optional_parts_are_named(monkeypatch):
    def handler(request):
        if request.url.path == "/v2/dbaas-service":
            return httpx.Response(403)
        if request.url.path == "/v2/sks-cluster":
            return httpx.Response(429)
        if request.url.path == "/v2/load-balancer":
            return httpx.Response(200, json={"wrong": 1})
        return happy(request)

    install(monkeypatch, handler)
    result = await cfg().collect()
    assert result.success
    assert result.data["unavailable"] == ["DBaaS services", "SKS clusters", "load balancers"]
    assert result.data["instances"][0]["name"] == "web"


async def test_unknown_zone_fails_without_network(monkeypatch):
    seen = install(monkeypatch, happy)
    result = await cfg(zone="evil.example.com/").collect()
    assert result.success is False and "zone" in result.error and seen == []


async def test_missing_credentials_fail_without_network(monkeypatch):
    seen = install(monkeypatch, happy)
    assert (await ExoscaleIntegration({}).collect()).success is False
    assert (await ExoscaleIntegration({"api_key": "k"}).collect()).success is False
    assert seen == []


def test_zone_select_offers_the_documented_zones():
    field = next(f for f in ExoscaleIntegration.config_fields if f.key == "zone")
    assert [o["value"] for o in field.options] == list(ZONES)
    assert {"ch-gva-2", "ch-dk-2"} <= set(ZONES)


def test_api_rejects_unknown_zone():
    with pytest.raises(ExoscaleError):
        ExoscaleAPI("k", "s", "mars-1")

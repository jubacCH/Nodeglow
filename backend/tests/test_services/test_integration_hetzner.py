"""Hetzner Cloud integration: parsers, alerts and collect() over a mocked transport."""
import httpx
import pytest

from integrations import hetzner
from integrations.hetzner import (
    HetznerIntegration,
    parse_floating_ip,
    parse_load_balancer,
    parse_primary_ip,
    parse_server,
    parse_volume,
    traffic_pct,
)

GB = 1000 ** 3
TB = 1000 * GB


def server(sid=1, name="web-1", status="running", outgoing=1 * TB, included=20 * TB, **extra):
    raw = {
        "id": sid, "name": name, "status": status, "created": "2026-01-01T10:00:00+00:00",
        "public_net": {"ipv4": {"ip": "203.0.113.5"}, "ipv6": {"ip": "2001:db8::/64"}},
        "server_type": {"name": "cx22", "cores": 2, "memory": 4.0},
        "datacenter": {"name": "fsn1-dc14", "location": {"name": "fsn1"}},
        "ingoing_traffic": 5 * GB, "outgoing_traffic": outgoing, "included_traffic": included,
        "protection": {"delete": True, "rebuild": False}, "locked": False,
    }
    raw.update(extra)
    return raw


def lb(targets, lid=7, name="lb-1"):
    return {
        "id": lid, "name": name, "load_balancer_type": {"name": "lb11"},
        "location": {"name": "fsn1"}, "public_net": {"ipv4": {"ip": "203.0.113.9"}},
        "services": [{"listen_port": 443}], "targets": targets,
    }


def srv_target(sid, *statuses):
    return {"type": "server", "server": {"id": sid},
            "health_status": [{"listening_port": 443 + i, "status": s} for i, s in enumerate(statuses)]}


def plugin(**thresholds):
    p = HetznerIntegration({"api_token": "t"})
    p.threshold_overrides = dict(thresholds)
    return p


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_parse_server_healthy():
    s = parse_server(server())
    assert s["name"] == "web-1" and s["status"] == "running"
    assert s["ipv4"] == "203.0.113.5" and s["location"] == "fsn1" and s["datacenter"] == "fsn1-dc14"
    assert s["server_type"] == "cx22"
    assert s["traffic_pct"] == 5.0
    assert s["protect_delete"] is True and s["protect_rebuild"] is False and s["locked"] is False


def test_parse_server_missing_fields_stay_missing():
    s = parse_server({"id": 3, "name": "bare"})
    assert s["status"] == "unknown"
    assert s["outgoing_traffic"] is None and s["included_traffic"] is None
    assert s["traffic_pct"] is None
    assert s["locked"] is None and s["protect_delete"] is None
    assert s["ipv4"] == ""


def test_traffic_pct_edges():
    assert traffic_pct(0, 100) == 0.0
    assert traffic_pct(None, 100) is None
    assert traffic_pct(10, None) is None
    assert traffic_pct(10, 0) is None
    assert traffic_pct("10", 100) is None
    assert traffic_pct(True, 100) is None


def test_parse_volume_attached_and_free():
    assert parse_volume({"id": 1, "status": "available", "server": 42, "size": 50})["attached"] is True
    free = parse_volume({"id": 2, "status": "available", "server": None})
    assert free["attached"] is False and free["server_id"] is None
    assert parse_volume({"id": 3})["attached"] is None


def test_parse_load_balancer_target_health():
    parsed = parse_load_balancer(lb([
        srv_target(1, "healthy"),
        srv_target(2, "healthy", "unhealthy"),
        srv_target(3, "unknown"),
        {"type": "server", "server": {"id": 4}, "health_status": []},
    ]))
    health = [t["healthy"] for t in parsed["targets"]]
    assert health == [True, False, None, None]
    assert parsed["unhealthy_targets"] == 1 and parsed["healthy_targets"] == 1
    assert parsed["targets"][1]["unhealthy_ports"] == [444]


def test_parse_load_balancer_label_selector_expands_targets():
    parsed = parse_load_balancer(lb([{
        "type": "label_selector", "label_selector": {"selector": "env=prod"},
        "targets": [srv_target(1, "healthy"), srv_target(2, "unhealthy")],
    }]))
    assert [t["label"] for t in parsed["targets"]] == ["server 1", "server 2"]
    assert parsed["unhealthy_targets"] == 1


def test_parse_ips_assignment():
    assert parse_floating_ip({"id": 1, "ip": "1.2.3.4", "server": None})["assigned"] is False
    assert parse_floating_ip({"id": 1, "ip": "1.2.3.4", "server": 9})["assigned"] is True
    assert parse_floating_ip({"id": 1})["assigned"] is None
    assert parse_primary_ip({"id": 1, "assignee_id": None})["assigned"] is False
    assert parse_primary_ip({"id": 1, "assignee_id": 5})["assigned"] is True
    assert parse_primary_ip({"id": 1})["assigned"] is None


# ── Alerts ────────────────────────────────────────────────────────────────────


def snapshot(servers=(), lbs=()):
    return {"servers": [parse_server(s) for s in servers],
            "load_balancers": [parse_load_balancer(x) for x in lbs]}


def test_running_server_has_no_alert():
    assert plugin().parse_alerts(snapshot([server()])) == []


def test_off_server_is_a_warning_on_its_host():
    alerts = plugin().parse_alerts(snapshot([server(status="off")]))
    assert len(alerts) == 1
    assert alerts[0].severity == "warning" and alerts[0].host == "web-1"
    assert alerts[0].entity == "server:1"


@pytest.mark.parametrize("status", ["starting", "migrating", "rebuilding", "weird"])
def test_other_states_are_warnings(status):
    alerts = plugin().parse_alerts(snapshot([server(status=status)]))
    assert [a.severity for a in alerts] == ["warning"]
    assert status in alerts[0].detail


def test_server_state_change_keeps_one_incident():
    # Dedup key is (title, entity): the title must not name the changing state.
    a = plugin().parse_alerts(snapshot([server(status="stopping")]))
    b = plugin().parse_alerts(snapshot([server(status="off")]))
    assert (a[0].title, a[0].entity) == (b[0].title, b[0].entity) == ("Server web-1 is not running", "server:1")


def test_missing_or_unknown_status_is_no_alert():
    raw = server()
    raw.pop("status")
    assert plugin().parse_alerts(snapshot([raw])) == []
    assert plugin().parse_alerts(snapshot([server(status="unknown")])) == []


def test_alert_titles_are_stable_across_values():
    a = plugin().parse_alerts(snapshot([server(outgoing=17 * TB)]))
    b = plugin().parse_alerts(snapshot([server(outgoing=18 * TB)]))
    assert a[0].title == b[0].title and a[0].entity == b[0].entity


def test_traffic_threshold_edges():
    p = plugin()
    below = p.parse_alerts(snapshot([server(outgoing=15.9 * TB)]))
    at_warn = p.parse_alerts(snapshot([server(outgoing=16 * TB)]))
    below_crit = p.parse_alerts(snapshot([server(outgoing=18.9 * TB)]))
    at_crit = p.parse_alerts(snapshot([server(outgoing=19 * TB)]))
    assert below == []
    assert [a.severity for a in at_warn] == ["warning"]
    assert [a.severity for a in below_crit] == ["warning"]
    assert [a.severity for a in at_crit] == ["critical"]


def test_traffic_thresholds_are_tenant_adjustable():
    p = plugin(traffic_warn_pct=10, traffic_crit_pct=20)
    alerts = p.parse_alerts(snapshot([server(outgoing=3 * TB)]))  # 15 %
    assert [a.severity for a in alerts] == ["warning"]


def test_no_traffic_alert_without_quota_or_counter():
    assert plugin().parse_alerts(snapshot([server(included=None)])) == []
    assert plugin().parse_alerts(snapshot([server(outgoing=None)])) == []
    assert plugin().parse_alerts(snapshot([server(included=0)])) == []


def test_unhealthy_lb_target_with_healthy_peer_is_warning():
    alerts = plugin().parse_alerts(snapshot(lbs=[lb([srv_target(1, "healthy"), srv_target(2, "unhealthy")])]))
    assert [a.severity for a in alerts] == ["warning"]
    assert alerts[0].entity == "lb:7:server 2"


def test_lb_without_any_healthy_target_is_critical():
    alerts = plugin().parse_alerts(snapshot(lbs=[lb([srv_target(1, "unhealthy")])]))
    assert [a.severity for a in alerts] == ["critical"]


def test_unknown_lb_health_is_no_alert():
    assert plugin().parse_alerts(snapshot(lbs=[lb([srv_target(1, "unknown")])])) == []


@pytest.mark.parametrize("data", [{}, {"servers": None}, {"servers": [None, 5, {}]},
                                  {"load_balancers": [{"targets": "x"}, 3]}, []])
def test_parse_alerts_tolerates_garbage(data):
    assert isinstance(plugin().parse_alerts(data), list)


def test_dashboard_summary_does_not_count_unavailable_parts():
    data = snapshot([server(), server(sid=2, name="b", status="off")])
    data["unavailable"] = ["load balancers"]
    summary = plugin().get_dashboard_summary(data)
    assert summary["servers"] == 2 and summary["running"] == 1
    assert summary["load_balancers"] is None and summary["volumes"] == 0
    assert plugin().get_dashboard_summary({}) is None


# ── collect() over a mocked transport ─────────────────────────────────────────


def install(monkeypatch, handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        hetzner, "safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


def page(key, items, page_no=1, next_page=None):
    return httpx.Response(200, json={
        key: items,
        "meta": {"pagination": {"page": page_no, "per_page": 50, "next_page": next_page,
                                "previous_page": None, "last_page": next_page or page_no,
                                "total_entries": len(items)}},
    })


def happy(request):
    path = request.url.path
    if path.endswith("/servers"):
        return page("servers", [server()])
    if path.endswith("/volumes"):
        return page("volumes", [{"id": 1, "name": "v", "status": "available", "server": None}])
    if path.endswith("/load_balancers"):
        return page("load_balancers", [lb([srv_target(1, "healthy")])])
    if path.endswith("/floating_ips"):
        return page("floating_ips", [])
    if path.endswith("/primary_ips"):
        return page("primary_ips", [])
    return httpx.Response(404)


async def test_collect_success(monkeypatch):
    seen = install(monkeypatch, happy)
    result = await HetznerIntegration({"api_token": "secret-token"}).collect()
    assert result.success, result.error
    data = result.data
    assert data["server_count"] == 1 and data["unavailable"] == []
    assert data["volumes"][0]["attached"] is False
    assert data["collected_at"].endswith("+00:00")
    assert all(r.headers["authorization"] == "Bearer secret-token" for r in seen)
    assert all("secret-token" not in str(r.url) for r in seen)


async def test_collect_follows_pagination(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/servers"):
            p = int(request.url.params["page"])
            if p == 1:
                return page("servers", [server(sid=1, name="a")], 1, next_page=2)
            if p == 2:
                return page("servers", [server(sid=2, name="b")], 2, next_page=3)
            return page("servers", [server(sid=3, name="c")], 3, next_page=None)
        return happy(request)

    seen = install(monkeypatch, handler)
    result = await HetznerIntegration({"api_token": "t"}).collect()
    assert result.success
    assert [s["name"] for s in result.data["servers"]] == ["a", "b", "c"]
    assert [r.url.params["page"] for r in seen if r.url.path.endswith("/servers")] == ["1", "2", "3"]


@pytest.mark.parametrize("status,needle", [(401, "rejected"), (403, "rejected"),
                                           (429, "rate limit"), (500, "HTTP 500")])
async def test_core_failure_fails_collection(monkeypatch, status, needle):
    install(monkeypatch, lambda r: httpx.Response(status, headers={"RateLimit-Reset": "1790000000"}))
    result = await HetznerIntegration({"api_token": "secret-token"}).collect()
    assert result.success is False
    assert needle in result.error
    assert "secret-token" not in result.error


async def test_rate_limit_is_not_retried(monkeypatch):
    seen = install(monkeypatch, lambda r: httpx.Response(429))
    result = await HetznerIntegration({"api_token": "t"}).collect()
    assert result.success is False and len(seen) == 1


async def test_network_error_fails_collection(monkeypatch):
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    install(monkeypatch, boom)
    result = await HetznerIntegration({"api_token": "t"}).collect()
    assert result.success is False and "ConnectTimeout" in result.error


async def test_malformed_core_response_fails(monkeypatch):
    install(monkeypatch, lambda r: httpx.Response(200, json={"unexpected": True}))
    result = await HetznerIntegration({"api_token": "t"}).collect()
    assert result.success is False


async def test_failing_optional_parts_are_named(monkeypatch):
    def handler(request):
        path = request.url.path
        if path.endswith("/volumes"):
            return httpx.Response(403)
        if path.endswith("/load_balancers"):
            return httpx.Response(429)
        return happy(request)

    install(monkeypatch, handler)
    result = await HetznerIntegration({"api_token": "t"}).collect()
    assert result.success
    assert result.data["unavailable"] == ["load balancers", "volumes"]
    assert result.data["volumes"] == [] and result.data["server_count"] == 1


async def test_missing_token_fails_without_network(monkeypatch):
    seen = install(monkeypatch, happy)
    result = await HetznerIntegration({}).collect()
    assert result.success is False and seen == []

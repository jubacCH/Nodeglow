"""Kubernetes integration: parsers, alerts and collect() through a mocked transport.

The API server is replaced by ``httpx.MockTransport``; nothing leaves the process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from integrations import kubernetes as k8s
from integrations._base import Alert
from integrations.kubernetes import (
    KubernetesIntegration,
    build_snapshot,
    classify_pod,
    parse_node,
    parse_quantity,
    parse_workload,
)

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def ts(minutes_ago: float = 0) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Payload builders (shapes taken from kubectl get -o json) ─────────────────


def node(name="node-1", ready="True", pressure=(), roles=("worker",), **extra):
    conds = [{"type": "Ready", "status": ready, "reason": "KubeletReady" if ready == "True" else "NodeStatusUnknown"}]
    for p in ("MemoryPressure", "DiskPressure", "PIDPressure"):
        conds.append({"type": p, "status": "True" if p in pressure else "False"})
    return {
        "metadata": {"name": name, "creationTimestamp": ts(100000),
                     "labels": {f"node-role.kubernetes.io/{r}": "" for r in roles}},
        "spec": {},
        "status": {
            "conditions": conds,
            "capacity": {"cpu": "4", "memory": "16314216Ki", "pods": "110"},
            "nodeInfo": {"kubeletVersion": "v1.31.2", "osImage": "Ubuntu 24.04 LTS"},
            **extra,
        },
    }


def cstatus(name="app", restarts=0, waiting=None, terminated=None, last_finished=None, running=False):
    state: dict = {}
    if waiting:
        state["waiting"] = {"reason": waiting}
    elif terminated:
        state["terminated"] = {"reason": terminated, "exitCode": 1}
    else:
        state["running"] = {"startedAt": ts(30)}
    cs = {"name": name, "restartCount": restarts, "state": state, "ready": not waiting and not terminated}
    if last_finished:
        cs["lastState"] = {"terminated": {"reason": "Error", "finishedAt": last_finished}}
    return cs


def pod(name="web-1", ns="default", phase="Running", node_name="node-1", created_min=120,
        statuses=None, init=None, conditions=None, reason=None):
    status: dict = {"phase": phase, "containerStatuses": statuses if statuses is not None else [cstatus()]}
    if init:
        status["initContainerStatuses"] = init
    if conditions:
        status["conditions"] = conditions
    if reason:
        status["reason"] = reason
    return {
        "metadata": {"name": name, "namespace": ns, "creationTimestamp": ts(created_min)},
        "spec": {"nodeName": node_name} if node_name else {},
        "status": status,
    }


def deployment(name="api", ns="default", replicas=3, ready=3, updated=None, total=None,
               generation=5, observed=5, progressing="True"):
    status = {
        "observedGeneration": observed,
        "replicas": replicas if total is None else total,
        "updatedReplicas": replicas if updated is None else updated,
        "conditions": [{"type": "Progressing", "status": progressing,
                        "reason": "NewReplicaSetAvailable" if progressing == "True" else "ProgressDeadlineExceeded"}],
    }
    if ready:
        status["readyReplicas"] = ready  # the API omits the field at zero
    return {"metadata": {"name": name, "namespace": ns, "generation": generation},
            "spec": {"replicas": replicas}, "status": status}


def statefulset(name="db", ns="data", replicas=3, ready=3, updated=None, generation=2, observed=2):
    status = {"observedGeneration": observed, "replicas": replicas,
              "updatedReplicas": replicas if updated is None else updated}
    if ready:
        status["readyReplicas"] = ready
    return {"metadata": {"name": name, "namespace": ns, "generation": generation},
            "spec": {"replicas": replicas}, "status": status}


def daemonset(name="agent", ns="kube-system", desired=3, ready=3, updated=None, generation=1, observed=1):
    return {"metadata": {"name": name, "namespace": ns, "generation": generation},
            "spec": {}, "status": {
                "observedGeneration": observed, "desiredNumberScheduled": desired,
                "numberReady": ready,
                "updatedNumberScheduled": desired if updated is None else updated}}


def pvc(name="data", ns="default", phase="Bound", created_min=60):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": ts(created_min)},
            "spec": {"storageClassName": "fast"}, "status": {"phase": phase}}


def snapshot(nodes=None, pods=None, deployments=None, statefulsets=None, daemonsets=None, pvcs=None,
             truncated=None, unavailable=()):
    return build_snapshot(
        version={"gitVersion": "v1.31.2", "platform": "linux/amd64"},
        nodes=[] if nodes is None else nodes,
        pods=[] if pods is None else pods,
        workloads={"deployments": [] if deployments is None else deployments,
                   "statefulsets": [] if statefulsets is None else statefulsets,
                   "daemonsets": [] if daemonsets is None else daemonsets},
        pvcs=[] if pvcs is None else pvcs,
        now=NOW, truncated=truncated or {}, unavailable=set(unavailable),
    )


def alerts_for(data: dict, **overrides) -> list[Alert]:
    plugin = KubernetesIntegration(config={})
    plugin.threshold_overrides = overrides
    return plugin.parse_alerts(data)


def titles(alerts: list[Alert]) -> list[str]:
    return sorted(a.title for a in alerts)


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_quantities():
    assert parse_quantity("4") == 4.0
    assert parse_quantity("3920m") == pytest.approx(3.92)
    assert parse_quantity("16314216Ki") == 16314216 * 1024
    assert parse_quantity("2Gi") == 2 * 1024 ** 3
    assert parse_quantity("1e3") is None
    assert parse_quantity(None) is None
    assert parse_quantity("12Xi") is None


def test_healthy_node():
    row = parse_node(node("cp-1", roles=("control-plane",)))
    assert row["ready"] is True and row["state"] == "Ready"
    assert row["roles"] == ["control-plane"]
    assert row["pressure"] == []
    assert row["cpu_cores"] == 4.0 and row["memory_bytes"] == 16314216 * 1024
    assert row["pods_capacity"] == 110 and row["kubelet_version"] == "v1.31.2"


def test_node_not_ready_unknown_and_pressure():
    assert parse_node(node(ready="False"))["state"] == "NotReady"
    # The kubelet stopped reporting: kubectl shows NotReady, so do we.
    unknown = parse_node(node(ready="Unknown"))
    assert unknown["ready"] is False and unknown["state"] == "NotReady"
    assert parse_node(node(pressure=("DiskPressure", "PIDPressure")))["pressure"] == ["DiskPressure", "PIDPressure"]


def test_node_without_conditions_is_unknown_not_ready():
    row = parse_node({"metadata": {"name": "n"}})
    assert row["ready"] is None and row["state"] == "Unknown"
    assert row["cpu_cores"] is None and row["memory_bytes"] is None


def test_healthy_and_completed_pods_are_not_problems():
    assert classify_pod(pod()) is None
    done = pod("job-1", phase="Succeeded", statuses=[cstatus(terminated="Completed")])
    assert classify_pod(done) is None


def test_crashloop_pod():
    row = classify_pod(pod(statuses=[cstatus(restarts=14, waiting="CrashLoopBackOff", last_finished=ts(2))]))
    assert row["issues"] == [{"kind": "crashloop", "detail": "app"}]
    assert row["restarts"] == 14 and row["last_restart"] == ts(2)
    assert row["node"] == "node-1"


def test_image_pull_variants():
    for reason in ("ImagePullBackOff", "ErrImagePull"):
        row = classify_pod(pod(phase="Pending", statuses=[cstatus(waiting=reason)]))
        # The container-level reason wins over the generic "pending".
        assert [i["kind"] for i in row["issues"]] == ["imagepull"]


def test_terminated_error_container():
    row = classify_pod(pod(statuses=[cstatus(terminated="Error")]))
    assert [i["kind"] for i in row["issues"]] == ["error"]


def test_failed_pod_is_one_finding_even_with_error_container():
    row = classify_pod(pod(phase="Failed", reason="Evicted", statuses=[cstatus(terminated="Error")]))
    assert row["issues"] == [{"kind": "failed", "detail": "Evicted"}]


def test_init_container_crashloop_counts():
    row = classify_pod(pod(phase="Pending", statuses=[], init=[cstatus("init", waiting="CrashLoopBackOff", restarts=4)]))
    assert [i["kind"] for i in row["issues"]] == ["crashloop"]


def test_pending_pod_records_why():
    cond = [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}]
    row = classify_pod(pod(phase="Pending", node_name=None, statuses=[], conditions=cond))
    assert row["issues"] == [{"kind": "pending", "detail": "Unschedulable"}]
    assert row["node"] == ""


def test_restart_floor_keeps_running_pod():
    assert classify_pod(pod(statuses=[cstatus(restarts=2)])) is None
    row = classify_pod(pod(statuses=[cstatus(restarts=3)]))
    assert row["issues"] == [] and row["restarts"] == 3


def test_deployment_states():
    ok = parse_workload("Deployment", deployment())
    assert ok["problem"] is False and ok["ready"] == 3

    # Mid-rollout: new replicas still coming up, Progressing is True.
    rolling = parse_workload("Deployment", deployment(ready=2, updated=1, total=4, generation=6, observed=6))
    assert rolling["rolling"] is True and rolling["problem"] is False

    # Controller has not even seen the new spec yet.
    behind = parse_workload("Deployment", deployment(ready=1, generation=6, observed=5))
    assert behind["problem"] is False

    # Past the progress deadline: not progressing, even though a rollout exists.
    stalled = parse_workload("Deployment", deployment(ready=1, updated=1, total=4, progressing="False"))
    assert stalled["problem"] is True and stalled["stalled"] is True
    assert stalled["reason"] == "ProgressDeadlineExceeded"

    # Steady state with a missing replica and no rollout.
    steady = parse_workload("Deployment", deployment(ready=2))
    assert steady["problem"] is True

    # Zero ready: the API omits readyReplicas.
    zero = parse_workload("Deployment", deployment(ready=0))
    assert zero["ready"] == 0 and zero["problem"] is True


def test_scaled_to_zero_deployment_is_fine():
    row = parse_workload("Deployment", deployment(replicas=0, ready=0))
    assert row["desired"] == 0 and row["problem"] is False


def test_workload_without_status_is_not_judged():
    row = parse_workload("Deployment", {"metadata": {"name": "x", "namespace": "n"}, "spec": {"replicas": 2}})
    # No controller status at all: the controller has not reported yet.
    assert row["problem"] is False
    assert parse_workload("DaemonSet", {"metadata": {"name": "d"}})["desired"] is None
    assert parse_workload("DaemonSet", {"metadata": {"name": "d"}})["problem"] is False


def test_statefulset_and_daemonset():
    assert parse_workload("StatefulSet", statefulset())["problem"] is False
    assert parse_workload("StatefulSet", statefulset(ready=2))["problem"] is True
    assert parse_workload("StatefulSet", statefulset(ready=2, updated=1, generation=3, observed=3))["problem"] is False
    assert parse_workload("DaemonSet", daemonset())["problem"] is False
    assert parse_workload("DaemonSet", daemonset(ready=2))["problem"] is True
    assert parse_workload("DaemonSet", daemonset(ready=1, updated=0, generation=2, observed=1))["problem"] is False


# ── Snapshot ──────────────────────────────────────────────────────────────────


def test_snapshot_is_bounded_and_aggregates_per_namespace():
    healthy = [pod(f"p{i}", ns="prod" if i % 2 else "stage") for i in range(300)]
    bad = pod("bad", ns="prod", statuses=[cstatus(waiting="CrashLoopBackOff", restarts=9)])
    snap = snapshot(nodes=[node("a"), node("b", ready="False")], pods=healthy + [bad],
                    deployments=[deployment(), deployment("web", ready=1)], pvcs=[pvc(), pvc("p2", phase="Pending")])

    assert snap["pod_totals"] == {"total": 301, "running": 301, "pending": 0, "succeeded": 0,
                                  "failed": 0, "unknown": 0, "problem": 1}
    assert [p["name"] for p in snap["problem_pods"]] == ["bad"]   # no healthy pod is stored
    assert snap["node_totals"] == {"total": 2, "ready": 1, "not_ready": 1, "unknown": 0, "pressure": 0}
    assert snap["workload_totals"]["deployments"] == {"total": 2, "problem": 1}
    assert snap["workload_totals"]["statefulsets"] == {"total": 0, "problem": 0}
    assert snap["pvc_totals"] == {"total": 2, "pending": 1}
    by_ns = {n["name"]: n for n in snap["namespaces"]}
    assert by_ns["prod"]["pods"] == 151 and by_ns["prod"]["problem_pods"] == 1
    assert by_ns["stage"]["pods"] == 150
    assert by_ns["default"]["workloads_degraded"] == 1 and by_ns["default"]["pvcs_pending"] == 1
    # Namespaces with problems sort first.
    assert snap["namespaces"][0]["name"] == "default"
    assert snap["collected_at"] == "2026-11-12T12:00:00Z"


def test_snapshot_marks_unavailable_resources_as_unknown_not_zero():
    snap = build_snapshot(version={}, nodes=[node()], pods=None,
                          workloads={"deployments": None, "statefulsets": [], "daemonsets": []},
                          pvcs=None, now=NOW, truncated={}, unavailable={"pods (missing RBAC permission: list pods)"})
    assert snap["pod_totals"] is None and snap["pvc_totals"] is None
    assert snap["workload_totals"]["deployments"] is None
    assert snap["workload_totals"]["statefulsets"] == {"total": 0, "problem": 0}
    assert snap["problem_pods"] == [] and snap["problem_pods_total"] == 0
    assert snap["unavailable"] == ["pods (missing RBAC permission: list pods)"]


def test_problem_pod_list_is_capped_and_says_so(monkeypatch):
    monkeypatch.setattr(k8s, "MAX_PROBLEM_PODS", 3)
    pods = [pod(f"c{i}", statuses=[cstatus(waiting="CrashLoopBackOff", restarts=i)]) for i in range(5)]
    pods.append(pod("pend", phase="Pending", statuses=[]))
    snap = snapshot(pods=pods)
    assert len(snap["problem_pods"]) == 3 and snap["problem_pods_total"] == 6
    # Most severe and most-restarted first.
    assert [p["name"] for p in snap["problem_pods"]] == ["c4", "c3", "c2"]


# ── Alerts ────────────────────────────────────────────────────────────────────


def test_empty_and_odd_data_never_raises_or_alerts():
    plugin = KubernetesIntegration(config={})
    for sample in ({}, {"foo": "bar"}, {"nodes": []}, {"nodes": None}, {"problem_pods": "x"},
                   {"nodes": [None, 3, {}], "problem_pods": [{}], "problem_workloads": [{"problem": True}],
                    "pending_pvcs": [{}], "truncated": None, "collected_at": "garbage"}):
        assert isinstance(plugin.parse_alerts(sample), list)
    assert plugin.parse_alerts({}) == []
    assert plugin.parse_alerts(None) == []  # type: ignore[arg-type]


def test_healthy_cluster_has_no_alerts():
    snap = snapshot(nodes=[node()], pods=[pod(), pod("job", phase="Succeeded", statuses=[cstatus(terminated="Completed")])],
                    deployments=[deployment()], statefulsets=[statefulset()], daemonsets=[daemonset()], pvcs=[pvc()])
    assert alerts_for(snap) == []


def test_node_alerts_carry_the_node_as_host():
    snap = snapshot(nodes=[node("n1", ready="False"), node("n2", pressure=("MemoryPressure", "DiskPressure")),
                           node("n3", pressure=("PIDPressure",)), node("ok")])
    got = alerts_for(snap)
    by_title = {}
    for a in got:
        by_title.setdefault(a.title, []).append(a)
    assert [a.host for a in by_title["Node not ready"]] == ["n1"]
    assert by_title["Node not ready"][0].severity == "critical"
    assert [a.host for a in by_title["Node MemoryPressure"]] == ["n2"]
    assert [a.host for a in by_title["Node DiskPressure"]] == ["n2"]
    assert [a.host for a in by_title["Node PIDPressure"]] == ["n3"]
    assert all(a.entity.startswith("node: ") for a in got)


def test_node_with_unknown_state_is_not_alerted():
    snap = snapshot(nodes=[{"metadata": {"name": "ghost"}}])
    assert snap["nodes"][0]["state"] == "Unknown"
    assert alerts_for(snap) == []


def test_pod_state_alerts():
    snap = snapshot(pods=[
        pod("crash", statuses=[cstatus(waiting="CrashLoopBackOff", restarts=1)]),
        pod("pull", ns="prod", phase="Pending", node_name=None, statuses=[cstatus(waiting="ImagePullBackOff")]),
        pod("err", statuses=[cstatus(terminated="Error")]),
        pod("evicted", phase="Failed", reason="Evicted", statuses=[]),
    ])
    got = {a.entity: a for a in alerts_for(snap)}
    assert got["pod: default/crash"].title == "Pod in CrashLoopBackOff"
    assert got["pod: default/crash"].severity == "critical"
    assert got["pod: default/crash"].host == "node-1"
    assert got["pod: prod/pull"].title == "Pod in ImagePullBackOff"
    assert got["pod: default/err"].title == "Pod container in Error state"
    assert got["pod: default/evicted"].title == "Pod failed"
    assert "Evicted" in got["pod: default/evicted"].detail


def test_pending_threshold_edges_use_collected_at():
    def pending(minutes):
        return snapshot(pods=[pod("p", phase="Pending", node_name=None, statuses=[], created_min=minutes)])

    assert alerts_for(pending(9.9)) == []
    [a] = alerts_for(pending(10))
    assert (a.title, a.entity, a.severity) == ("Pod pending too long", "pod: default/p", "warning")
    assert len(alerts_for(pending(45))) == 1
    # A tenant raising the limit silences it, lowering it brings it back.
    assert alerts_for(pending(10), pending_minutes=30) == []
    assert len(alerts_for(pending(2), pending_minutes=1)) == 1


def test_pending_without_usable_timestamps_does_not_alert():
    snap = snapshot(pods=[pod("p", phase="Pending", node_name=None, statuses=[], created_min=999)])
    snap["problem_pods"][0]["created"] = ""
    assert alerts_for(snap) == []
    snap["problem_pods"][0]["created"] = ts(999)
    snap["collected_at"] = "not a date"
    assert alerts_for(snap) == []


def test_restart_threshold_edges():
    def running(restarts):
        return snapshot(pods=[pod("r", statuses=[cstatus(restarts=restarts, last_finished=ts(5))])])

    assert alerts_for(running(9)) == []
    [a] = alerts_for(running(10))
    assert (a.title, a.entity) == ("Container restarts high", "pod: default/r")
    assert alerts_for(running(10), restart_count=11) == []
    assert len(alerts_for(running(3), restart_count=3)) == 1


def test_old_restarts_do_not_alert_forever():
    # restartCount never resets: restarts long ago must let the incident resolve.
    def restarted(minutes_ago):
        return snapshot(pods=[pod("r", statuses=[cstatus(restarts=50, last_finished=ts(minutes_ago))])])

    assert len(alerts_for(restarted(60))) == 1
    assert alerts_for(restarted(61)) == []
    # No last termination time: nothing says the restarts are recent.
    assert alerts_for(snapshot(pods=[pod("r", statuses=[cstatus(restarts=50)])])) == []


def test_crashlooping_pod_raises_two_distinct_incidents():
    snap = snapshot(pods=[pod("c", statuses=[cstatus(waiting="CrashLoopBackOff", restarts=20,
                                                         last_finished=ts(1))])])
    got = alerts_for(snap)
    assert titles(got) == ["Container restarts high", "Pod in CrashLoopBackOff"]
    # Same entity, different titles: the dedup key is (title, entity).
    assert {a.entity for a in got} == {"pod: default/c"}


def test_workload_alerts():
    snap = snapshot(
        deployments=[deployment("ok"), deployment("down", ready=0), deployment("part", ready=2),
                     deployment("rolling", ready=2, updated=1, total=4, generation=7, observed=7),
                     deployment("stuck", ready=1, updated=1, total=4, progressing="False")],
        statefulsets=[statefulset(ready=2)], daemonsets=[daemonset(ready=2)])
    got = {a.entity: a for a in alerts_for(snap)}
    assert set(got) == {"deployment: default/down", "deployment: default/part", "deployment: default/stuck",
                        "statefulset: data/db", "daemonset: kube-system/agent"}
    assert got["deployment: default/down"].severity == "critical"
    assert got["deployment: default/part"].severity == "warning"
    assert got["deployment: default/part"].detail == "default/part: 2/3 ready"
    assert "ProgressDeadlineExceeded" in got["deployment: default/stuck"].detail
    assert got["statefulset: data/db"].title == "StatefulSet replicas not ready"


def test_pending_pvc_alerts_after_the_threshold():
    young = snapshot(pvcs=[pvc("v", phase="Pending", created_min=3)])
    old = snapshot(pvcs=[pvc("v", phase="Pending", created_min=15)])
    assert alerts_for(young) == []
    [a] = alerts_for(old)
    assert (a.title, a.entity) == ("Volume claim pending", "pvc: default/v")
    assert alerts_for(snapshot(pvcs=[pvc("v", phase="Bound", created_min=999)])) == []


def test_truncated_lists_are_reported():
    snap = snapshot(truncated={"pods": True})
    [a] = alerts_for(snap)
    assert (a.severity, a.title, a.entity) == ("info", "Kubernetes data incomplete", "cluster: truncated")
    assert "pods" in a.detail


# ── collect() through a mocked transport ──────────────────────────────────────


class FakeApi:
    """Routes by path; ``pages`` maps a path to a list of (status, body) pages
    served in order via the ``continue`` token."""

    def __init__(self):
        self.routes: dict[str, list[tuple[int, dict | str]]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, path, items=None, status=200, body=None):
        self.routes[path] = [(status, body if body is not None else {"metadata": {}, "items": items or []})]

    def pages(self, path, pages: list[list[dict]], forever=False):
        out = []
        for i, items in enumerate(pages):
            more = i < len(pages) - 1 or forever
            out.append((200, {"metadata": {"continue": f"tok{i + 1}"} if more else {}, "items": items}))
        self.routes[path] = out

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path not in self.routes:
            return httpx.Response(404, json={"message": "not found"})
        pages = self.routes[path]
        cont = request.url.params.get("continue")
        index = int(cont[3:]) if cont else 0
        status, body = pages[min(index, len(pages) - 1)]
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


def healthy_api() -> FakeApi:
    api = FakeApi()
    api.add("/version", body={"gitVersion": "v1.31.2", "platform": "linux/amd64"})
    api.add("/api/v1/nodes", [node()])
    api.add("/api/v1/pods", [pod()])
    api.add("/apis/apps/v1/deployments", [deployment()])
    api.add("/apis/apps/v1/statefulsets", [statefulset()])
    api.add("/apis/apps/v1/daemonsets", [daemonset()])
    api.add("/api/v1/persistentvolumeclaims", [pvc()])
    return api


@pytest.fixture
def cluster(monkeypatch):
    api = healthy_api()
    seen_kwargs: list[dict] = []

    def factory(**kwargs):
        seen_kwargs.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(api.handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(k8s, "safe_async_client", factory)
    api.client_kwargs = seen_kwargs  # type: ignore[attr-defined]
    return api


def plugin(**cfg) -> KubernetesIntegration:
    return KubernetesIntegration(config={"host": "https://k8s.example.com:6443", "token": "s3cr3t-token", **cfg})


async def test_collect_healthy_cluster(cluster):
    result = await plugin().collect()
    assert result.success, result.error
    data = result.data
    assert data["version"]["git_version"] == "v1.31.2"
    assert data["unavailable"] == []
    assert data["truncated"] == {k: False for k in k8s._RESOURCES}
    assert data["node_totals"]["total"] == 1 and data["pod_totals"]["total"] == 1
    assert datetime.fromisoformat(data["collected_at"].replace("Z", "+00:00")).tzinfo is not None
    assert KubernetesIntegration(config={}).parse_alerts(data) == []
    # Only GET, always with the bearer token, never with the token in the URL.
    assert {r.method for r in cluster.requests} == {"GET"}
    assert all(r.headers["authorization"] == "Bearer s3cr3t-token" for r in cluster.requests)
    assert all("s3cr3t" not in str(r.url) for r in cluster.requests)
    assert cluster.client_kwargs[0]["verify"] is True


async def test_verify_ssl_is_passed_through(cluster):
    await plugin(verify_ssl=False).collect()
    assert cluster.client_kwargs[0]["verify"] is False


async def test_failing_version_request_fails_the_collect(cluster):
    cluster.add("/version", status=500, body={"message": "boom"})
    result = await plugin().collect()
    assert result.success is False and "HTTP 500" in result.error and "s3cr3t" not in result.error


async def test_unauthorized_fails_even_when_it_is_a_later_resource(cluster):
    cluster.add("/api/v1/pods", status=401, body={"message": "Unauthorized"})
    result = await plugin().collect()
    assert result.success is False and "401" in result.error and "s3cr3t" not in result.error


async def test_connection_error_fails_the_collect(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused for https://k8s.example.com:6443?token=s3cr3t-token")

    monkeypatch.setattr(k8s, "safe_async_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    result = await plugin().collect()
    assert result.success is False
    assert "ConnectError" in result.error and "s3cr3t" not in result.error


async def test_missing_config_fails_without_a_request(cluster):
    for cfg in ({"host": ""}, {"token": ""}, {"namespace": "Not Valid/../x"}):
        result = await plugin(**cfg).collect()
        assert result.success is False and result.error
    assert cluster.requests == []


async def test_forbidden_resource_goes_to_unavailable_naming_the_permission(cluster):
    cluster.add("/api/v1/pods", status=403, body={"message": "forbidden"})
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == ["pods (missing RBAC permission: list pods)"]
    assert result.data["pod_totals"] is None          # unknown, not zero
    assert result.data["problem_pods"] == []
    assert result.data["node_totals"]["total"] == 1   # the rest is intact


async def test_several_forbidden_resources_are_sorted(cluster):
    cluster.add("/apis/apps/v1/statefulsets", status=403, body={})
    cluster.add("/api/v1/nodes", status=403, body={})
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == [
        "nodes (missing RBAC permission: list nodes)",
        "statefulsets (missing RBAC permission: list statefulsets.apps)",
    ]
    assert result.data["nodes"] is None and result.data["node_totals"] is None
    assert result.data["workload_totals"]["statefulsets"] is None


async def test_server_error_on_one_resource_is_unavailable_not_empty(cluster):
    cluster.add("/api/v1/persistentvolumeclaims", status=503, body={})
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == ["persistent volume claims (HTTP 503)"]
    assert result.data["pvc_totals"] is None


async def test_nothing_readable_is_a_failure_not_an_empty_cluster(cluster):
    for path in list(k8s._RESOURCES.values()):
        cluster.add(path[0], status=403, body={})
    result = await plugin().collect()
    assert result.success is False and "No resource could be read" in result.error


async def test_pagination_follows_continue_tokens(cluster):
    cluster.pages("/api/v1/pods", [[pod("a"), pod("b")], [pod("c", statuses=[cstatus(waiting="CrashLoopBackOff")])]])
    result = await plugin().collect()
    assert result.success
    assert result.data["pod_totals"]["total"] == 3
    assert result.data["truncated"]["pods"] is False
    pod_requests = [r for r in cluster.requests if r.url.path == "/api/v1/pods"]
    assert [r.url.params.get("continue") for r in pod_requests] == [None, "tok1"]
    assert all(r.url.params["limit"] == str(k8s.PAGE_LIMIT) for r in pod_requests)


async def test_pagination_is_capped_and_flagged(cluster, monkeypatch):
    monkeypatch.setattr(k8s, "MAX_PAGES", 3)
    cluster.pages("/api/v1/pods", [[pod("a")], [pod("b")], [pod("c")]], forever=True)
    result = await plugin().collect()
    assert result.success
    assert result.data["pod_totals"]["total"] == 3
    assert result.data["truncated"]["pods"] is True
    assert result.data["truncated"]["nodes"] is False
    assert len([r for r in cluster.requests if r.url.path == "/api/v1/pods"]) == 3
    # The cut is visible to alerting too.
    assert any(a.title == "Kubernetes data incomplete" for a in KubernetesIntegration(config={}).parse_alerts(result.data))


async def test_exactly_the_cap_with_no_continue_is_not_truncated(cluster, monkeypatch):
    monkeypatch.setattr(k8s, "MAX_PAGES", 2)
    cluster.pages("/api/v1/pods", [[pod("a")], [pod("b")]])
    result = await plugin().collect()
    assert result.data["truncated"]["pods"] is False and result.data["pod_totals"]["total"] == 2


async def test_expired_continue_token_makes_the_resource_unavailable(cluster):
    cluster.routes["/api/v1/pods"] = [
        (200, {"metadata": {"continue": "tok1"}, "items": [pod("a")]}),
        (410, {"message": "too old resource version"}),
    ]
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == ["pods (HTTP 410, continue token expired)"]
    assert result.data["pod_totals"] is None


async def test_namespace_filter_scopes_namespaced_resources_only(cluster):
    ns = "prod"
    cluster.add("/api/v1/namespaces/prod/pods", [pod("only", ns="prod")])
    cluster.add("/apis/apps/v1/namespaces/prod/deployments", [])
    cluster.add("/apis/apps/v1/namespaces/prod/statefulsets", [])
    cluster.add("/apis/apps/v1/namespaces/prod/daemonsets", [])
    cluster.add("/api/v1/namespaces/prod/persistentvolumeclaims", [])
    result = await plugin(namespace=ns).collect()
    assert result.success, result.error
    paths = {r.url.path for r in cluster.requests}
    assert "/api/v1/nodes" in paths and "/api/v1/namespaces/prod/pods" in paths
    assert "/api/v1/pods" not in paths and "/apis/apps/v1/deployments" not in paths
    assert result.data["namespace_filter"] == "prod"
    assert result.data["pod_totals"]["total"] == 1


async def test_unexpected_payload_is_unavailable_not_a_crash(cluster):
    cluster.add("/api/v1/pods", body="<html>proxy error</html>")
    result = await plugin().collect()
    assert result.success
    assert result.data["unavailable"] == ["pods (invalid JSON)"]


async def test_health_check_reflects_version_endpoint(cluster):
    assert await plugin().health_check() is True
    cluster.add("/version", status=401, body={})
    assert await plugin().health_check() is False


def test_config_secret_is_encrypted_password_field():
    token = next(f for f in KubernetesIntegration.config_fields if f.key == "token")
    assert token.encrypted and token.field_type == "password"
    assert KubernetesIntegration.name == "kubernetes" and KubernetesIntegration.color == "blue"

"""Kubernetes integration – nodes, problem pods, workloads and volume claims.

Read-only: the integration talks to the API server with a service-account
bearer token and only ever issues GET requests. A minimal ClusterRole for it::

    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: nodeglow-readonly
    rules:
      - apiGroups: [""]
        resources: ["nodes", "pods", "persistentvolumeclaims"]
        verbs: ["get", "list"]
      - apiGroups: ["apps"]
        resources: ["deployments", "statefulsets", "daemonsets"]
        verbs: ["get", "list"]
      - nonResourceURLs: ["/version"]
        verbs: ["get"]

Bind it to the service account with a ClusterRoleBinding (or a RoleBinding per
namespace when a namespace filter is configured; ``nodes`` is cluster-scoped and
then simply reports as unavailable unless the ClusterRole is bound as well).

The snapshot stays bounded however big the cluster is: one row per node, counts
per namespace, and detail only for the pods and workloads that have a problem.
Lists are fetched with ``limit``/``continue`` pagination and a page cap; when
the cap cuts a list short, ``truncated`` says so for that resource instead of
letting the counts pass as complete.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

log = logging.getLogger(__name__)

PAGE_LIMIT = 200
MAX_PAGES = 50                # 10 000 objects per resource
MAX_PROBLEM_PODS = 200
MAX_PROBLEM_WORKLOADS = 200
MAX_PENDING_PVCS = 100
MAX_NAMESPACES = 300
MAX_CONTAINERS_PER_POD = 6
# Pods with at least this many restarts are kept in the snapshot, so a tenant
# threshold (minimum: this value) can always be evaluated against stored rows.
RESTART_STORE_FLOOR = 3
# restartCount only ever grows for the life of a pod. A pod counts as
# "restarting" only while its last restart is this recent, otherwise one bad
# night would keep the incident open until the pod is deleted.
RESTART_RECENT_MINUTES = 60

PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure")
CRASHLOOP_REASONS = frozenset({"CrashLoopBackOff"})
IMAGE_PULL_REASONS = frozenset({"ImagePullBackOff", "ErrImagePull"})

_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")

# resource key -> (path, namespaced, RBAC permission named when access is denied)
_RESOURCES: dict[str, tuple[str, bool, str]] = {
    "nodes": ("/api/v1/nodes", False, "list nodes"),
    "pods": ("/api/v1/pods", True, "list pods"),
    "deployments": ("/apis/apps/v1/deployments", True, "list deployments.apps"),
    "statefulsets": ("/apis/apps/v1/statefulsets", True, "list statefulsets.apps"),
    "daemonsets": ("/apis/apps/v1/daemonsets", True, "list daemonsets.apps"),
    "pvcs": ("/api/v1/persistentvolumeclaims", True, "list persistentvolumeclaims"),
}
_LABELS = {
    "nodes": "nodes", "pods": "pods", "deployments": "deployments",
    "statefulsets": "statefulsets", "daemonsets": "daemonsets", "pvcs": "persistent volume claims",
}


# ── Small helpers ─────────────────────────────────────────────────────────────


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list:
    return value if isinstance(value, list) else []


def _rows(value: Any) -> list[dict]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: Any) -> datetime | None:
    """Timezone-aware UTC datetime from an ISO 8601 string, else None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_SUFFIX = {
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0,
    "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 1024.0, "Mi": 1024.0 ** 2, "Gi": 1024.0 ** 3,
    "Ti": 1024.0 ** 4, "Pi": 1024.0 ** 5, "Ei": 1024.0 ** 6,
}
_QUANTITY_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)$")


def parse_quantity(value: Any) -> float | None:
    """A Kubernetes resource quantity ("3920m", "16314216Ki", "8") as a float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _QUANTITY_RE.match(value.strip())
    if not m or m.group(2) not in _SUFFIX:
        return None
    return float(m.group(1)) * _SUFFIX[m.group(2)]


def _conditions(status: dict) -> dict[str, dict]:
    return {c.get("type"): c for c in _rows(status.get("conditions")) if c.get("type")}


# ── Parsers ───────────────────────────────────────────────────────────────────


def parse_node(item: dict) -> dict:
    meta, status, spec = _d(item.get("metadata")), _d(item.get("status")), _d(item.get("spec"))
    conds = _conditions(status)
    ready_status = _d(conds.get("Ready")).get("status")
    # "Unknown" means the kubelet stopped reporting: kubectl shows NotReady.
    ready: bool | None = None
    if ready_status == "True":
        ready = True
    elif ready_status in ("False", "Unknown"):
        ready = False
    roles = sorted(
        k.split("/", 1)[1] for k in _d(meta.get("labels"))
        if k.startswith("node-role.kubernetes.io/") and k.split("/", 1)[1]
    )
    info, cap = _d(status.get("nodeInfo")), _d(status.get("capacity"))
    cpu = parse_quantity(cap.get("cpu"))
    mem = parse_quantity(cap.get("memory"))
    pods_cap = parse_quantity(cap.get("pods"))
    return {
        "name": str(meta.get("name") or ""),
        "ready": ready,
        "state": {True: "Ready", False: "NotReady"}.get(ready, "Unknown"),
        "ready_reason": _d(conds.get("Ready")).get("reason") or "",
        "pressure": [t for t in PRESSURE_CONDITIONS if _d(conds.get(t)).get("status") == "True"],
        "roles": roles,
        "unschedulable": bool(spec.get("unschedulable")),
        "kubelet_version": info.get("kubeletVersion") or "",
        "os_image": info.get("osImage") or "",
        "cpu_cores": cpu,
        "memory_bytes": int(mem) if mem is not None else None,
        "pods_capacity": int(pods_cap) if pods_cap is not None else None,
        "created": meta.get("creationTimestamp") or "",
    }


def classify_pod(item: dict) -> dict | None:
    """The problem row of a pod, or None when the pod is fine.

    Completed job pods (phase Succeeded) are never a problem. ``issues`` holds
    the state-based findings; age- and count-based ones (pending too long,
    restarts) are decided in parse_alerts against the tenant's thresholds.
    """
    meta, status = _d(item.get("metadata")), _d(item.get("status"))
    spec = _d(item.get("spec"))
    phase = str(status.get("phase") or "")
    if phase == "Succeeded":
        return None

    crash: list[str] = []
    pull: list[str] = []
    errored: list[str] = []
    containers: list[dict] = []
    max_restarts = 0
    last_restart: datetime | None = None
    all_statuses = _rows(status.get("initContainerStatuses")) + _rows(status.get("containerStatuses"))
    for cs in all_statuses:
        cname = str(cs.get("name") or "")
        state = _d(cs.get("state"))
        waiting_reason = str(_d(state.get("waiting")).get("reason") or "")
        terminated = _d(state.get("terminated"))
        restarts = _int(cs.get("restartCount")) or 0
        max_restarts = max(max_restarts, restarts)
        finished = parse_ts(_d(_d(cs.get("lastState")).get("terminated")).get("finishedAt"))
        if finished and (last_restart is None or finished > last_restart):
            last_restart = finished
        flagged = False
        if waiting_reason in CRASHLOOP_REASONS:
            crash.append(cname)
            flagged = True
        elif waiting_reason in IMAGE_PULL_REASONS:
            pull.append(cname)
            flagged = True
        elif terminated and terminated.get("reason") == "Error":
            errored.append(cname)
            flagged = True
        if flagged or restarts >= RESTART_STORE_FLOOR:
            reason = waiting_reason or str(terminated.get("reason") or "")
            containers.append({"name": cname, "restarts": restarts, "reason": reason})

    issues: list[dict] = []
    if phase == "Failed":
        # Evicted, node lost, or a failed one-shot pod; its container errors are
        # part of the same finding.
        issues.append({"kind": "failed", "detail": str(status.get("reason") or status.get("message") or "")[:200]})
    else:
        if crash:
            issues.append({"kind": "crashloop", "detail": ", ".join(crash)})
        if pull:
            issues.append({"kind": "imagepull", "detail": ", ".join(pull)})
        if errored:
            issues.append({"kind": "error", "detail": ", ".join(errored)})
        if phase == "Pending" and not issues:
            detail = ""
            for c in _rows(status.get("conditions")):
                if c.get("type") == "PodScheduled" and c.get("status") == "False":
                    detail = str(c.get("reason") or "")
            issues.append({"kind": "pending", "detail": detail})

    if not issues and max_restarts < RESTART_STORE_FLOOR:
        return None
    return {
        "namespace": str(meta.get("namespace") or ""),
        "name": str(meta.get("name") or ""),
        "node": str(spec.get("nodeName") or ""),
        "phase": phase,
        "created": meta.get("creationTimestamp") or "",
        "issues": issues,
        "restarts": max_restarts,
        "last_restart": iso(last_restart) if last_restart else "",
        "containers": containers[:MAX_CONTAINERS_PER_POD],
    }


_POD_SEVERITY = {"crashloop": 0, "failed": 1, "imagepull": 1, "error": 1, "pending": 2}


def _pod_rank(row: dict) -> tuple:
    kinds = [_POD_SEVERITY.get(i.get("kind"), 3) for i in _rows(row.get("issues"))]
    return (min(kinds) if kinds else 3, -int(row.get("restarts") or 0),
            row.get("namespace") or "", row.get("name") or "")


def parse_workload(kind: str, item: dict) -> dict:
    """Replica state of a Deployment, StatefulSet or DaemonSet.

    ``problem`` is set when fewer replicas are ready than desired and the
    rollout is not making progress (a deployment past its progress deadline, or
    no rollout under way at all). A rollout that is still moving never counts.
    Whether a StatefulSet or DaemonSet rollout is stuck cannot be told from the
    API without a clock on the rollout, so those are judged on steady state.
    """
    meta, spec, status = _d(item.get("metadata")), _d(item.get("spec")), _d(item.get("status"))
    generation, observed = _int(meta.get("generation")), _int(status.get("observedGeneration"))
    behind = generation is not None and (observed is None or observed < generation)

    stalled = False
    reason = ""
    if kind == "DaemonSet":
        desired = _int(status.get("desiredNumberScheduled"))
        ready = (_int(status.get("numberReady")) or 0) if desired is not None else None
        updated = _int(status.get("updatedNumberScheduled")) or 0
        rolling = behind or (desired is not None and updated < desired)
    else:
        raw = spec.get("replicas")
        desired = 1 if raw is None else _int(raw)
        ready = (_int(status.get("readyReplicas")) or 0) if desired is not None else None
        updated = _int(status.get("updatedReplicas")) or 0
        total = _int(status.get("replicas")) or 0
        rolling = behind or (desired is not None and updated < desired) or total > updated
        if kind == "Deployment":
            progressing = _conditions(status).get("Progressing")
            if progressing and progressing.get("status") == "False":
                stalled = True
                reason = str(progressing.get("reason") or "NotProgressing")

    # No status block at all: the controller has not reported yet, nothing to judge.
    problem = (bool(status) and desired is not None and ready is not None and desired > 0
               and ready < desired and (stalled or not rolling))
    return {
        "kind": kind,
        "namespace": str(meta.get("namespace") or ""),
        "name": str(meta.get("name") or ""),
        "desired": desired,
        "ready": ready,
        "updated": updated,
        "rolling": bool(rolling),
        "stalled": stalled,
        "reason": reason,
        "problem": bool(problem),
    }


def parse_pvc(item: dict) -> dict:
    meta, spec, status = _d(item.get("metadata")), _d(item.get("spec")), _d(item.get("status"))
    return {
        "namespace": str(meta.get("namespace") or ""),
        "name": str(meta.get("name") or ""),
        "phase": str(status.get("phase") or ""),
        "storage_class": str(spec.get("storageClassName") or ""),
        "created": meta.get("creationTimestamp") or "",
    }


def _ns_entry(table: dict[str, dict], ns: str) -> dict:
    return table.setdefault(ns or "-", {
        "name": ns or "-", "pods": 0, "running": 0, "pending": 0, "succeeded": 0,
        "failed": 0, "unknown": 0, "problem_pods": 0, "workloads": 0,
        "workloads_degraded": 0, "pvcs": 0, "pvcs_pending": 0,
    })


def build_snapshot(
    *,
    version: dict,
    nodes: list[dict] | None,
    pods: list[dict] | None,
    workloads: dict[str, list[dict] | None],
    pvcs: list[dict] | None,
    now: datetime,
    truncated: dict[str, bool],
    unavailable: set[str],
    namespace: str = "",
) -> dict:
    """Pure: raw API objects (None = could not be listed) -> bounded snapshot."""
    ns_table: dict[str, dict] = {}

    # Nodes
    node_rows = None
    node_totals: dict | None = None
    if nodes is not None:
        node_rows = sorted((parse_node(n) for n in nodes), key=lambda r: r["name"])
        node_totals = {
            "total": len(node_rows),
            "ready": sum(1 for r in node_rows if r["ready"] is True),
            "not_ready": sum(1 for r in node_rows if r["ready"] is False),
            "unknown": sum(1 for r in node_rows if r["ready"] is None),
            "pressure": sum(1 for r in node_rows if r["pressure"]),
        }

    # Pods
    problem_pods: list[dict] = []
    pod_totals: dict | None = None
    if pods is not None:
        pod_totals = {"total": 0, "running": 0, "pending": 0, "succeeded": 0,
                      "failed": 0, "unknown": 0, "problem": 0}
        for item in pods:
            phase = str(_d(item.get("status")).get("phase") or "")
            key = phase.lower() if phase.lower() in ("running", "pending", "succeeded", "failed") else "unknown"
            entry = _ns_entry(ns_table, str(_d(item.get("metadata")).get("namespace") or ""))
            pod_totals["total"] += 1
            pod_totals[key] += 1
            entry["pods"] += 1
            entry[key] += 1
            row = classify_pod(item)
            if row is not None:
                pod_totals["problem"] += 1
                entry["problem_pods"] += 1
                problem_pods.append(row)
        problem_pods.sort(key=_pod_rank)

    # Workloads
    workload_totals: dict[str, dict | None] = {}
    problem_workloads: list[dict] = []
    kinds = {"deployments": "Deployment", "statefulsets": "StatefulSet", "daemonsets": "DaemonSet"}
    for key, kind in kinds.items():
        items = workloads.get(key)
        if items is None:
            workload_totals[key] = None
            continue
        rows = [parse_workload(kind, i) for i in items]
        workload_totals[key] = {
            "total": len(rows),
            "problem": sum(1 for r in rows if r["problem"]),
        }
        for r in rows:
            entry = _ns_entry(ns_table, r["namespace"])
            entry["workloads"] += 1
            if r["problem"]:
                entry["workloads_degraded"] += 1
                problem_workloads.append(r)
    problem_workloads.sort(key=lambda r: (r["namespace"], r["kind"], r["name"]))

    # Volume claims
    pending_pvcs: list[dict] = []
    pvc_totals: dict | None = None
    if pvcs is not None:
        pvc_rows = [parse_pvc(i) for i in pvcs]
        pvc_totals = {"total": len(pvc_rows), "pending": sum(1 for r in pvc_rows if r["phase"] == "Pending")}
        for r in pvc_rows:
            entry = _ns_entry(ns_table, r["namespace"])
            entry["pvcs"] += 1
            if r["phase"] == "Pending":
                entry["pvcs_pending"] += 1
                pending_pvcs.append(r)
        pending_pvcs.sort(key=lambda r: (r["namespace"], r["name"]))

    namespaces = sorted(
        ns_table.values(),
        key=lambda e: (-(e["problem_pods"] + e["workloads_degraded"] + e["pvcs_pending"]), e["name"]),
    )
    return {
        "collected_at": iso(now),
        "version": {
            "git_version": version.get("gitVersion") or "",
            "platform": version.get("platform") or "",
        },
        "namespace_filter": namespace,
        "nodes": node_rows,
        "node_totals": node_totals,
        "pod_totals": pod_totals,
        "workload_totals": workload_totals,
        "pvc_totals": pvc_totals,
        "namespaces": namespaces[:MAX_NAMESPACES],
        "namespaces_total": len(namespaces),
        "problem_pods": problem_pods[:MAX_PROBLEM_PODS],
        "problem_pods_total": len(problem_pods),
        "problem_workloads": problem_workloads[:MAX_PROBLEM_WORKLOADS],
        "problem_workloads_total": len(problem_workloads),
        "pending_pvcs": pending_pvcs[:MAX_PENDING_PVCS],
        "pending_pvcs_total": len(pending_pvcs),
        # Per resource: True when the page cap cut the list short, so the counts
        # above cover only part of the cluster.
        "truncated": {k: bool(truncated.get(k)) for k in _RESOURCES},
        "unavailable": sorted(unavailable),
    }


# ── API Client ────────────────────────────────────────────────────────────────


class KubernetesError(Exception):
    """A request failed in a way worth reporting; the message is safe to show.
    ``short`` is the brief form used in the snapshot's ``unavailable`` list."""

    def __init__(self, message: str, short: str = ""):
        super().__init__(message)
        self.short = short or message


class _Unauthorized(KubernetesError):
    """HTTP 401: the token is not accepted at all, so no partial data is trusted."""


class _Forbidden(KubernetesError):
    """HTTP 403: the service account lacks the permission for one resource."""


class KubernetesAPI:
    def __init__(self, host: str, token: str, verify_ssl: bool = True, namespace: str = ""):
        self.base = (host or "").strip().rstrip("/")
        self.token = (token or "").strip()
        self.verify_ssl = verify_ssl
        self.namespace = (namespace or "").strip()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _path(self, key: str) -> str:
        path, namespaced, _perm = _RESOURCES[key]
        if namespaced and self.namespace:
            # /api/v1/pods -> /api/v1/namespaces/<ns>/pods
            head, _, tail = path.rpartition("/")
            return f"{head}/namespaces/{self.namespace}/{tail}"
        return path

    def validate(self) -> None:
        if not self.base:
            raise KubernetesError("API server URL is not configured")
        if not self.token:
            raise KubernetesError("Service account token is not configured")
        if self.namespace and not _NAMESPACE_RE.match(self.namespace):
            raise KubernetesError(f"Invalid namespace filter: {self.namespace!r}")

    async def _get(self, client, path: str, params: dict | None = None) -> dict:
        try:
            resp = await client.get(f"{self.base}{path}", headers=self._headers(), params=params)
        except Exception as exc:
            # No exception text: it could carry the request URL.
            raise KubernetesError(f"{type(exc).__name__} while requesting {path}",
                                  short=type(exc).__name__) from None
        status = resp.status_code
        if status == 401:
            raise _Unauthorized("Authentication failed (HTTP 401): check the service account token")
        if status == 403:
            raise _Forbidden(f"HTTP 403 on {path}")
        if status == 410:
            raise KubernetesError(f"HTTP 410 on {path}: continue token expired",
                                  short="HTTP 410, continue token expired")
        if status >= 400:
            raise KubernetesError(f"HTTP {status} on {path}", short=f"HTTP {status}")
        try:
            body = resp.json()
        except ValueError:
            raise KubernetesError(f"Invalid JSON from {path}", short="invalid JSON") from None
        if not isinstance(body, dict):
            raise KubernetesError(f"Unexpected response from {path}", short="unexpected response")
        return body

    async def list_all(self, client, key: str) -> tuple[list[dict], bool]:
        """All objects of one resource (limit/continue pagination) and whether
        the page cap cut the list short."""
        path = self._path(key)
        items: list[dict] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"limit": PAGE_LIMIT}
            if token:
                params["continue"] = token
            body = await self._get(client, path, params)
            items.extend(i for i in _l(body.get("items")) if isinstance(i, dict))
            token = _d(body.get("metadata")).get("continue") or None
            if not token:
                return items, False
        return items, True

    async def fetch_all(self) -> dict:
        self.validate()
        async with safe_async_client(verify=self.verify_ssl, timeout=30.0, follow_redirects=False) as client:
            # The core request: unreachable or unauthenticated means no data at all.
            version = await self._get(client, "/version")
            now = datetime.now(timezone.utc)

            lists: dict[str, list[dict] | None] = {}
            truncated: dict[str, bool] = {}
            unavailable: set[str] = set()
            errors: list[str] = []
            for key, (_path, _ns, permission) in _RESOURCES.items():
                try:
                    lists[key], truncated[key] = await self.list_all(client, key)
                except _Forbidden:
                    lists[key] = None
                    unavailable.add(f"{_LABELS[key]} (missing RBAC permission: {permission})")
                    errors.append(f"{_LABELS[key]}: forbidden")
                except _Unauthorized:
                    raise
                except KubernetesError as exc:
                    lists[key] = None
                    unavailable.add(f"{_LABELS[key]} ({exc.short})")
                    errors.append(f"{_LABELS[key]}: {exc}")

        if all(v is None for v in lists.values()):
            raise KubernetesError("No resource could be read: " + "; ".join(errors))

        return build_snapshot(
            version=version,
            nodes=lists["nodes"],
            pods=lists["pods"],
            workloads={k: lists[k] for k in ("deployments", "statefulsets", "daemonsets")},
            pvcs=lists["pvcs"],
            now=now,
            truncated=truncated,
            unavailable=unavailable,
            namespace=self.namespace,
        )

    async def health_check(self) -> bool:
        try:
            self.validate()
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0, follow_redirects=False) as client:
                await self._get(client, "/version")
            return True
        except Exception:
            return False


# ── Integration Plugin ────────────────────────────────────────────────────────


class KubernetesIntegration(BaseIntegration):
    name = "kubernetes"
    display_name = "Kubernetes"
    icon = "kubernetes"
    color = "blue"
    description = "Monitor Kubernetes nodes, failing pods, workloads and volume claims (read-only)."

    config_fields = [
        ConfigField(key="host", label="API server URL", field_type="url",
                    placeholder="https://k8s.example.com:6443"),
        ConfigField(key="token", label="Service account token", field_type="password",
                    encrypted=True, placeholder="Bearer token of a read-only service account"),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox",
                    required=False, default=True),
        ConfigField(key="namespace", label="Namespace filter", required=False,
                    placeholder="Empty = all namespaces"),
    ]

    alert_thresholds = (
        Threshold("pending_minutes", "Pod / volume claim pending for", 10, "min", 1, 1440),
        Threshold("restart_count", "Container restarts", 10, "", RESTART_STORE_FLOOR, 10000),
    )

    def _api(self) -> KubernetesAPI:
        return KubernetesAPI(
            host=self.config.get("host", ""),
            token=self.config.get("token", ""),
            verify_ssl=self.config.get("verify_ssl", True),
            namespace=self.config.get("namespace", ""),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except KubernetesError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:
            log.warning("Kubernetes collect failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"{type(exc).__name__}: unexpected error while collecting")

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        now = parse_ts(data.get("collected_at"))
        pending_limit = float(self.threshold("pending_minutes")) * 60
        restart_limit = self.threshold("restart_count")

        def aged(created: Any) -> float | None:
            """Seconds since creation at collection time; None when unknown."""
            ts = parse_ts(created)
            return (now - ts).total_seconds() if now and ts else None

        for node in _rows(data.get("nodes")):
            name = str(node.get("name") or "")
            if not name:
                continue
            entity = f"node: {name}"
            if node.get("ready") is False:
                why = node.get("ready_reason") or "kubelet not reporting"
                alerts.append(Alert(severity="critical", title="Node not ready",
                                    detail=f"{name}: {why}", entity=entity, host=name))
            for cond in _l(node.get("pressure")):
                alerts.append(Alert(severity="warning", title=f"Node {cond}",
                                    detail=f"{name} reports {cond}", entity=entity, host=name))

        for pod in _rows(data.get("problem_pods")):
            ns, name = str(pod.get("namespace") or ""), str(pod.get("name") or "")
            if not name:
                continue
            entity = f"pod: {ns}/{name}"
            node = str(pod.get("node") or "")
            for issue in _rows(pod.get("issues")):
                kind, detail = issue.get("kind"), str(issue.get("detail") or "")
                if kind == "crashloop":
                    alerts.append(Alert(severity="critical", title="Pod in CrashLoopBackOff",
                                        detail=f"{ns}/{name}: {detail}", entity=entity, host=node))
                elif kind == "imagepull":
                    alerts.append(Alert(severity="warning", title="Pod in ImagePullBackOff",
                                        detail=f"{ns}/{name}: {detail}", entity=entity, host=node))
                elif kind == "error":
                    alerts.append(Alert(severity="warning", title="Pod container in Error state",
                                        detail=f"{ns}/{name}: {detail}", entity=entity, host=node))
                elif kind == "failed":
                    alerts.append(Alert(severity="warning", title="Pod failed",
                                        detail=f"{ns}/{name}: {detail or 'phase Failed'}",
                                        entity=entity, host=node))
                elif kind == "pending":
                    age = aged(pod.get("created"))
                    if age is not None and age >= pending_limit:
                        alerts.append(Alert(
                            severity="warning", title="Pod pending too long",
                            detail=f"{ns}/{name}: pending for {int(age // 60)} min"
                                   + (f" ({detail})" if detail else ""),
                            entity=entity, host=node))
            restarts = _int(pod.get("restarts"))
            since_restart = aged(pod.get("last_restart"))
            if (restarts is not None and restarts >= restart_limit and since_restart is not None
                    and since_restart <= RESTART_RECENT_MINUTES * 60):
                alerts.append(Alert(severity="warning", title="Container restarts high",
                                    detail=f"{ns}/{name}: {restarts} restarts", entity=entity, host=node))

        for wl in _rows(data.get("problem_workloads")):
            if wl.get("problem") is not True:
                continue
            kind, ns, name = str(wl.get("kind") or "Workload"), str(wl.get("namespace") or ""), str(wl.get("name") or "")
            if not name:
                continue
            desired, ready = _int(wl.get("desired")), _int(wl.get("ready"))
            detail = f"{ns}/{name}: {ready if ready is not None else '?'}/{desired if desired is not None else '?'} ready"
            if wl.get("reason"):
                detail += f" ({wl['reason']})"
            alerts.append(Alert(
                severity="critical" if ready == 0 else "warning",
                title=f"{kind} replicas not ready", detail=detail,
                entity=f"{kind.lower()}: {ns}/{name}"))

        for pvc in _rows(data.get("pending_pvcs")):
            ns, name = str(pvc.get("namespace") or ""), str(pvc.get("name") or "")
            age = aged(pvc.get("created"))
            if name and age is not None and age >= pending_limit:
                alerts.append(Alert(severity="warning", title="Volume claim pending",
                                    detail=f"{ns}/{name}: Pending for {int(age // 60)} min",
                                    entity=f"pvc: {ns}/{name}"))

        cut = [k for k, v in _d(data.get("truncated")).items() if v is True]
        if cut:
            alerts.append(Alert(severity="info", title="Kubernetes data incomplete",
                                detail="Page cap reached, counts are partial for: " + ", ".join(sorted(cut)),
                                entity="cluster: truncated"))
        return alerts

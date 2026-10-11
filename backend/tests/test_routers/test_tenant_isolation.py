"""Tenant-isolation suite (multi-tenancy phase 8) — enumerating and gating.

Like ``test_route_auth_coverage.py`` this walks ``app.routes``; a route added
later is covered automatically, and the suite fails for anything it cannot
classify (an unknown path parameter, a table without a seed, a stale
allowlist entry).

Setup: tenant A (id 1, the core's tenant) and tenant B (id 2), one row of
**every tenant-owned table** in each (``SEEDS`` must cover
``services.tenancy.tenant_tables()``), every B row carrying
``CANARY-B-<table>``. Then, as a user of A (real session) and with an API
key of A:

* every route with path parameters, called with B's ids → 404, and after a
  mutating method B's rows are byte-for-byte unchanged;
* every response (lists, details, aggregates, actions) contains no
  ``CANARY-B``;
* with ``X-Nodeglow-Tenant: 2`` (A's user is no member of B) → 404.

Routes outside the tenant model are listed in ``INSTALLATION_ROUTES`` /
``TENANTLESS_ROUTES`` with a reason per entry. ClickHouse (phase 6): a
recording fake client checks that every ClickHouse call made while serving
A is scoped to A
(:func:`ch_call_is_scoped`, which encodes phase 6's contract). Jobs:
``test_tenant_job_isolation.py``.
"""
from __future__ import annotations

import json
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import bcrypt
import pytest
from fastapi.routing import APIWebSocketRoute
from sqlalchemy import select
from starlette.routing import Mount

from models.base import Base
from models.settings import Session, User, _hash_token
from models.tenant import Tenant, TenantMembership
from services import clickhouse_client as _ch
from services.tenancy import system_scope, tenant_scope, tenant_tables
from tests.test_routers.conftest import make_client

A, B = 1, 2
CANARY = "CANARY-B"
PW = "Corr3ct-Horse!"
BASE = {A: 100, B: 900}  # row ids per tenant
TAG = {A: "CANARY-A", B: CANARY}

# The real ClickHouse entry points, captured before any test patches them.
_REAL_CH_QUERY = _ch.query
_REAL_CH_SCALAR = _ch.query_scalar
_REAL_CH_INSERTS = {name: getattr(_ch, name) for name in (
    "insert_batch", "insert_ping_checks", "insert_agent_metrics", "insert_bandwidth_metrics")}


# ── Seeds: one row of every tenant-owned table per tenant ───────────────────

def _later(days=30):
    return datetime.utcnow() + timedelta(days=days)


def _seed_key(tid: int) -> tuple[str, bytes]:
    """(key id, data key) of each tenant's seeded tenant key: the key id
    carries the canary, the secrets below are encrypted under it."""
    import hashlib

    return f"{TAG[tid]}-tenant_keys", hashlib.sha256(f"seed-dek-{tid}".encode()).digest()


def _secret(tid: int, plaintext: str) -> str:
    from services import tenant_keys

    key_id, dek = _seed_key(tid)
    return tenant_keys.seal(tenant_keys.dek_fernet(dek), key_id, plaintext)


def _seeds(tid: int) -> dict[str, list[dict]]:
    """table -> rows (column values) for tenant ``tid``; children after parents."""
    from services import tenant_keys

    b, tag = BASE[tid], TAG[tid]
    net = 10 if tid == A else 20

    def c(table):
        return f"{tag}-{table}"

    key_id, dek = _seed_key(tid)
    return {
        "tenant_keys": [dict(key_id=key_id, kek_id=tenant_keys.KEK_ID,
                             wrapped_dek=tenant_keys.wrap_dek(dek, tid, key_id))],
        "sites": [dict(id=b, name=c("sites"))],
        "agents": [dict(id=b, name=c("agents"), hostname=f"{tag.lower()}-agent", token=f"tok-{tid}",
                        is_probe=True, site_id=b, platform="linux",
                        watched_services=json.dumps([c("agents")]),
                        last_seen=datetime.utcnow())],
        "agent_install_tokens": [dict(id=b, token_hash=f"install-hash-{tid}", prefix=f"ngi_{tid}",
                                      note=c("agent_install_tokens"), expires_at=_later(),
                                      site_id=b)],
        "ping_hosts": [dict(id=b, name=c("ping_hosts"), hostname=f"{net}.0.0.1", probe_id=b,
                            site_id=b, ssl_expiry_days=5, ip_address=f"{net}.0.0.1",
                            check_detail=json.dumps({"icmp": c("check_detail")}),
                            source="manual", source_detail=c("source_detail")),
                       dict(id=b + 1, name=c("ping_hosts") + "-https", hostname=f"https://{net}.0.0.2",
                            check_type="https", port=443, ssl_expiry_days=3, site_id=b,
                            parent_id=b)],
        "discovered_ports": [dict(id=b, host_id=b, port=22, service=c("discovered_ports"),
                                  has_ssl=True, ssl_issuer=c("ssl_issuer"), last_open=True)],
        "incidents": [dict(id=b, rule="host_down", title=c("incidents"), status="open",
                           severity="critical", host_ids=f"[{b}]", host_ids_hash=f"h{tid}",
                           postmortem=c("postmortem"), precursor_template=c("precursor"))],
        "incident_events": [dict(id=b, incident_id=b, event_type="created",
                                 summary=c("incident_events"))],
        "integration_configs": [dict(id=b, type="proxmox", name=c("integration_configs"),
                                     config_json=_secret(tid, json.dumps(
                                         {"host": f"{net}.0.0.9", "token_name": c("config")})),
                                     cluster_group=c("cluster"))],
        "snapshots": [dict(id=b, entity_type="proxmox", entity_id=b, ok=True,
                           timestamp=datetime.utcnow() - timedelta(hours=1),
                           data_json=json.dumps({"cluster_name": c("snapshots"), "nodes": [],
                                                 "cpu": 99})),
                      ],
        "alert_rules": [dict(id=b, name=c("alert_rules"), source_type="proxmox", source_id=b,
                             field_path="cpu", operator="gt", threshold="1",
                             message_template=c("message_template")),
                        dict(id=b + 1, name=c("alert_rules") + "-ping", source_type="ping",
                             source_id=b + 1, field_path="online", operator="eq",
                             threshold="false")],
        "credentials": [dict(id=b, name=c("credentials"), type="snmp_v2c",
                             data_json=_secret(tid, json.dumps({"community": c("community")})))],
        "snmp_host_configs": [dict(id=b, host_id=b, credential_id=b,
                                   oids_json=json.dumps([{"oid": "1.3.6.1", "name": c("oid")}]))],
        "snmp_results": [dict(id=b, host_id=b, data_json=json.dumps({c("snmp_results"): 1}))],
        "log_templates": [dict(id=b, template_hash=_tpl_hash(tid), template=c("log_templates"),
                               example=c("example"), count=5, tags=c("tags"))],
        "precursor_patterns": [dict(id=b, template_id=b, precedes_event=c("precursor_patterns"),
                                    confidence=0.9, occurrence_count=5)],
        "fleet_patterns": [dict(id=b, template_hash=_tpl_hash(tid), host_count=3,
                                source_ips=c("fleet_patterns"))],
        "host_baselines": [dict(id=b, host_key=c("host_baselines"), hour_of_day=1, day_of_week=1)],
        "syslog_views": [dict(id=b, name=c("syslog_views"), filters_json=json.dumps({"q": c("q")}))],
        "subnet_scan_schedules": [dict(id=b, name=c("subnet_scan_schedules"), cidr=f"{net}.9.0.0/30",
                                       site_id=b, enabled=False)],
        "subnet_scan_logs": [dict(id=b, schedule_id=b, cidr=f"{net}.9.0.0/30",
                                  hosts_added=c("subnet_scan_logs"))],
        "maintenance_windows": [dict(id=b, name=c("maintenance_windows"), host_ids=f"[{b}]")],
        "notification_logs": [dict(id=b, channel="telegram", title=c("notification_logs"),
                                   message=c("message"), severity="info", status="sent")],
        "api_keys": [dict(id=b, name=c("api_keys"), key_hash=f"seed-key-{tid}", prefix=f"ng_s{tid}",
                          role="admin", enabled=True, hash_algo="hmac", note=c("note"))],
        "audit_logs": [dict(id=b, action=c("audit_logs"), username=c("username"),
                            target_name=c("target"), timestamp=datetime.utcnow())],
        "ai_usage_logs": [dict(id=b, feature=c("ai_usage_logs"), model=c("model"),
                               input_tokens=10, output_tokens=10)],
        "backup_jobs": [dict(id=b, name=c("backup_jobs"), source_type="proxmox", source_config_id=b,
                             target_name=c("target_name"), last_status="failed",
                             last_error=c("last_error"), last_run_at=datetime.utcnow())],
        "backup_history": [dict(id=b, job_id=b, timestamp=datetime.utcnow(), status="failed",
                                error=c("backup_history"))],
        "tenant_settings": [dict(key="site_name", value=c("tenant_settings")),
                            dict(key="telegram_chat_id", value=c("telegram_chat_id")),
                            dict(key="ntfy_topic", value=c("ntfy_topic")),
                            dict(key="smtp_to", value=c("smtp_to"))],
        "user_preferences": [dict(user_id=b, dashboard_layout=json.dumps(
            [{"i": c("user_preferences"), "x": 0, "y": 0, "w": 4, "h": 4}]))],
    }


def _tpl_hash(tid: int) -> str:
    """A template hash in the format the routes accept (16 hex digits)."""
    return ("a" if tid == A else "b") + f"{tid:015x}"


def _model_for(table_name: str):
    for m in Base.registry.mappers:
        if m.local_table.name == table_name:
            return m.class_
    raise KeyError(table_name)


async def _seed(sf) -> None:
    async with sf() as s:
        with system_scope("test: seed tenants A and B", audit=False):
            s.add(Tenant(id=B, provider_id=1, name=f"{CANARY}-tenants", slug="b", status="active"))
            # A user of each tenant (users are global; memberships say where).
            for tid in (A, B):
                s.add(User(id=BASE[tid], username=f"{TAG[tid].lower()}-user", role="admin",
                           auth_source="local", display_name=f"{TAG[tid]}-users",
                           password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()))
            await s.flush()
            for tid in (A, B):
                s.add(TenantMembership(user_id=BASE[tid], tenant_id=tid, role="admin"))
                for table, rows in _seeds(tid).items():
                    model = _model_for(table)
                    for row in rows:
                        # merge: make_client already stored tenant 1's site_name
                        await s.merge(model(tenant_id=tid, **row))
                    await s.flush()
            await s.commit()


def test_every_tenant_owned_table_is_seeded():
    """A new tenant-owned table must get a seed row here (with its canary),
    otherwise the routes serving it are walked against an empty table."""
    tables = {t.name for t in tenant_tables()}
    seeded = set(_seeds(B))
    assert seeded == tables, (f"tenant-owned tables without a seed: {sorted(tables - seeded)}; "
                              f"seeds for unknown tables: {sorted(seeded - tables)}")


async def _tenant_rows(sf, tid: int) -> dict[str, list]:
    """Every tenant-owned row of ``tid``, all columns — to prove "unchanged" —
    plus the tenant's users (global table: its members' rows), memberships
    and their sessions."""
    out: dict[str, list] = {}
    async with sf() as s:
        with system_scope("test: snapshot a tenant", audit=False):
            for t in sorted(tenant_tables(), key=lambda t: t.name):
                rows = (await s.execute(select(t).where(t.c.tenant_id == tid))).all()
                out[t.name] = sorted((tuple(map(repr, r)) for r in rows))
            members = select(TenantMembership.user_id).where(TenantMembership.tenant_id == tid)
            for name, stmt in (
                ("tenant_memberships", select(TenantMembership.__table__).where(
                    TenantMembership.tenant_id == tid)),
                ("users", select(User.__table__).where(User.id.in_(members))),
                ("sessions", select(Session.__table__).where(Session.user_id.in_(members))),
            ):
                out[name] = sorted(tuple(map(repr, r)) for r in (await s.execute(stmt)).all())
    return out


# Columns of tenant-owned rows that point at a row of another table
# (including the soft references without a foreign key).
REF_TARGETS = {
    "parent_id": "ping_hosts", "probe_id": "agents", "site_id": "sites",
    "credential_id": "credentials", "host_id": "ping_hosts", "host_ids": "ping_hosts",
    "source_config_id": "integration_configs", "template_id": "log_templates",
    "job_id": "backup_jobs", "schedule_id": "subnet_scan_schedules",
    "incident_id": "incidents", "entity_id": "integration_configs",
}


def _ref_target(table: str, column: str, row) -> str | None:
    if table == "alert_rules" and column == "source_id":
        return "ping_hosts" if row["source_type"] == "ping" else "integration_configs"
    return REF_TARGETS.get(column)


async def _foreign_refs(sf, tid: int = A) -> list[str]:
    """Rows of ``tid`` that reference a row of another tenant."""
    bad: list[str] = []
    async with sf() as s:
        with system_scope("test: references of a tenant", audit=False):
            owner: dict[str, dict] = {}
            for t in tenant_tables():
                if "id" in t.c:
                    owner[t.name] = dict((await s.execute(select(t.c.id, t.c.tenant_id))).all())
            for t in sorted(tenant_tables(), key=lambda t: t.name):
                cols = [c.name for c in t.c if c.name in REF_TARGETS or
                        (t.name == "alert_rules" and c.name == "source_id")]
                if not cols:
                    continue
                for row in (await s.execute(select(t).where(t.c.tenant_id == tid))).mappings():
                    for col in cols:
                        target, v = _ref_target(t.name, col, row), row[col]
                        if v is None or target is None:
                            continue
                        ids = {int(x) for x in re.findall(r"\d+", v)} if isinstance(v, str) else {v}
                        foreign = {i for i in ids if owner.get(target, {}).get(i, tid) != tid}
                        if foreign:
                            bad.append(f"{t.name}.{col}={v!r} -> {target} of another tenant "
                                       f"(row {row.get('id')})")
    return bad


# ── Route classification ─────────────────────────────────────────────────────

# Path parameter -> the table whose B id is used (or a literal value).
PARAM_TABLE = {
    "host_id": "ping_hosts",
    "agent_id": "agents",
    "incident_id": "incidents",
    "config_id": "integration_configs",
    "key_id": "api_keys",
    "token_id": "agent_install_tokens",
    "schedule_id": "subnet_scan_schedules",
    "cred_id": "credentials",
    "job_id": "backup_jobs",
    "window_id": "maintenance_windows",
    "rule_id": "alert_rules",
    "port_id": "discovered_ports",
    "user_id": "users",
    "template_hash": "log_templates",
}
# Where a parameter name means another table on one route.
PARAM_OVERRIDES = {
    ("/api/snmp/hosts/{config_id}", "config_id"): "snmp_host_configs",
    ("/api/snmp/hosts/{config_id}/test-oids", "config_id"): "snmp_host_configs",
    ("/api/snmp/hosts/{config_id}/poll", "config_id"): "snmp_host_configs",
}
# Parameters that are not a row of a tenant (same value for A and B).
LITERAL_PARAMS = {
    "integration_type": "proxmox",
    "platform": "linux",
    "mib_id": "999999",  # snmp_mibs is a global table (MIB catalogue)
}

# Installation routes: no tenant data by design, or operator-only views of the
# whole installation. Called in the walk (so they must not crash) but their
# bodies are allowed to contain B's canary — each entry says why.
INSTALLATION_ROUTES = {
    ("POST", "/api/v1/backup"):
        "operator-only full backup of the installation (spec: raw-SQL inventory; per-tenant "
        "export is phase 12)",
    ("GET", "/api/v1/backup/info"):
        "operator-only row counts of the installation backup",
    ("POST", "/api/v1/backup/restore"):
        "operator-only restore of the installation (replaces every tenant's rows)",
    ("POST", "/api/snmp/mibs/upload"): "global MIB catalogue; operator-only with several tenants",
    ("DELETE", "/api/snmp/mibs/{mib_id}"): "global MIB catalogue; operator-only with several tenants",
    ("POST", "/api/snmp/mibs/seed-defaults"):
        "global MIB catalogue; operator-only with several tenants",
    ("POST", "/api/snmp/mibs/library/import"):
        "global MIB catalogue; operator-only with several tenants",
}

# Routes that never resolve a tenant from the session (public, token-authenticated
# or authentication itself) — not walked here; the header check does not apply.
TENANTLESS_ROUTES = {
    ("GET", "/health"): "liveness, no data",
    ("GET", "/livez"): "liveness, no data",
    ("GET", "/readyz"): "readiness, no data",
    ("GET", "/metrics"): "Prometheus counters; tenant label is a numeric id, no tenant data",
    ("POST", "/api/auth/login"): "authentication itself (test_tenant_auth_binding.py)",
    ("POST", "/api/auth/logout"): "authentication itself",
    ("GET", "/api/auth/me"): "resolves the tenant itself (test_tenant_auth_binding.py)",
    ("POST", "/api/auth/tenant"): "tenant switch, membership-checked (test_tenant_auth_binding.py)",
    ("GET", "/setup"): "first-run wizard, refuses after setup",
    ("GET", "/setup/status"): "first-run wizard",
    ("POST", "/setup/complete"): "first-run wizard, refuses after setup",
    ("POST", "/api/agent/enroll"): "install-token auth; tenant from the token (agent tests)",
    ("POST", "/api/agent/report"): "agent-token auth; tenant from the agent (agent tests)",
    ("POST", "/api/agent/logs"): "agent-token auth; tenant from the agent (agent tests)",
    ("GET", "/api/agent/version/{platform}"): "public agent version",
    ("GET", "/api/agent/update-public-key"): "public signing key",
    ("GET", "/install/linux"): "install-token auth",
    ("GET", "/install/windows"): "install-token auth",
    ("GET", "/agents/download/{platform}"): "public agent binaries",
}

# Path keys that are content, not row ids: the same log template (same text,
# same hash) can exist in both tenants, so B's hash is no secret of B's and
# the route need not 404. Their Postgres part is tenant-filtered (walked for
# the canary); their ClickHouse reads are scoped like every other.
CONTENT_KEYED_ROUTES = {
    ("GET", "/syslog/api/root-cause/{template_hash}"): "template_hash is a content hash",
    ("GET", "/syslog/api/reverse-cause/{template_hash}"): "template_hash is a content hash",
}

# Answers that come before any row is looked up (validation, licence):
# accepted for B's id only when identical to the answer for an id that
# exists in no tenant — no existence oracle.
PRE_LOOKUP_STATUSES = {400, 402, 422}

# Long-lived streams: the walk would block on them. Covered by the stream
# isolation tests below (and in test_tenant_auth_binding.py); the header
# check still runs (a foreign tenant gets 404 before any stream starts).
STREAM_ROUTES = {
    ("GET", "/syslog/stream"): "SSE live tail; fan-out per tenant",
}

class Form(dict):
    """A request body sent as form data (HTML-form routes)."""


# Request bodies: valid payloads, so a route reaches its row lookup instead
# of failing validation first; those without path parameters point at B's
# rows. Every other mutating route gets ``{}``.
def _bodies(ids: dict[str, int]) -> dict[tuple[str, str], Any]:
    b_host, b_cfg = ids["ping_hosts"], ids["integration_configs"]
    return {
        ("PATCH", "/api/v1/hosts/bulk"): {"ids": [b_host], "updates": {"enabled": False}},
        ("POST", "/api/snmp/hosts"): {"host_id": b_host, "credential_id": ids["credentials"],
                                      "oids": []},
        ("POST", "/api/v1/maintenance-windows"): {"name": "w", "kind": "once", "host_ids": [b_host],
                                                  "starts_at": "2026-10-11T00:00:00",
                                                  "ends_at": "2026-10-12T00:00:00"},
        ("POST", "/api/rules/test"): {"source_type": "proxmox", "source_id": b_cfg,
                                      "field_path": "cpu", "operator": "gt", "threshold": "1"},
        ("POST", "/rules/add"): Form(name="r", source_type="proxmox", source_id=str(b_cfg),
                                     field_path="cpu", operator="gt", threshold="1"),
        ("POST", "/api/subnet-scanner/add-hosts"): {"ips": ["10.0.0.1"]},
        ("POST", "/api/v2/me/seen"): {},
        # Routes with path parameters (called with B's ids).
        ("POST", "/api/snmp/hosts/{config_id}/test-oids"): {"oids": ["1.3.6.1.2.1.1.1.0"]},
        ("POST", "/hosts/{host_id}/edit"): Form(name="n", hostname="10.1.1.1", check_types="icmp"),
        ("POST", "/users/{user_id}/role"): Form(role="readonly"),
        ("POST", "/users/{user_id}/password"): Form(password="Corr3ct-Horse-Battery!"),
        ("POST", "/rules/{rule_id}/edit"): Form(name="r", source_type="proxmox", field_path="cpu"),
        ("POST", "/api/v1/incidents/{incident_id}/feedback"): {"verdict": "noise"},
        ("PATCH", "/syslog/api/templates/{template_hash}/tags"): {"tags": "x"},
        ("POST", "/api/v1/glow/chat"): {"message": "what is down?"},
    }


# Writes into A's own rows whose body names one of B's rows: refused or
# ignored, never stored (checked by _foreign_refs after each call).
def _cross_ref_calls() -> list[tuple[str, str, Any]]:
    a, b = BASE[A], BASE[B]
    return [
        ("POST", f"/hosts/{a}/edit", Form(name="n", hostname="10.0.0.1", parent_id=str(b))),
        ("POST", "/rules/add", Form(name="x", source_type="proxmox", source_id=str(b),
                                    field_path="cpu", operator="gt", threshold="1")),
        ("POST", "/rules/add", Form(name="y", source_type="ping", source_id=str(b),
                                    field_path="online", operator="eq", threshold="false")),
        ("POST", f"/rules/{a}/edit", Form(name="x", source_type="proxmox", source_id=str(b))),
        ("POST", "/api/snmp/hosts", {"host_id": a, "credential_id": b}),
        ("POST", "/api/snmp/hosts", {"host_id": b}),
        ("PATCH", f"/api/snmp/hosts/{a}", {"credential_id": b}),
        ("PATCH", f"/api/v1/hosts/{a}", {"probe_id": b}),
        ("PATCH", "/api/v1/hosts/bulk", {"ids": [a, b], "updates": {"probe_id": b}}),
        ("POST", "/api/v1/maintenance-windows", {"name": "w", "kind": "once", "host_ids": [a, b],
                                                 "starts_at": "2026-10-11T00:00:00",
                                                 "ends_at": "2026-10-12T00:00:00"}),
        ("PATCH", f"/api/v1/maintenance-windows/{a}", {"host_ids": [b]}),
        ("POST", "/api/agents/install-tokens", {"note": "t", "site_id": b}),
        ("POST", "/api/subnet-scanner/schedules", {"name": "s", "cidr": "10.5.0.0/30", "site_id": b}),
        ("PATCH", f"/api/subnet-scanner/schedules/{a}", {"site_id": b}),
        ("POST", f"/api/backups/{a}/settings", {"source_config_id": b}),
        ("POST", "/api/v1/hosts", {"name": "c", "hostname": "10.1.1.2", "parent_id": b,
                                   "probe_id": b}),
    ]


# Routes with path parameters that answer before any lookup on purpose.
# Accepted only when identical to the answer for an id that exists nowhere.
PRE_LOOKUP_ROUTES = {
    # Licence check first (ee): 402 without a licence — the licensed walk
    # (test_walk_with_an_active_licence) reaches their lookup.
    ("POST", "/api/v1/incidents/{incident_id}/postmortem"): "licence-gated (ee)",
}


def _http_routes(app):
    for route in app.routes:
        if isinstance(route, (Mount, APIWebSocketRoute)):
            continue
        for method in sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}):
            yield method, route.path


def _params(path: str) -> list[str]:
    return [p.split(":")[0] for p in re.findall(r"\{([^}]+)\}", path)]


def _ids_of(tid: int) -> dict[str, Any]:
    b = BASE[tid]
    ids = {t: b for t in PARAM_TABLE.values()}
    ids["snmp_host_configs"] = b
    ids["log_templates"] = _tpl_hash(tid)
    return ids


def _missing_ids() -> dict[str, Any]:
    """Ids that exist in no tenant."""
    ids = {t: 999_999 for t in PARAM_TABLE.values()}
    ids["snmp_host_configs"] = 999_999
    ids["log_templates"] = "f" * 16
    return ids


def _concrete(path: str, ids: dict[str, Any]) -> str:
    def sub(m):
        name = m.group(1).split(":")[0]
        if name in LITERAL_PARAMS:
            return str(LITERAL_PARAMS[name])
        table = PARAM_OVERRIDES.get((path, name)) or PARAM_TABLE[name]
        return str(ids[table])
    return re.sub(r"\{([^}]+)\}", sub, path)


def test_route_classification_is_complete_and_not_stale():
    from main import app

    routes = set(_http_routes(app))
    unknown = sorted(
        f"{m} {p}: {{{n}}}" for m, p in routes for n in _params(p)
        if n not in PARAM_TABLE and n not in LITERAL_PARAMS and (p, n) not in PARAM_OVERRIDES
        and (m, p) not in TENANTLESS_ROUTES
    )
    assert not unknown, "path parameters the isolation suite cannot map to a table:\n  " + \
        "\n  ".join(unknown)
    for name, listing in (("INSTALLATION_ROUTES", INSTALLATION_ROUTES),
                          ("TENANTLESS_ROUTES", TENANTLESS_ROUTES),
                          ("STREAM_ROUTES", STREAM_ROUTES),
                          ("CONTENT_KEYED_ROUTES", CONTENT_KEYED_ROUTES)):
        stale = sorted(set(listing) - routes)
        assert not stale, f"{name} lists routes that do not exist: {stale}"
    stale = sorted({p for p, _ in PARAM_OVERRIDES} - {p for _, p in routes})
    assert not stale, f"PARAM_OVERRIDES for routes that do not exist: {stale}"
    assert all(reason for reason in {**INSTALLATION_ROUTES, **TENANTLESS_ROUTES, **STREAM_ROUTES,
                                     **CONTENT_KEYED_ROUTES}.values()), "every entry needs a reason"


# ── The environment: two tenants, a user and an API key of A ────────────────

@dataclass
class Env:
    client: Any
    sf: Any
    session_a: str
    key_a: str


async def _session(sf, user_id: int, tenant_id: int) -> str:
    token = secrets.token_hex(32)
    async with sf() as s:
        s.add(Session(token=_hash_token(token), user_id=user_id, active_tenant_id=tenant_id,
                      expires_at=datetime.utcnow() + timedelta(days=1)))
        await s.commit()
    return token


async def _api_key(sf, tenant_id: int) -> str:
    from models import ApiKey
    from routers.api_v1 import _hash_key

    raw = f"ng_{secrets.token_hex(12)}"
    async with sf() as s:
        with tenant_scope(tenant_id):
            s.add(ApiKey(name="walker", key_hash=_hash_key(raw), prefix=raw[:8], role="admin",
                         enabled=True, hash_algo="hmac"))
            await s.commit()
    return raw


@dataclass
class ChCall:
    sql: str
    parameters: dict
    settings: dict
    kind: str = "query"
    table: str | None = None
    column_names: list | None = None
    data: list | None = None
    tenant: int | None = None  # the tenant context the call was made in


class _Result:
    def __init__(self, rows: list[dict] | None = None):
        rows = rows or []
        self.column_names = list(rows[0]) if rows else []
        self.result_rows = [tuple(r.values()) for r in rows]


def _ctx_tenant() -> int | None:
    from services.tenancy import current

    ctx = current()
    return ctx.tenant_id if ctx is not None and not ctx.system else None


class FakeClickHouse:
    """Stands in for the clickhouse-connect async client; records every call.

    ``responders`` give a scoped read canned rows of *its* tenant: a list of
    ``(regex on the SQL, fn(tenant_id, parameters) -> rows)``. A read that is
    not scoped gets nothing — like the row policy, it cannot see any tenant.
    """

    def __init__(self):
        self.calls: list[ChCall] = []
        self.responders: list[tuple[str, Any]] = []

    async def query(self, sql, parameters=None, settings=None, **kw):
        call = ChCall(sql, dict(parameters or {}), dict(settings or {}), tenant=_ctx_tenant())
        self.calls.append(call)
        tid = call.parameters.get(_ch.TENANT_PARAM)
        if tid is not None and ch_call_is_scoped(call, int(tid)):
            for pattern, fn in self.responders:
                if re.search(pattern, sql, re.S | re.I):
                    return _Result(fn(int(tid), call.parameters))
        return _Result()

    async def command(self, sql, parameters=None, settings=None, **kw):
        self.calls.append(ChCall(sql, dict(parameters or {}), dict(settings or {}), kind="command",
                                 tenant=_ctx_tenant()))
        return None

    async def insert(self, table, data=None, column_names=None, settings=None, **kw):
        self.calls.append(ChCall("", {}, dict(settings or {}), kind="insert", table=table,
                                 column_names=list(column_names or []),
                                 data=[list(r) for r in (data or [])], tenant=_ctx_tenant()))
        return None


def _owner_of(column: str, value) -> int | None:
    """The seeded tenant of a host / agent / probe id in an inserted row."""
    if column not in ("host_id", "agent_id", "probe_id") or not isinstance(value, int):
        return None
    for tid, base in BASE.items():
        if value in (base, base + 1):
            return tid
    return None


def ch_call_is_scoped(call: ChCall, tenant_id: int) -> bool:
    """Whether a ClickHouse call is confined to ``tenant_id`` — phase 6's
    contract (services/clickhouse_client.py):

    * a read names every tenant table it reads (``tenant_tables_read``) with
      ``tenant_id = {nodeglow_tenant:UInt32}`` — at least one placeholder per
      read — and binds ``nodeglow_tenant`` to the tenant; or
    * it runs with the row-policy setting ``SQL_nodeglow_tenant`` = tenant;
    * a read of no tenant table (``system.*``, metadata) is installation-level;
    * an insert stamps every row with the tenant, and a row naming a seeded
      host / agent / probe stamps that row's owner.

    ``additional_table_filters`` is phase 6's server-side net; it is checked
    in addition (when present it must name the same tenant), not instead.
    """
    if call.kind == "insert":
        cols = call.column_names or []
        if "tenant_id" not in cols:
            return False
        i = cols.index("tenant_id")
        for row in call.data or []:
            if row[i] != tenant_id:
                return False
            for col, value in zip(cols, row):
                owner = _owner_of(col, value)
                if owner is not None and owner != tenant_id:
                    return False
        return True
    reads = _ch.tenant_tables_read(call.sql)
    if not reads:
        return True
    filters = call.settings.get("additional_table_filters")
    if filters is not None:
        for table in set(reads):
            if f"'{table}': 'tenant_id = {tenant_id}'" not in str(filters):
                return False
    setting = call.settings.get(_ch.ROW_POLICY_SETTING)
    if setting is not None and str(setting) == str(tenant_id):
        return True
    placeholders = call.sql.count("{" + _ch.TENANT_PARAM + ":")
    return placeholders >= len(reads) and str(call.parameters.get(_ch.TENANT_PARAM)) == str(tenant_id)


def test_the_clickhouse_matcher_knows_phase_6s_contract():
    """Self-test: a mechanism change must break here, loudly."""
    p6 = ChCall(f"SELECT count() FROM ping_checks WHERE {_ch.TENANT_FILTER}",
                {_ch.TENANT_PARAM: 1}, _ch.tenant_query_settings(1, row_policy=False))
    assert ch_call_is_scoped(p6, 1) and not ch_call_is_scoped(p6, 2)
    unscoped = ChCall("SELECT count() FROM ping_checks", {"tenant": 1}, {})
    assert not ch_call_is_scoped(unscoped, 1)
    tricky = ChCall("SELECT tenant_id FROM ping_checks WHERE host_id = {tenant_host:UInt32}",
                    {"tenant_host": 1}, {})
    assert not ch_call_is_scoped(tricky, 1)
    join = ChCall(f"SELECT 1 FROM ping_checks p JOIN agent_metrics a ON 1 WHERE p.{_ch.TENANT_FILTER}",
                  {_ch.TENANT_PARAM: 1}, {})
    assert not ch_call_is_scoped(join, 1)  # two reads, one predicate
    policy = ChCall("SELECT count() FROM syslog_messages", {}, {_ch.ROW_POLICY_SETTING: "1"})
    assert ch_call_is_scoped(policy, 1)
    assert ch_call_is_scoped(ChCall("SELECT 1 FROM system.parts", {}, {}), 1)
    ins = ChCall("", {}, {}, kind="insert", column_names=["tenant_id", "host_id"], data=[[1, BASE[A]]])
    assert ch_call_is_scoped(ins, 1)
    wrong = ChCall("", {}, {}, kind="insert", column_names=["tenant_id", "host_id"], data=[[1, BASE[B]]])
    assert not ch_call_is_scoped(wrong, 1)


@asynccontextmanager
async def _real_clickhouse_path(fake: FakeClickHouse):
    """Route every ClickHouse access through ``fake``: the real query
    helpers (make_client mocks them) and modules that bound the mocks at
    import time."""
    import sys

    patches = [patch("services.clickhouse_client.get_client", new=_async_return(fake)),
               patch("services.clickhouse_client.query", new=_REAL_CH_QUERY),
               patch("services.clickhouse_client.query_scalar", new=_REAL_CH_SCALAR)]
    patches += [patch(f"services.clickhouse_client.{name}", new=fn)
                for name, fn in _REAL_CH_INSERTS.items()]
    for name, mod in list(sys.modules.items()):
        if not name.startswith(("routers", "services", "scheduler", "nodeglow_ee", "main",
                                "notifications")):
            continue
        for attr, val in list(vars(mod).items()):
            fn = getattr(getattr(val, "side_effect", None), "__name__", "")
            if fn == "_ch_query_mock":
                patches.append(patch.object(mod, attr, _REAL_CH_QUERY))
            elif fn == "_ch_scalar_mock":
                patches.append(patch.object(mod, attr, _REAL_CH_SCALAR))
    for p in patches:
        p.start()
    try:
        yield fake
    finally:
        for p in reversed(patches):
            p.stop()


def _async_return(value):
    async def f(*a, **k):
        return value
    return f


@pytest.fixture
async def env():
    from services import shared_state

    shared_state.reset()  # rate limits are process-global
    async with make_client(fake_user=None) as (client, sf):
        await _seed(sf)
        e = Env(client=client, sf=sf, session_a=await _session(sf, BASE[A], A),
                key_a=await _api_key(sf, A))
        # Nothing in the walk may reach the network.
        with patch("utils.ping.check_host", side_effect=_no_network), \
                patch("routers.ping.check_host", side_effect=_no_network), \
                patch("utils.ping.get_ssl_expiry_days", new=_async_return(None)), \
                patch("ssl.get_server_certificate", side_effect=OSError("no network in tests")), \
                patch("socket.gethostbyaddr", side_effect=OSError("no network in tests")), \
                patch("socket.gethostbyname", side_effect=OSError("no network in tests")), \
                patch("routers.update._sidecar_post", side_effect=RuntimeError("no sidecar")), \
                patch("httpx.AsyncHTTPTransport.handle_async_request",
                      side_effect=RuntimeError("network blocked in tests")):
            yield e
    shared_state.reset()


async def _no_network(*a, **k):
    raise OSError("no network in tests")


# ── The walk ─────────────────────────────────────────────────────────────────

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


async def _call(client, method, url, *, auth: dict, body=None):
    headers = dict(auth.get("headers", {}))
    cookies = auth.get("cookies", {})
    client.cookies.clear()
    csrf = client.headers.get("x-csrf-token")
    if csrf:
        client.cookies.set("ng_csrf", csrf)
    for k, v in cookies.items():
        client.cookies.set(k, v)
    kwargs = {"headers": headers, "follow_redirects": False}
    if method in MUTATING:
        if isinstance(body, Form):
            kwargs["data"] = dict(body)
        else:
            kwargs["json"] = body if body is not None else {}
    return await client.request(method, url, **kwargs)


def _session_auth(e: Env, **headers) -> dict:
    return {"cookies": {"nodeglow_session": e.session_a}, "headers": headers}


def _key_auth(e: Env, **headers) -> dict:
    return {"headers": {"X-API-Key": e.key_a, **headers}}


def _walkable(app):
    for method, path in _http_routes(app):
        if (method, path) in TENANTLESS_ROUTES or (method, path) in STREAM_ROUTES:
            continue
        if path.startswith("/static"):
            continue
        yield method, path


async def _walk(e: Env, auth: dict, *, accept_unauthenticated: bool,
                licensed: bool = False) -> tuple[list[str], int]:
    """Every walkable route, as A: B's ids → 404, B unchanged, no canary,
    A's rows reference nothing of B's."""
    from main import app

    failures: list[str] = []
    checked = 0
    ids_b = _ids_of(B)
    bodies = _bodies(ids_b)
    before = await _tenant_rows(e.sf, B)
    assert not await _foreign_refs(e.sf), "the seed itself crosses tenants"
    for method, path in _walkable(app):
        has_params = bool([n for n in _params(path) if n not in LITERAL_PARAMS])
        url = _concrete(path, ids_b) if _params(path) else path
        try:
            resp = await _call(e.client, method, url, auth=auth, body=bodies.get((method, path)))
        except Exception as exc:  # noqa: BLE001 — a crash is reported, not an isolation verdict
            import traceback

            where = traceback.extract_tb(exc.__traceback__)[-1]
            failures.append(f"{method} {path}: raised {type(exc).__name__}: {exc} "
                            f"({where.filename.rsplit('backend', 1)[-1]}:{where.lineno})")
            continue
        if accept_unauthenticated and (resp.status_code in (401, 403) or _is_login_redirect(resp)):
            continue  # this credential is not accepted here: nothing served
        checked += 1
        allowed = (method, path) in INSTALLATION_ROUTES
        if CANARY in resp.text and not allowed:
            failures.append(f"{method} {url} -> {resp.status_code} leaks {CANARY}: "
                            f"{_excerpt(resp.text)}")
        if has_params and resp.status_code != 404 and not allowed \
                and (method, path) not in CONTENT_KEYED_ROUTES:
            same = False
            if resp.status_code in PRE_LOOKUP_STATUSES and (method, path) in PRE_LOOKUP_ROUTES \
                    and not licensed:
                missing = await _call(e.client, method, _concrete(path, _missing_ids()), auth=auth,
                                      body=bodies.get((method, path)))
                same = (missing.status_code, missing.text) == (resp.status_code, resp.text)
            if not same:
                failures.append(f"{method} {url} -> {resp.status_code}, expected 404 for B's id"
                                + (f": {resp.text[:120]}" if resp.status_code in PRE_LOOKUP_STATUSES
                                   else ""))
        if resp.status_code >= 500 and resp.status_code != 503:  # 503: no update sidecar
            failures.append(f"{method} {url} -> {resp.status_code}: {resp.text[:200]}")
        if method in MUTATING:
            after = await _tenant_rows(e.sf, B)
            if after != before:
                changed = [t for t in after if after[t] != before[t]]
                failures.append(f"{method} {url} changed tenant B's rows in {changed}")
                before = after
            for ref in await _foreign_refs(e.sf):
                failures.append(f"{method} {url} left A referencing B: {ref}")
    return failures, checked


def _is_login_redirect(resp) -> bool:
    return resp.status_code in (301, 302, 303, 307, 308) and \
        resp.headers.get("location", "").rstrip("/").endswith("/login")


def _excerpt(text: str) -> str:
    i = text.find(CANARY)
    return text[max(0, i - 80):i + 60].replace("\n", " ")


async def test_walk_as_a_user_of_a(env):
    failures, checked = await _walk(env, _session_auth(env), accept_unauthenticated=False)
    assert checked > 120, f"only {checked} routes walked — enumeration is broken"
    assert not failures, "tenant isolation failures (session of A):\n  " + "\n  ".join(failures)


async def test_walk_with_an_api_key_of_a(env):
    failures, checked = await _walk(env, _key_auth(env), accept_unauthenticated=True)
    assert checked > 40, f"only {checked} routes accepted the API key"
    assert not failures, "tenant isolation failures (API key of A):\n  " + "\n  ".join(failures)


async def test_a_cannot_reference_b_objects(env):
    """Writes to A's own rows that name B's rows in the body are refused or
    ignored — A never ends up pointing at B (on PostgreSQL the composite
    foreign keys would refuse it with a 500; here the routes must)."""
    failures = []
    before = await _tenant_rows(env.sf, B)
    for method, url, body in _cross_ref_calls():
        resp = await _call(env.client, method, url, auth=_session_auth(env), body=body)
        if resp.status_code >= 500:
            failures.append(f"{method} {url} -> {resp.status_code}")
        if CANARY in resp.text:
            failures.append(f"{method} {url} leaks {CANARY}")
        for ref in await _foreign_refs(env.sf):
            failures.append(f"{method} {url} {body!r}: {ref}")
        if await _foreign_refs(env.sf):
            break
    assert await _tenant_rows(env.sf, B) == before
    assert not failures, "A stored references to B's rows:\n  " + "\n  ".join(failures)


async def test_the_reference_check_sees_a_cross_tenant_reference(env):
    """Self-test of _foreign_refs: A rows pointing at B are reported. (Soft
    references: a real foreign key such as ping_hosts.parent_id is refused
    by PostgreSQL's composite keys before the check could see it.)"""
    from models import AlertRule, MaintenanceWindow

    assert not await _foreign_refs(env.sf)
    async with env.sf() as s:
        s.add(AlertRule(name="x", source_type="ping", source_id=BASE[B], field_path="f",
                        operator="gt"))
        (await s.get(MaintenanceWindow, BASE[A])).host_ids = f"[{BASE[A]}, {BASE[B]}]"
        await s.commit()
    found = await _foreign_refs(env.sf)
    assert any("alert_rules.source_id" in f for f in found), found
    assert any("maintenance_windows.host_ids" in f for f in found), found


@pytest.fixture
def licensed_ai():
    """An active ee licence and a fake AI provider recording every prompt."""
    nodeglow_ee = pytest.importorskip("nodeglow_ee")  # noqa: F841
    from nodeglow_ee import license_runtime
    from services import ai_client

    prompts: list[str] = []

    async def completion(system_prompt=None, user_message=None, *a, **kw):
        prompts.append(f"{system_prompt}\n{user_message}\n{kw.get('history', '')}")
        return ("answer", {"input_tokens": 1, "output_tokens": 1}) if kw.get("return_usage") \
            else "answer"

    import sys as _sys

    real = ai_client.generate_completion
    patches = [patch.object(license_runtime, "is_active", _async_return(True)),
               patch.object(license_runtime, "blocked", _async_return(None)),
               patch.object(ai_client, "generate_completion", completion)]
    for name in ("nodeglow_ee.ai.postmortem", "nodeglow_ee.ai.glow", "nodeglow_ee.ai.daily_summary"):
        try:
            __import__(name)
        except ImportError:
            continue
    for mod in list(_sys.modules.values()):
        if mod is not None and getattr(mod, "generate_completion", None) is real \
                and mod is not ai_client:
            patches.append(patch.object(mod, "generate_completion", completion))
    for p in patches:
        p.start()
    try:
        yield prompts
    finally:
        for p in reversed(patches):
            p.stop()


async def test_walk_with_an_active_licence(env, licensed_ai, monkeypatch):
    """The ee routes behind the licence (Glow chat, postmortem, test summary)
    run for real: B's ids → 404, and no AI prompt built for A carries B."""
    from tests.conftest import put_settings
    from database import encrypt_value

    async with env.sf() as s:
        await put_settings(s, tenant_id=A, ai_enabled="1")
        await put_settings(s, claude_api_key=encrypt_value("sk-test"))
    failures, checked = await _walk(env, _session_auth(env), accept_unauthenticated=False,
                                    licensed=True)
    assert not failures, "tenant isolation failures (licensed):\n  " + "\n  ".join(failures)
    # Glow for A, with A's own incident: the context is A's alone.
    resp = await _call(env.client, "POST", "/api/v1/glow/chat", auth=_session_auth(env),
                       body={"message": "what happened to my hosts and incidents?"})
    assert resp.status_code == 200, resp.text[:300]
    from models import Incident
    async with env.sf() as s:
        (await s.get(Incident, BASE[A])).status = "resolved"
        await s.commit()
    resp = await _call(env.client, "POST", f"/api/v1/incidents/{BASE[A]}/postmortem",
                       auth=_session_auth(env))
    assert resp.status_code in (200, 202), resp.text[:300]
    for _ in range(20):
        import asyncio
        await asyncio.sleep(0.01)
    assert licensed_ai, "no AI prompt was built — the licensed routes did not run"
    leaked = [p[:200] for p in licensed_ai if CANARY in p]
    assert not leaked, f"AI prompts for A mention B: {leaked}"


async def test_foreign_tenant_header_is_not_found_everywhere(env):
    """A's user names tenant B (not a member): every tenant route → 404,
    whether it has path parameters or not — before any route code runs."""
    from main import app

    failures = []
    routes = list(_walkable(app)) + list(STREAM_ROUTES)
    for method, path in routes:
        url = _concrete(path, _ids_of(B)) if _params(path) else path
        for label, auth in (("session", _session_auth(env, **{"X-Nodeglow-Tenant": "2"})),
                            ("api key", _key_auth(env, **{"X-Nodeglow-Tenant": "2"}))):
            resp = await _call(env.client, method, url, auth=auth)
            if label == "api key" and (resp.status_code in (401, 403) or _is_login_redirect(resp)):
                continue  # the key is not accepted on this route at all
            if resp.status_code != 404 or CANARY in resp.text:
                failures.append(f"{label}: {method} {url} -> {resp.status_code}")
    assert not failures, "routes serving a foreign tenant header:\n  " + "\n  ".join(failures)


async def test_the_canaries_are_reachable_from_their_own_tenant(env):
    """Control: B's own user sees B's canaries on the main lists — the walk
    passes because of isolation, not because the routes return nothing."""
    session_b = await _session(env.sf, BASE[B], B)
    auth = {"cookies": {"nodeglow_session": session_b}, "headers": {}}
    seen = 0
    for path in ("/api/v1/hosts", "/api/v1/incidents", "/api/v1/integrations", "/api/v1/agents",
                 "/api/credentials/list", "/api/v1/rules", "/api/backups", "/settings/json",
                 "/settings/notifications/history", "/api/v1/audit", "/api/users",
                 "/api/v1/maintenance-windows", "/api/subnet-scanner/page-data",
                 "/syslog/api/templates", "/api/agents/install-tokens", "/api/v1/keys"):
        resp = await _call(env.client, "GET", path, auth=auth)
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
        seen += CANARY in resp.text
        assert "CANARY-A" not in resp.text, f"{path} shows tenant A to B"
    assert seen >= 14, f"only {seen} lists show tenant B's own rows"


async def test_installation_routes_are_operator_only(env, monkeypatch):
    """The allowlisted routes may show the whole installation because only
    the operator reaches them: a tenant admin who is not the operator is
    refused."""
    from services.tenancy import SingleTenancy

    monkeypatch.setattr(SingleTenancy, "is_operator", lambda self, *a, **k: False)
    monkeypatch.setattr(SingleTenancy, "active_tenant_ids", _async_return([A, B]))
    for method, path in INSTALLATION_ROUTES:
        url = _concrete(path, _ids_of(B)) if _params(path) else path
        if path == "/api/snmp/mibs/upload":  # multipart, validated before the handler
            env.client.cookies.set("nodeglow_session", env.session_a)
            resp = await env.client.post(url, files={"file": ("X-MIB.txt", b"X DEFINITIONS ::= BEGIN END")})
        else:
            resp = await _call(env.client, method, url, auth=_session_auth(env))
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code} for a non-operator"
        assert CANARY not in resp.text


# ── Streams: /ws/live and the syslog live tail, over the real ASGI app ─────

class _Asgi:
    """A raw ASGI conversation with the app (httpx cannot speak WebSocket
    or read an endless SSE response)."""

    def __init__(self, app, scope):
        import asyncio

        self.inbox: asyncio.Queue = asyncio.Queue()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(app(scope, self.inbox.get, self.outbox.put))

    async def next(self, timeout=5.0):
        import asyncio

        return await asyncio.wait_for(self.outbox.get(), timeout)

    async def drain(self) -> list[dict]:
        import asyncio

        for _ in range(20):
            await asyncio.sleep(0)
        out = []
        while not self.outbox.empty():
            out.append(self.outbox.get_nowait())
        return out

    async def close(self, message):
        import asyncio

        await self.inbox.put(message)
        try:
            await asyncio.wait_for(self.task, 5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — teardown
            self.task.cancel()


def _scope(kind: str, path: str, cookie: str, query: str = "") -> dict:
    return {
        "type": kind, "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": query.encode(), "scheme": "ws" if kind == "websocket" else "http",
        "server": ("test", 80), "client": ("127.0.0.1", 50000), "http_version": "1.1",
        "method": "GET", "subprotocols": [],
        "headers": [(b"host", b"test"), (b"cookie", f"nodeglow_session={cookie}".encode()),
                    (b"accept", b"text/event-stream")],
    }


async def test_websocket_of_a_receives_nothing_of_b(env):
    from main import app
    from services import websocket as hub

    # A foreign tenant on the handshake: refused like HTTP (4404), not accepted.
    foreign = _Asgi(app, _scope("websocket", "/ws/live", env.session_a, "tenant=2"))
    await foreign.inbox.put({"type": "websocket.connect"})
    msg = await foreign.next()
    assert msg["type"] == "websocket.close" and msg.get("code") == 4404, msg
    await foreign.close({"type": "websocket.disconnect", "code": 1000})

    own = _Asgi(app, _scope("websocket", "/ws/live", env.session_a))
    await own.inbox.put({"type": "websocket.connect"})
    assert (await own.next())["type"] == "websocket.accept"
    await hub.broadcast_ping_update(BASE[B], f"{CANARY}-host", False, None, tenant_id=B)
    await hub.broadcast("agent_metric", {"agent_name": f"{CANARY}-agent"}, tenant_id=B)
    await hub.broadcast_ping_update(BASE[A], "CANARY-A-host", True, 1.0, tenant_id=A)
    sent = [m.get("text", "") for m in await own.drain() if m["type"] == "websocket.send"]
    assert len(sent) == 1 and "CANARY-A-host" in sent[0], sent
    assert not any(CANARY in m for m in sent)
    await own.close({"type": "websocket.disconnect", "code": 1000})


async def test_syslog_live_tail_of_a_receives_nothing_of_b(env):
    import asyncio

    from main import app
    from services import syslog

    stream = _Asgi(app, _scope("http", "/syslog/stream", env.session_a))
    start = await stream.next()
    assert start["type"] == "http.response.start" and start["status"] == 200, start
    for _ in range(50):  # the generator subscribes once the body starts
        if any(t == A for t, _q in syslog._subscribers):
            break
        await asyncio.sleep(0.01)
    assert {t for t, _q in syslog._subscribers} == {A}
    with patch("services.syslog._buffer", []):
        await syslog._enqueue({syslog.TENANT_KEY: B, "message": f"{CANARY}-syslog",
                               "timestamp": datetime.utcnow(), "severity": 3,
                               "source_ip": "20.0.0.1", "hostname": "b"})
        await syslog._enqueue({syslog.TENANT_KEY: A, "message": "CANARY-A-syslog",
                               "timestamp": datetime.utcnow(), "severity": 3,
                               "source_ip": "10.0.0.1", "hostname": "a"})
    body = b""
    for _ in range(20):
        try:
            msg = await stream.next(timeout=1)
        except asyncio.TimeoutError:
            break
        body += msg.get("body", b"")
        if b"CANARY-A-syslog" in body:
            break
    assert b"CANARY-A-syslog" in body and CANARY.encode() not in body, body[:300]
    await stream.close({"type": "http.disconnect"})
    syslog._subscribers.clear()

# ── ClickHouse: every query made for A is scoped to A ───────────────────────

async def _ch_unscoped(e: Env, method: str, path: str, auth: dict) -> list[str]:
    fake = FakeClickHouse()
    async with _real_clickhouse_path(fake):
        url = _concrete(path, _ids_of(A)) if _params(path) else path
        try:
            await _call(e.client, method, url, auth=auth,
                        body=_bodies(_ids_of(A)).get((method, path)))
        except Exception:  # noqa: BLE001 — crashes are the walk's business; queries count
            pass
    bad = [c.sql.strip().split("\n")[0][:120] or f"insert {c.table}"
           for c in fake.calls if not ch_call_is_scoped(c, A)]
    return bad, fake.calls


async def test_clickhouse_calls_are_tenant_scoped(env):
    """Walk every route as A (A's ids, mutating routes included) with a
    recording ClickHouse client: every ClickHouse read and insert made while
    serving A is scoped to A (ch_call_is_scoped). A new route reading
    ClickHouse without the tenant fails here."""
    from main import app

    bad_routes, with_calls = [], 0
    # Reads first: the mutating routes act on A's own rows (they may delete
    # the very hosts the reads would report on, or the caller's user).
    routes = sorted(_walkable(app), key=lambda r: r[0] in MUTATING)
    for method, path in routes:
        auth = _session_auth(env)
        if method in MUTATING:  # a fresh session each time (a route may end the old one)
            auth = {"cookies": {"nodeglow_session": await _session(env.sf, BASE[A], A)},
                    "headers": {}}
        bad, calls = await _ch_unscoped(env, method, path, auth)
        with_calls += bool(calls)
        if bad:
            bad_routes.append(f"{method} {path}: {bad[0]}")
    assert with_calls > 20, f"only {with_calls} routes reached ClickHouse — the fake is not wired"
    assert not bad_routes, "routes with ClickHouse calls not scoped to the tenant:\n  " + \
        "\n  ".join(bad_routes)


# Mutating routes that read ClickHouse for their answer.
_READ_LIKE_MUTATIONS: set[tuple[str, str]] = {
    ("POST", "/api/rules/test"),
    ("POST", "/api/v1/glow/chat"),
    ("POST", "/api/v1/incidents/{incident_id}/postmortem"),
    ("POST", "/settings/ai/test-summary"),
}

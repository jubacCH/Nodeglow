"""
Generic router for all integrations.

Provides CRUD (list, detail, add, edit, delete), test-connection,
and JSON API endpoints for every registered integration.
Integration-specific custom routes can be added via BaseIntegration.get_router().
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession


from integrations import get_registry, get_integration
from integrations._base import BaseIntegration
from models.api_key import ApiKey
from models.base import get_db
from ratelimit import rate_limit
from routers.api_v1 import require_admin
from services import integration as int_svc
from services import snapshot as snap_svc
from services.host_state import iso


def _endpoint_summary(fields, config: dict) -> dict:
    """Non-secret endpoint values for the audit trail (never secrets)."""
    return {
        f.key: str(config.get(f.key) or "")
        for f in fields
        if f.key in ENDPOINT_FIELD_KEYS
    }


async def _audit(db, request, action: str, target_id, target_name, details: dict):
    from services.audit import log_action
    details = {k: v for k, v in details.items() if v is not None}
    try:
        await log_action(db, request, action, "integration", target_id, target_name,
                         details=details)
        await db.commit()
    except Exception as exc:  # auditing must not undo a completed change
        log.warning("Audit log for %s failed: %s", action, exc)


# Config keys that name the remote endpoint an integration talks to. Changing
# one of these redirects where the stored credentials are sent.
ENDPOINT_FIELD_KEYS = ("host", "url", "base_url", "server", "address", "endpoint")

# SSRF guards live in utils.net_safety (shared with notifications/webhooks);
# re-exported under the old names for existing callers (routers/ping.py, tests).
from utils.net_safety import (  # noqa: E402
    INTERNAL_HOSTS as _INTERNAL_HOSTS,
    METADATA_HOSTS as _METADATA_HOSTS,
    METADATA_IPS as _METADATA_IPS,
    blocked_ip_reason as _blocked_ip_reason,
    extract_host as _extract_host,
    resolve_all as _resolve_all,
    validate_host as _validate_host,
    validate_host_async,
)

__all__ = [
    "ENDPOINT_FIELD_KEYS", "_INTERNAL_HOSTS", "_METADATA_HOSTS", "_METADATA_IPS",
    "_blocked_ip_reason", "_extract_host", "_resolve_all", "_validate_host",
    "validate_host_async",
]


def _validate_config_hosts(config_dict: dict, fields) -> str | None:
    """Validate all host/url fields in a config dict."""
    for f in fields:
        if f.key in ENDPOINT_FIELD_KEYS:
            err = _validate_host(str(config_dict.get(f.key, "")))
            if err:
                return f"{f.label}: {err}"
    return None


async def _validate_config_hosts_async(config_dict: dict, fields) -> str | None:
    import asyncio
    return await asyncio.to_thread(_validate_config_hosts, config_dict, fields)


def _config_problems(integration_cls: type[BaseIntegration], config_dict: dict) -> list[str]:
    """What the integration itself finds wrong with a configuration
    (BaseIntegration.validate_config). A validator that fails does not block
    the save: the collector reports the same problems at its first run."""
    try:
        problems = integration_cls(config=config_dict).validate_config(config_dict)
    except Exception:
        log.exception("validate_config of %s failed", integration_cls.name)
        return []
    return [str(p) for p in problems or [] if p]


def _problems_response(problems: list[str]) -> JSONResponse:
    shown = problems[:5]
    more = len(problems) - len(shown)
    text = "; ".join(shown) + (f" (and {more} more)" if more > 0 else "")
    return JSONResponse({"error": text, "code": "invalid_config", "problems": problems},
                        status_code=400)


def _is_secret_field(field) -> bool:
    return field.field_type == "password" or bool(getattr(field, "encrypted", False))


def _normalise_endpoint(value) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _endpoint_changed(fields, old: dict, new: dict) -> list[str]:
    """Endpoint fields whose value differs between the stored and new config."""
    return [
        f.key for f in fields
        if f.key in ENDPOINT_FIELD_KEYS
        and _normalise_endpoint(old.get(f.key)) != _normalise_endpoint(new.get(f.key))
    ]


# Checkbox keys that turn TLS certificate verification on (True = verify).
TLS_VERIFY_FIELD_KEYS = ("verify_ssl",)


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _tls_verification_disabled(fields, old: dict, new: dict) -> bool:
    """True when an edit switches TLS certificate verification from on to off."""
    for f in fields:
        if f.key not in TLS_VERIFY_FIELD_KEYS:
            continue
        default = f.default if f.default is not None else True
        was_on = _truthy(old.get(f.key, default))
        if was_on and not _truthy(new.get(f.key, default)):
            return True
    return False


logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_form_config(integration_cls: type[BaseIntegration], form: dict,
                       existing_config: dict | None = None) -> dict:
    """
    Extract config values from submitted form data based on config_fields.
    For edit operations, if a password field is empty, keep the existing value.
    """
    config = {}
    for field in integration_cls.config_fields:
        raw = form.get(field.key, "")
        if isinstance(raw, str):
            raw = raw.strip()

        if field.field_type == "checkbox":
            config[field.key] = raw in ("on", "true", "True", True, "1")
        elif field.field_type == "password":
            # Don't overwrite existing secret if form field is empty
            if not raw and existing_config:
                config[field.key] = existing_config.get(field.key, "")
            else:
                config[field.key] = raw
        elif field.field_type == "number":
            try:
                config[field.key] = int(raw) if raw else field.default
            except (ValueError, TypeError):
                config[field.key] = field.default
        else:
            config[field.key] = raw if raw else (field.default or "")

    # Not declared per class (see api_config_fields), so extract it here too.
    # Empty means "use the integration's default", which effective_interval
    # resolves — storing 0 or "" would be indistinguishable from an explicit
    # choice, so the key is simply omitted.
    raw_interval = form.get("poll_interval_seconds", "")
    if isinstance(raw_interval, str):
        raw_interval = raw_interval.strip()
    if raw_interval not in ("", None):
        try:
            parsed = int(raw_interval)
            if parsed > 0:
                config["poll_interval_seconds"] = parsed
        except (TypeError, ValueError):
            pass  # leave unset; the default applies
    return config



# ── JSON API: config fields ──────────────────────────────────────────────────


@router.get("/api/integration/{integration_type}/fields")
async def api_config_fields(integration_type: str):
    """Return the config fields schema for an integration type."""
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)
    fields = []
    for f in integration_cls.config_fields:
        fields.append({
            "key": f.key,
            "label": f.label,
            "field_type": f.field_type,
            "placeholder": f.placeholder or "",
            "required": f.required,
            "default": f.default if f.default is not None else "",
            "options": f.options if hasattr(f, "options") and f.options else None,
        })

    # Offered on every integration rather than declared in each class: how often
    # to poll is a property of the deployment, not of the integration. The
    # placeholder shows what it would do if left empty.
    default_interval = getattr(integration_cls, "default_interval_seconds", 60)
    fields.append({
        "key": "poll_interval_seconds",
        "label": "Poll interval (seconds)",
        "field_type": "number",
        "placeholder": f"Default: {default_interval}",
        "required": False,
        "default": "",
        "options": None,
    })
    return JSONResponse({
        "type": integration_type,
        "display_name": integration_cls.display_name,
        "description": integration_cls.description,
        "fields": fields,
    })


# ── JSON API: create instance ────────────────────────────────────────────────


@router.post("/api/integration/{integration_type}/create")
@rate_limit(max_requests=10, window_seconds=60)
async def api_create_instance(
    request: Request,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """JSON API for creating an integration instance. Admin only: an
    integration holds credentials for other systems."""
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)

    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        name = f"{integration_cls.display_name} Instance"
    cluster_group = str(body.get("cluster_group", "")).strip() or None
    config_dict = {}
    for field in integration_cls.config_fields:
        val = body.get(field.key, "")
        if field.field_type == "checkbox":
            config_dict[field.key] = bool(val)
        elif field.field_type == "number":
            try:
                config_dict[field.key] = int(val) if val else (field.default if field.default is not None else 0)
            except (ValueError, TypeError):
                config_dict[field.key] = field.default if field.default is not None else 0
        else:
            config_dict[field.key] = str(val).strip() if val else (str(field.default) if field.default is not None else "")
    host_err = await _validate_config_hosts_async(config_dict, integration_cls.config_fields)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)
    if problems := _config_problems(integration_cls, config_dict):
        return _problems_response(problems)
    cfg = await int_svc.create_config(
        db, integration_type, name, config_dict, cluster_group=cluster_group,
    )
    await _audit(db, request, "integration.create", cfg.id, cfg.name,
                 {"type": integration_type,
                  **_endpoint_summary(integration_cls.config_fields, config_dict)})
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return JSONResponse({"ok": True, "id": cfg.id, "name": cfg.name})


# ── JSON API: edit instance ──────────────────────────────────────────────────


@router.patch("/api/integration/{integration_type}/{config_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def api_edit_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """JSON API for editing an integration instance. Admin only.

    Secret fields left empty keep their stored value — EXCEPT when the
    endpoint (host/url/…) changes. Then every stored secret has to be supplied
    again, otherwise anyone allowed to edit could repoint the integration at a
    server they control and receive the stored credentials on the next poll.
    """
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"error": "Instance not found"}, status_code=404)

    existing_config = await int_svc.decrypt_config(db, cfg)
    body = await request.json()
    name = str(body.get("name", "")).strip() or cfg.name
    config_dict = {}
    for field in integration_cls.config_fields:
        val = body.get(field.key)
        if val is None:
            # Keep existing value if not provided
            config_dict[field.key] = existing_config.get(field.key, "")
            continue
        if field.field_type == "password" and val == "":
            # Don't overwrite password with empty string
            config_dict[field.key] = existing_config.get(field.key, "")
        elif field.field_type == "checkbox":
            config_dict[field.key] = bool(val)
        elif field.field_type == "number":
            try:
                config_dict[field.key] = int(val) if val else (field.default if field.default is not None else 0)
            except (ValueError, TypeError):
                config_dict[field.key] = field.default if field.default is not None else 0
        else:
            config_dict[field.key] = str(val).strip()

    fields = integration_cls.config_fields
    changed_endpoints = _endpoint_changed(fields, existing_config, config_dict)
    # Turning certificate verification off is as good as repointing the
    # endpoint: anyone on the path can then impersonate the server and collect
    # the stored credentials. Same rule — secrets must be supplied again.
    tls_disabled = _tls_verification_disabled(fields, existing_config, config_dict)
    if changed_endpoints or tls_disabled:
        missing = [
            f.key for f in fields
            if _is_secret_field(f)
            and existing_config.get(f.key)          # a secret is stored …
            and not str(body.get(f.key) or "").strip()  # … and not re-supplied
        ]
        if missing:
            labels = [f.label for f in fields if f.key in missing]
            what = ("Changing the address of an integration" if changed_endpoints
                    else "Disabling TLS certificate verification")
            return JSONResponse({
                "error": (
                    f"{what} requires entering its credentials again: "
                    + ", ".join(labels)
                ),
                "code": "secrets_required",
                "missing_fields": missing,
            }, status_code=400)

    host_err = await _validate_config_hosts_async(config_dict, fields)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)
    if problems := _config_problems(integration_cls, config_dict):
        return _problems_response(problems)

    # Tri-state: only thread cluster_group when the body explicitly contains
    # the key. Empty string or null clear it; missing key leaves it untouched.
    update_kwargs: dict = {"name": name, "config_dict": config_dict}
    if "cluster_group" in body:
        update_kwargs["cluster_group"] = (str(body.get("cluster_group") or "").strip() or None)

    await int_svc.update_config(db, config_id, **update_kwargs)
    secrets_changed = [
        f.key for f in fields
        if _is_secret_field(f) and str(body.get(f.key) or "").strip()
    ]
    await _audit(db, request, "integration.update", config_id, name, {
        "type": integration_type,
        **_endpoint_summary(fields, config_dict),
        "endpoint_changed": changed_endpoints or None,
        "tls_verification_disabled": True if tls_disabled else None,
        "secrets_replaced": secrets_changed or None,
    })
    return JSONResponse({"ok": True, "id": config_id, "name": name})


# ── JSON API: delete instance ────────────────────────────────────────────────


@router.delete("/api/integration/{integration_type}/{config_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def api_delete_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """JSON API for deleting an integration instance. Admin only."""
    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"error": "Instance not found"}, status_code=404)
    cfg_name = cfg.name
    await int_svc.delete_config(db, config_id)
    await _audit(db, request, "integration.delete", config_id, cfg_name,
                 {"type": integration_type})
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return JSONResponse({"ok": True})



# ── JSON API: latest status ───────────────────────────────────────────────────


@router.get("/api/integration/{integration_type}/{config_id}/status")
async def api_status(
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"error": "not found"}, status_code=404)

    snap = await snap_svc.get_latest(db, integration_type, config_id)
    if not snap:
        return JSONResponse({"error": "no data yet"}, status_code=404)

    data = json.loads(snap.data_json) if snap.data_json else None
    return JSONResponse({
        "ok": snap.ok,
        "data": data,
        "error": snap.error,
        "timestamp": iso(snap.timestamp),
        # Probe-side integrations: who collected it (NULL = the server).
        "collector": cfg.collector,
        "collected_by": snap.collected_by,
        "collected_at": iso(snap.collected_at),
    })


# ── JSON API: list all integrations ──────────────────────────────────────────


@router.get("/api/integrations")
async def api_list_integrations(db: AsyncSession = Depends(get_db)):
    """Return metadata for all registered integrations with config counts."""
    registry = get_registry()
    from services.integration import count_all_by_type
    counts = await count_all_by_type(db)
    return JSONResponse([
        {
            "name": name,
            "display_name": cls.display_name,
            "icon": cls.icon,
            "icon_svg": cls.icon_svg or "",
            "color": cls.color,
            "description": cls.description,
            "single_instance": cls.single_instance,
            "network_scope": cls.network_scope,
            "configured": counts.get(name, 0),
        }
        for name, cls in sorted(registry.items(), key=lambda x: x[1].display_name)
    ])


# ── Proxmox: deploy the agent into every running container ──────────────────


@router.post("/api/v1/integrations/proxmox/{config_id}/deploy-agent")
@rate_limit(max_requests=10, window_seconds=60)
async def deploy_agent_to_lxcs(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """Install the Nodeglow agent in every running container of a Proxmox
    integration.

    With an SSH key in the integration (and SSH deploy switched on): one SSH
    connection to the node, ``pct exec`` per container, each with its own
    install token; answers 202 with a run to follow under
    ``GET /api/agent-deploy/{run_id}``. An unknown node key is asked about
    first (409 ``host_key_unknown``; repeat with ``host_key_fingerprint``).
    Otherwise: a script for the node's shell, with a fresh install token.
    See docs/specs/2026-11-18-agent-ssh-deploy-design.md.
    """
    import socket
    from urllib.parse import urlparse

    from integrations.proxmox import ProxmoxAPI
    from models.agent_deploy import AgentDeployJob
    from models.integration import IntegrationConfig
    from routers import agent_deploy as deploy_routes
    from routers.agents import (
        _require_access_admin, _username, issue_install_token, resolve_server_url,
    )
    from services import agent_deploy, ssh_deploy, tenancy
    from services.audit import log_action

    # Like issuing install tokens: an admin in a session, never an API key
    # and never through support access (tokens and runs outlive both).
    if err := _require_access_admin(request):
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    cfg = await db.get(IntegrationConfig, config_id)
    if not cfg or cfg.type != "proxmox":
        return JSONResponse({"error": "Proxmox config not found"}, status_code=404)
    if (cfg.collector or "core") != "core":
        # A probe collects it: the backend neither reaches the node nor uses
        # its secrets (probe-side integrations, rule 2). The probe-side
        # deploy is a separate, on-prem-only command (spec phase P5).
        return JSONResponse(
            {"error": "This Proxmox integration is collected by a probe; deploy the agent "
                      "with the manual install command instead."},
            status_code=409,
        )

    config_dict = await int_svc.decrypt_config(db, cfg)
    api = ProxmoxAPI(
        host=config_dict["host"],
        token_id=config_dict["token_id"],
        token_secret=config_dict["token_secret"],
        verify_ssl=config_dict.get("verify_ssl", True),
    )
    resources = await api.cluster_resources()
    # pct exec reaches the containers of the node behind the connection only:
    # the node that answers the API (``local``) at config.host.
    try:
        local_node = next((n.get("name") for n in await api.cluster_status()
                           if n.get("type") == "node" and n.get("local")), None)
    except Exception:  # noqa: BLE001 — unknown: do not filter
        local_node = None

    server_url = await resolve_server_url(request)
    # Leave out the container Nodeglow itself runs in.
    self_names: set[str] = set()
    server_host = urlparse(server_url).hostname or ""
    if server_host:
        self_names.add(server_host.lower().split(".")[0])
        try:
            self_names.add(socket.getfqdn(server_host).lower().split(".")[0])
        except OSError:
            pass
    lxcs, skipped_self, other_nodes = [], None, []
    for r in resources:
        if r.get("type") != "lxc" or r.get("status") != "running":
            continue
        if local_node and r.get("node") and r["node"] != local_node:
            other_nodes.append(f"{r.get('vmid')} {r.get('name') or '?'} ({r['node']})")
            continue
        name = (r.get("name") or "").lower()
        if name and name.split(".")[0] in self_names:
            skipped_self = r.get("name")
            continue
        lxcs.append(r)
    if not lxcs:
        return JSONResponse({"ok": True, "mode": "none", "results": [], "jobs": [],
                             "skipped_other_nodes": other_nodes,
                             "message": "No running containers found on this node",
                             "manual_script": None, "nodeglow_url": server_url,
                             "skipped_self": skipped_self})

    ssh_key = (config_dict.get("ssh_private_key") or "").strip()
    ssh_user = (config_dict.get("ssh_user") or "root").strip() or "root"
    node = config_dict["host"].split("://")[-1].split(":")[0].split("/")[0]
    try:
        ssh_port = int(config_dict.get("ssh_port") or 22)
    except (TypeError, ValueError):
        ssh_port = 22

    if ssh_key and agent_deploy.deploy_enabled():
        pinned = await deploy_routes.pinned_host_key(db, request, node, ssh_port,
                                                     body.get("host_key_fingerprint"))
        if isinstance(pinned, JSONResponse):
            return pinned
        run_id = deploy_routes.new_run_id()
        targets, views = [], []
        for lxc in lxcs:
            vmid = int(lxc["vmid"])
            name = lxc.get("name") or f"ct-{vmid}"
            _row, raw = await issue_install_token(
                db, note=f"proxmox deploy {name}", ttl_hours=1, created_by=_username(request))
            job = AgentDeployJob(run_id=run_id, kind="proxmox_lxc", target=f"{vmid} {name}",
                                 status="queued", created_by=_username(request))
            db.add(job)
            await db.flush()
            targets.append(agent_deploy.RunTarget(job_id=job.id, token=raw, vmid=vmid))
            views.append(agent_deploy.job_view(job))
        await log_action(db, request, "agent.deploy", "integration", cfg.id, cfg.name, {
            "run_id": run_id, "kind": "proxmox_lxc", "target": node, "port": ssh_port,
            "username": ssh_user, "containers": [v["target"] for v in views],
        })
        await db.commit()
        agent_deploy.start(agent_deploy.Run(
            run_id=run_id, tenant_id=tenancy.current_tenant_id(), host=node, port=ssh_port,
            pinned_public_key=pinned,
            creds=ssh_deploy.SshCredentials(username=ssh_user, private_key=ssh_key),
            server_url=server_url, targets=targets,
        ))
        return JSONResponse({"ok": True, "mode": "ssh", "run_id": run_id, "jobs": views,
                             "nodeglow_url": server_url, "skipped_self": skipped_self,
                             "skipped_other_nodes": other_nodes},
                            status_code=202)

    # No key (or SSH deploy switched off): a script for the node's shell. One
    # install token for all of them, valid for two hours.
    _row, raw = await issue_install_token(
        db, note=f"proxmox script {cfg.name}"[:256], ttl_hours=2, created_by=_username(request))
    await log_action(db, request, "install_token.create", "install_token", _row.id, _row.prefix,
                     {"ttl_hours": 2, "proxmox_config": cfg.id})
    await db.commit()
    vmids = " ".join(str(int(lxc["vmid"])) for lxc in lxcs)
    manual_script = (
        "# Run on the Proxmox node's shell: installs the Nodeglow agent\n"
        "# in every running container. The token is valid for two hours.\n"
        f"URL='{server_url}/install/linux?token={raw}'\n"
        "\n"
        f"for VMID in {vmids}; do\n"
        '  echo "=== CT $VMID ==="\n'
        '  pct exec "$VMID" -- bash -c "curl -sSL \'$URL\' | bash"\n'
        "done\n"
    )
    return JSONResponse({
        "ok": True, "mode": "script", "deployed": 0, "failed": 0,
        "results": [{"vmid": lxc.get("vmid"), "name": lxc.get("name")} for lxc in lxcs],
        "manual_script": manual_script, "nodeglow_url": server_url,
        "skipped_self": skipped_self, "skipped_other_nodes": other_nodes,
    })

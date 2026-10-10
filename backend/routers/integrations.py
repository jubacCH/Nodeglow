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

from sqlalchemy import select as sa_select

from integrations import get_registry, get_integration
from integrations._base import BaseIntegration
from models.api_key import ApiKey
from models.base import get_db
from ratelimit import rate_limit
from routers.api_v1 import require_admin
from services import integration as int_svc
from services import snapshot as snap_svc


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

    existing_config = int_svc.decrypt_config(cfg.config_json)
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
    if changed_endpoints:
        missing = [
            f.key for f in fields
            if _is_secret_field(f)
            and existing_config.get(f.key)          # a secret is stored …
            and not str(body.get(f.key) or "").strip()  # … and not re-supplied
        ]
        if missing:
            labels = [f.label for f in fields if f.key in missing]
            return JSONResponse({
                "error": (
                    "Changing the address of an integration requires entering "
                    "its credentials again: " + ", ".join(labels)
                ),
                "code": "secrets_required",
                "missing_fields": missing,
            }, status_code=400)

    host_err = await _validate_config_hosts_async(config_dict, fields)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)

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
        "timestamp": snap.timestamp.isoformat() if snap.timestamp else None,
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
            "configured": counts.get(name, 0),
        }
        for name, cls in sorted(registry.items(), key=lambda x: x[1].display_name)
    ])


# ── Proxmox: Deploy Syslog Config ───────────────────────────────────────────


@router.post("/api/v1/integrations/proxmox/{config_id}/deploy-agent")
async def deploy_agent_to_lxcs(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """Deploy Nodeglow agent to all running LXCs via SSH → pct exec.

    The agent auto-enrolls, collects system metrics, system logs (journalctl),
    and Docker container logs — then reports everything to Nodeglow.
    """
    import asyncio
    import socket
    import tempfile
    import os
    from database import get_setting
    from integrations.proxmox import ProxmoxAPI
    from models.integration import IntegrationConfig
    import services.integration as int_svc

    cfg = await db.get(IntegrationConfig, config_id)
    if not cfg or cfg.type != "proxmox":
        return JSONResponse({"error": "Proxmox config not found"}, status_code=404)

    config_dict = int_svc.decrypt_config(cfg.config_json)

    api = ProxmoxAPI(
        host=config_dict["host"],
        token_id=config_dict["token_id"],
        token_secret=config_dict["token_secret"],
        verify_ssl=config_dict.get("verify_ssl", True),
    )

    resources = await api.cluster_resources()

    # Detect Nodeglow's LXC IP — find the PingHost for this Nodeglow instance
    # (Docker-internal IPs like 172.18.x.x are NOT reachable from LXCs)
    from models.ping import PingHost
    nodeglow_ip = await get_setting(db, "nodeglow_ip", "")
    if not nodeglow_ip:
        # Find by source_detail matching this Proxmox config, hostname containing "monitoring" or "nodeglow"
        ph_result = await db.execute(
            sa_select(PingHost).where(
                PingHost.source == "proxmox",
                PingHost.hostname.ilike("%monitoring%"),
            )
        )
        ph = ph_result.scalar_one_or_none()
        if ph:
            # Resolve hostname to IP
            try:
                nodeglow_ip = socket.gethostbyname(ph.hostname)
            except Exception:
                nodeglow_ip = ph.hostname
        if not nodeglow_ip:
            nodeglow_ip = "10.10.30.52"

    nodeglow_url = f"http://{nodeglow_ip}:8000"

    # Exclude the Nodeglow LXC itself
    _self_names = set()
    try:
        resolved = socket.getfqdn(nodeglow_ip)
        _self_names.add(resolved.lower())
        try:
            rev = socket.gethostbyaddr(nodeglow_ip)[0]
            _self_names.add(rev.lower())
        except Exception:
            pass
    except Exception:
        pass

    lxcs = []
    skipped_self = None
    for r in resources:
        if r.get("type") != "lxc" or r.get("status") != "running":
            continue
        name = (r.get("name") or "").lower()
        if name and name in _self_names:
            skipped_self = r.get("name", f"ct-{r.get('vmid')}")
            continue
        lxcs.append(r)

    if not lxcs:
        return JSONResponse({"ok": True, "results": [], "deployed": 0, "failed": 0,
                             "message": "No running LXCs found",
                             "manual_script": None, "nodeglow_url": nodeglow_url})

    # Install command: download + run install script from Nodeglow
    install_cmd = f"curl -sSL {nodeglow_url}/install/linux 2>/dev/null | bash"

    ssh_key = config_dict.get("ssh_private_key", "").strip()
    ssh_user = config_dict.get("ssh_user", "root").strip() or "root"
    proxmox_host = config_dict["host"].split("://")[-1].split(":")[0].split("/")[0]

    # ── SSH deploy (automatic) ───────────────────────────────────────────
    if ssh_key:
        results = []
        deployed = 0
        failed = 0

        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
        key_file.write(ssh_key if ssh_key.endswith("\n") else ssh_key + "\n")
        key_file.close()
        os.chmod(key_file.name, 0o600)

        try:
            for lxc in lxcs:
                vmid = lxc.get("vmid")
                name = lxc.get("name", f"ct-{vmid}")
                cmd = f'pct exec {vmid} -- bash -c "{install_cmd}"'

                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ssh", "-i", key_file.name,
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=10",
                        "-o", "IdentitiesOnly=yes",
                        f"{ssh_user}@{proxmox_host}",
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                    output = stdout.decode().strip()
                    if proc.returncode == 0:
                        results.append({"vmid": vmid, "name": name, "status": "ok",
                                        "detail": output[-200:] if len(output) > 200 else output})
                        deployed += 1
                    else:
                        err = stderr.decode().strip() or output or f"exit code {proc.returncode}"
                        results.append({"vmid": vmid, "name": name, "status": "failed",
                                        "error": err[-200:]})
                        failed += 1
                except asyncio.TimeoutError:
                    results.append({"vmid": vmid, "name": name, "status": "failed",
                                    "error": "Timeout (60s)"})
                    failed += 1
                except Exception as exc:
                    results.append({"vmid": vmid, "name": name, "status": "failed",
                                    "error": str(exc)})
                    failed += 1
        finally:
            os.unlink(key_file.name)

        return JSONResponse({
            "ok": True,
            "mode": "ssh",
            "deployed": deployed,
            "failed": failed,
            "results": results,
            "manual_script": None,
            "nodeglow_url": nodeglow_url,
            "skipped_self": skipped_self,
        })

    # ── No SSH → generate script ─────────────────────────────────────────
    vmids = " ".join(str(lxc.get("vmid")) for lxc in lxcs)
    results = [
        {"vmid": lxc.get("vmid"), "name": lxc.get("name", f"ct-{lxc.get('vmid')}")}
        for lxc in lxcs
    ]

    manual_script = (
        f'# Run on your Proxmox node shell\n'
        f'# Installs Nodeglow agent on all running LXCs\n'
        f'NODEGLOW="{nodeglow_url}"\n'
        f'\n'
        f'for VMID in {vmids}; do\n'
        f'  echo "=== Installing agent on CT $VMID ==="\n'
        f'  pct exec $VMID -- bash -c "curl -sSL $NODEGLOW/install/linux | bash"\n'
        f'  echo "  Done"\n'
        f'done\n'
        f'echo "\\nAll done! Agents will auto-enroll and appear in Nodeglow within 30 seconds."'
    )

    return JSONResponse({
        "ok": True,
        "mode": "script",
        "deployed": 0,
        "failed": 0,
        "results": results,
        "manual_script": manual_script,
        "nodeglow_url": nodeglow_url,
        "skipped_self": skipped_self,
    })

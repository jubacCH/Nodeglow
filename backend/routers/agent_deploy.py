"""Agent deploy over SSH: start a run, follow it, manage pinned host keys.

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``. Every route is for
admins, never through support access, and the deploy routes are behind the
``NODEGLOW_SSH_DEPLOY`` gate.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from models import base as models_base
from models.agent import Agent
from models.agent_deploy import AgentDeployJob, SshHostKey
from models.credential import Credential
from ratelimit import rate_limit
from services import agent_deploy, ssh_deploy, tenancy
from services.audit import log_action
from services.host_state import iso

router = APIRouter()

# A host name or address; no whitespace, no leading dash (never an option).
_HOST = re.compile(r"^[A-Za-z0-9_.:\[\]%-]{1,255}$")


def _refuse(error: str, status: int, code: str | None = None, **extra) -> JSONResponse:
    body = {"error": error, **extra}
    if code:
        body["code"] = code
    return JSONResponse(body, status_code=status)


def guard(request: Request) -> JSONResponse | None:
    """Admin, not through support access, gate open."""
    from routers.agents import _require_access_admin

    if err := _require_access_admin(request):
        return err
    if not agent_deploy.deploy_enabled():
        return _refuse("Deploying agents over SSH is switched off on this server.", 403,
                       "ssh_deploy_disabled")
    return None


def parse_address(body: dict) -> tuple[str, int] | JSONResponse:
    host = str(body.get("host") or "").strip()
    if not _HOST.match(host) or host.startswith("-"):
        return _refuse("Enter a host name or IP address.", 400, "invalid_host")
    try:
        raw_port = body.get("port")
        port = 22 if raw_port in (None, "") else int(raw_port)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        return _refuse("The port must be between 1 and 65535.", 400, "invalid_port")
    return host, port


async def parse_credentials(db, body: dict) -> ssh_deploy.SshCredentials | JSONResponse:
    """Credentials from the store (``credential_id``) or from the request."""
    from services.snmp import decrypt_credential

    fields = body
    if body.get("credential_id") not in (None, ""):
        try:
            cred = await db.get(Credential, int(body["credential_id"]))
        except (TypeError, ValueError):
            cred = None
        if cred is None:
            return _refuse("Credential not found", 404)
        if cred.type != "ssh":
            return _refuse("That credential is not an SSH credential.", 400, "not_ssh")
        fields = await decrypt_credential(db, cred)
    username = str(fields.get("username") or "").strip()
    password = fields.get("password") or None
    private_key = (fields.get("private_key") or "").strip() or None
    if not username:
        return _refuse("A user name is required.", 400, "missing_username")
    if not password and not private_key:
        return _refuse("A password or a private key is required.", 400, "missing_secret")
    return ssh_deploy.SshCredentials(
        username=username, password=password, private_key=private_key,
        passphrase=fields.get("passphrase") or None,
        sudo_password=body.get("sudo_password") or None,
    )


async def pinned_host_key(db, request: Request, host: str, port: int,
                          confirmed: str | None) -> str | JSONResponse:
    """The public key to trust for ``host:port``. Unknown hosts are asked
    about first (409 with the fingerprint); a confirmed fingerprint that
    matches what the host presents now is stored and trusted."""
    stored = (await db.execute(select(SshHostKey).where(
        SshHostKey.host == host, SshHostKey.port == port,
        SshHostKey.probe_id.is_(None)))).scalar_one_or_none()
    if stored is not None:
        return stored.public_key
    try:
        key = await ssh_deploy.fetch_host_key(host, port)
    except ssh_deploy.SshDeployError as exc:
        return _refuse(str(exc), 502, "unreachable")
    if not confirmed or confirmed.strip() != key.fingerprint:
        return _refuse(f"{host}:{port} is not known yet. Check its host key fingerprint before "
                       "trusting it.", 409, "host_key_unknown", host=host, port=port,
                       fingerprint=key.fingerprint, key_type=key.key_type)
    user = getattr(request.state, "current_user", None)
    row = SshHostKey(host=host, port=port, key_type=key.key_type, public_key=key.public_key,
                     fingerprint=key.fingerprint, created_by=user.username if user else None)
    db.add(row)
    await db.flush()
    await log_action(db, request, "ssh_host_key.trust", "ssh_host_key", row.id,
                     f"{host}:{port}", {"fingerprint": key.fingerprint})
    return key.public_key


def new_run_id() -> str:
    return uuid.uuid4().hex


@router.post("/api/agent-deploy")
@rate_limit(max_requests=10, window_seconds=60)
async def start_deploy(request: Request):
    """Install (or, with ``agent_id``, reinstall) the agent on one Linux host
    over SSH. 202 with the run id; follow it with GET /api/agent-deploy/{id}."""
    from routers.agents import _username, issue_install_token, resolve_server_url

    if err := guard(request):
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _refuse("Invalid request", 400)
    address = parse_address(body)
    if isinstance(address, JSONResponse):
        return address
    host, port = address

    async with models_base.AsyncSessionLocal() as db:
        creds = await parse_credentials(db, body)
        if isinstance(creds, JSONResponse):
            return creds
        agent = None
        if body.get("agent_id") not in (None, ""):
            try:
                agent = await db.get(Agent, int(body["agent_id"]))
            except (TypeError, ValueError):
                agent = None
            if agent is None:
                return _refuse("Agent not found", 404)
            if not agent.enabled:
                return _refuse("The agent is disabled. Enable it before reinstalling.", 409,
                               "agent_disabled")
        pinned = await pinned_host_key(db, request, host, port, body.get("host_key_fingerprint"))
        if isinstance(pinned, JSONResponse):
            return pinned

        run_id = new_run_id()
        token_row, raw = await issue_install_token(
            db, note=(f"reinstall {agent.name}" if agent else f"ssh deploy {host}"),
            ttl_hours=1, created_by=_username(request),
            site_id=agent.site_id if agent else None, agent_id=agent.id if agent else None)
        job = AgentDeployJob(run_id=run_id, kind="ssh", target=host,
                             agent_id=agent.id if agent else None, status="queued",
                             created_by=_username(request))
        db.add(job)
        await db.flush()
        await log_action(db, request, "agent.deploy", "agent_deploy", job.id, host, {
            "run_id": run_id, "kind": "ssh", "target": host, "port": port,
            "username": creds.username, "credential_id": body.get("credential_id") or None,
            "agent_id": agent.id if agent else None, "token_prefix": token_row.prefix,
        })
        await db.commit()
        view = agent_deploy.job_view(job)

    agent_deploy.start(agent_deploy.Run(
        run_id=run_id, tenant_id=tenancy.current_tenant_id(), host=host, port=port, pinned_public_key=pinned,
        creds=creds, server_url=await resolve_server_url(request),
        targets=[agent_deploy.RunTarget(job_id=view["id"], token=raw)],
    ))
    return JSONResponse({"ok": True, "run_id": run_id, "jobs": [view]}, status_code=202)


@router.get("/api/agent-deploy/{run_id}")
async def get_deploy(run_id: str, request: Request):
    from routers.agents import _require_access_admin

    if err := _require_access_admin(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        jobs = (await db.execute(select(AgentDeployJob).where(
            AgentDeployJob.run_id == run_id).order_by(AgentDeployJob.id))).scalars().all()
    if not jobs:
        return _refuse("Deploy run not found", 404)
    now = datetime.utcnow()
    live = agent_deploy.is_live(run_id)
    views = [agent_deploy.job_view(j, now, live=live) for j in jobs]
    return {"run_id": run_id, "done": all(v["status"] not in agent_deploy.ACTIVE for v in views),
            "jobs": views}


@router.get("/api/ssh-host-keys")
async def list_host_keys(request: Request):
    from routers.agents import _require_access_admin

    if err := _require_access_admin(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        rows = (await db.execute(select(SshHostKey).order_by(
            SshHostKey.host, SshHostKey.port))).scalars().all()
    return {"keys": [{"id": r.id, "host": r.host, "port": r.port, "probe_id": r.probe_id,
                      "key_type": r.key_type,
                      "fingerprint": r.fingerprint, "created_at": iso(r.created_at),
                      "created_by": r.created_by} for r in rows]}


@router.delete("/api/ssh-host-keys/{host_key_id}")
async def delete_host_key(host_key_id: int, request: Request):
    """Forget a host key: the next deploy to that address asks again."""
    from routers.agents import _require_access_admin

    if err := _require_access_admin(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        row = await db.get(SshHostKey, host_key_id)
        if row is None:
            return _refuse("Host key not found", 404)
        await log_action(db, request, "ssh_host_key.delete", "ssh_host_key", row.id,
                         f"{row.host}:{row.port}", {"fingerprint": row.fingerprint})
        await db.delete(row)
        await db.commit()
    return {"ok": True}

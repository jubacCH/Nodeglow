"""Deploy agents through a probe (part C).

See ``docs/specs/2026-11-19-probe-agent-deploy-design.md``. The server never
sees an SSH secret here: it names one of the credentials the probe reported,
and the probe does the rest with its own release-verified binary. The routes
queue commands (``services.agent_commands``) and create the jobs that
``GET /api/agent-deploy/{run_id}`` follows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from models import base as models_base
from models.agent import Agent
from models.agent_deploy import AgentCommand, AgentDeployJob, SshHostKey
from ratelimit import rate_limit
from routers.agent_deploy import _HOST, _refuse, new_run_id
from services import agent_commands
from services.audit import log_action

router = APIRouter()

MAX_CONTAINERS = 50
# How old a host-key answer may be to confirm a deploy with it.
HOST_KEY_ANSWER_TTL = timedelta(hours=1)


def _guard(request: Request) -> JSONResponse | None:
    """Admin in a session, not through support access. Not behind
    NODEGLOW_SSH_DEPLOY: that gate is about the server opening connections."""
    from routers.agents import _require_access_admin

    return _require_access_admin(request)


async def _load_probe(db, probe_id: int) -> Agent | JSONResponse:
    probe = await db.get(Agent, probe_id)
    if probe is None:
        return _refuse("Probe not found", 404)
    if not probe.is_probe:
        return _refuse("This agent is not a probe.", 409, "not_a_probe")
    if not agent_commands.capability(probe).get("enabled"):
        return _refuse("Deploying agents is not switched on on this probe (deploy.json on the "
                       "probe).", 409, "deploy_not_enabled")
    return probe


def _address(raw: dict) -> tuple[str, int] | JSONResponse:
    host = str(raw.get("host") or "").strip()
    if not _HOST.match(host) or host.startswith("-"):
        return _refuse("Enter a host name or IP address.", 400, "invalid_host")
    try:
        port = 22 if raw.get("port") in (None, "") else int(raw["port"])
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        return _refuse("The port must be between 1 and 65535.", 400, "invalid_port")
    return host, port


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/api/probes/{probe_id}/host-key")
@rate_limit(max_requests=20, window_seconds=60)
async def ask_host_key(probe_id: int, request: Request):
    """Ask the probe which host key ``host:port`` presents (no login)."""
    from routers.agents import _username

    if err := _guard(request):
        return err
    body = await _body(request)
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        address = _address(body)
        if isinstance(address, JSONResponse):
            return address
        host, port = address
        cmd = await agent_commands.enqueue(db, probe, "ssh_host_key", {"host": host, "port": port},
                                           created_by=_username(request))
        await db.commit()
        return JSONResponse({"ok": True, "command_id": cmd.id}, status_code=202)


@router.get("/api/probes/{probe_id}/commands/{command_id}")
async def get_command(probe_id: int, command_id: int, request: Request):
    if err := _guard(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        cmd = await db.get(AgentCommand, command_id)
    if cmd is None or cmd.agent_id != probe_id:
        return _refuse("Command not found", 404)
    try:
        result = json.loads(cmd.result) if cmd.result else None
    except ValueError:
        result = None
    return {"id": cmd.id, "op": cmd.op, "status": agent_commands.effective_status(cmd),
            "result": result}


async def _pinned(db, request, probe: Agent, host: str, port: int,
                  confirmed: str | None) -> str | JSONResponse:
    """The fingerprint the probe must accept for ``host:port``: a stored one,
    or a confirmed one that matches what the probe reported recently."""
    stored = (await db.execute(select(SshHostKey).where(
        SshHostKey.probe_id == probe.id, SshHostKey.host == host, SshHostKey.port == port,
    ))).scalar_one_or_none()
    if stored is not None:
        return stored.fingerprint
    unknown = _refuse(f"{host}:{port} is not known to this probe yet. Ask the probe for its host "
                      "key and confirm the fingerprint.", 409, "host_key_unknown",
                      host=host, port=port)
    if not confirmed:
        return unknown
    answers = (await db.execute(select(AgentCommand).where(
        AgentCommand.agent_id == probe.id, AgentCommand.op == "ssh_host_key",
        AgentCommand.status == "ok",
        AgentCommand.created_at >= datetime.utcnow() - HOST_KEY_ANSWER_TTL,
    ).order_by(AgentCommand.id.desc()))).scalars().all()
    for answer in answers:
        try:
            seen = json.loads(answer.result or "{}")
        except ValueError:
            continue
        if (seen.get("host"), seen.get("port"), seen.get("fingerprint")) == (host, port, confirmed):
            user = getattr(request.state, "current_user", None)
            row = SshHostKey(host=host, port=port, probe_id=probe.id,
                             key_type=str(seen.get("key_type") or "")[:64],
                             public_key=str(seen.get("public_key") or ""),
                             fingerprint=confirmed, created_by=user.username if user else None)
            db.add(row)
            await db.flush()
            await log_action(db, request, "ssh_host_key.trust", "ssh_host_key", row.id,
                             f"{host}:{port}", {"fingerprint": confirmed, "probe_id": probe.id})
            return confirmed
    return unknown


def _targets(raw) -> list[dict] | JSONResponse:
    """The command targets: one host, or one entry per container."""
    if not isinstance(raw, dict) or raw.get("kind") not in ("host", "lxc"):
        return _refuse("The target must be a host or containers on a Proxmox node.", 400,
                       "invalid_target")
    address = _address(raw)
    if isinstance(address, JSONResponse):
        return address
    host, port = address
    if raw["kind"] == "host":
        return [{"kind": "host", "host": host, "port": port}]
    vmids: list[int] = []
    for v in raw.get("vmids") or []:
        if isinstance(v, bool) or not str(v).isdigit() or not 1 <= int(v) <= 999_999_999:
            return _refuse("Container ids are numbers.", 400, "invalid_target")
        if int(v) not in vmids:
            vmids.append(int(v))
    if not 1 <= len(vmids) <= MAX_CONTAINERS:
        return _refuse(f"Pick between 1 and {MAX_CONTAINERS} containers.", 400, "invalid_target")
    return [{"kind": "lxc", "host": host, "port": port, "vmid": v} for v in vmids]


@router.post("/api/probes/{probe_id}/deploy")
@rate_limit(max_requests=10, window_seconds=60)
async def deploy_through_probe(probe_id: int, request: Request):
    """Install (or, with ``agent_id``, reinstall) the agent on a host or in
    containers the probe can reach. 202 with the run id."""
    from routers.agents import _username, issue_install_token

    if err := _guard(request):
        return err
    body = await _body(request)
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        targets = _targets(body.get("target"))
        if isinstance(targets, JSONResponse):
            return targets
        credential = str(body.get("credential") or "")
        if credential not in agent_commands.capability(probe).get("credentials", []):
            return _refuse("The probe has no credential with that name.", 400, "unknown_credential")
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
            if len(targets) != 1:
                return _refuse("A reinstall goes to one host or one container.", 400,
                               "one_target_for_reinstall")
        host, port = targets[0]["host"], targets[0]["port"]
        fingerprint = await _pinned(db, request, probe, host, port, body.get("host_key_fingerprint"))
        if isinstance(fingerprint, JSONResponse):
            return fingerprint

        run_id, views, user = new_run_id(), [], _username(request)
        from services.agent_deploy import job_view
        for target in targets:
            label = host if target["kind"] == "host" else f"{target['vmid']} on {host}"
            _row, raw = await issue_install_token(
                db, note=(f"reinstall {agent.name}" if agent else f"probe deploy {label}"),
                ttl_hours=1, created_by=user, site_id=agent.site_id if agent else probe.site_id,
                agent_id=agent.id if agent else None)
            cmd = await agent_commands.enqueue(db, probe, "deploy_agent", {
                "target": target, "credential": credential, "host_key_fingerprint": fingerprint,
                # No server URL: the probe binds the new agent to its own
                # server, so a command cannot point an agent elsewhere.
                "enrollment_token": raw,
            }, created_by=user)
            job = AgentDeployJob(run_id=run_id, kind=f"probe_{target['kind']}", target=label,
                                 agent_id=agent.id if agent else None, status="queued",
                                 created_by=user, probe_id=probe.id, command_id=cmd.id)
            db.add(job)
            await db.flush()
            views.append(job_view(job))
        await log_action(db, request, "agent.deploy", "agent", probe.id, probe.name, {
            "run_id": run_id, "kind": f"probe_{targets[0]['kind']}", "probe_id": probe.id,
            "target": host, "port": port, "credential": credential,
            "containers": [t["vmid"] for t in targets if "vmid" in t] or None,
            "agent_id": agent.id if agent else None,
        })
        await db.commit()
    return JSONResponse({"ok": True, "run_id": run_id, "jobs": views}, status_code=202)

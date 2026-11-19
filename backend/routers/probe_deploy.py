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
from models.probe_credential import ProbeCredential
from ratelimit import rate_limit
from routers.agent_deploy import _HOST, _refuse, new_run_id
from services import agent_commands, seal_keys, tenant_keys
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


async def _sealed(db, probe: Agent, raw) -> tuple[ProbeCredential, dict] | JSONResponse:
    """The sealed login ``raw`` names on this probe and its envelope."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return _refuse("The sealed login must be given by its id.", 400, "invalid_credential")
    if not agent_commands.capability(probe).get("sealed"):
        return _refuse("Sealed logins are not switched on on this probe.", 409,
                       "sealed_not_enabled")
    row = (await db.execute(select(ProbeCredential).where(
        ProbeCredential.id == raw, ProbeCredential.probe_id == probe.id))).scalar_one_or_none()
    if row is None:
        return _refuse("The probe has no sealed login with that id.", 400, "unknown_credential")
    if row.key_fingerprint != seal_keys.fingerprint(probe.seal_public_key):
        return _refuse("The probe's sealing key changed. Create the login again.", 409,
                       "credential_stale")
    try:
        envelope = json.loads(await tenant_keys.decrypt_secret(db, row.sealed,
                                                               tenant_id=row.tenant_id))
    except ValueError:
        return _refuse("The sealed login cannot be read. Create it again.", 409,
                       "credential_stale")
    return row, envelope


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
        payload: dict = {"host": host, "port": port}
        credential_id = None
        if body.get("sealed_credential_id") is not None:
            sealed = await _sealed(db, probe, body["sealed_credential_id"])
            if isinstance(sealed, JSONResponse):
                return sealed
            credential_id, payload["sealed_credential"] = sealed[0].id, sealed[1]
        cmd = await agent_commands.enqueue(db, probe, "ssh_host_key", payload,
                                           created_by=_username(request), target=f"{host}:{port}",
                                           credential_id=credential_id)
        await log_action(db, request, "agent.host_key_query", "agent", probe.id, probe.name,
                         {"target": f"{host}:{port}", "command_id": cmd.id})
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
    # Answers to what the server asked about this address (``target`` is the
    # server's, not the probe's), whose key really hashes to the fingerprint.
    answers = (await db.execute(select(AgentCommand).where(
        AgentCommand.agent_id == probe.id, AgentCommand.op == "ssh_host_key",
        AgentCommand.status == "ok", AgentCommand.target == f"{host}:{port}",
        AgentCommand.created_at >= datetime.utcnow() - HOST_KEY_ANSWER_TTL,
    ).order_by(AgentCommand.id.desc()))).scalars().all()
    for answer in answers:
        try:
            seen = json.loads(answer.result or "{}")
        except ValueError:
            continue
        public_key = str(seen.get("public_key") or "")
        if seen.get("fingerprint") != confirmed or openssh_fingerprint(public_key) != confirmed:
            continue
        from sqlalchemy.exc import IntegrityError

        user = getattr(request.state, "current_user", None)
        try:
            async with db.begin_nested():
                row = SshHostKey(host=host, port=port, probe_id=probe.id,
                                 key_type=str(seen.get("key_type") or "")[:64],
                                 public_key=public_key, fingerprint=confirmed,
                                 created_by=user.username if user else None)
                db.add(row)
                await db.flush()
        except IntegrityError:
            # Another request pinned it a moment ago: use what is stored.
            stored = (await db.execute(select(SshHostKey).where(
                SshHostKey.probe_id == probe.id, SshHostKey.host == host,
                SshHostKey.port == port))).scalar_one()
            return stored.fingerprint
        await log_action(db, request, "ssh_host_key.trust", "ssh_host_key", row.id,
                         f"{host}:{port}", {"fingerprint": confirmed, "probe_id": probe.id})
        return confirmed
    return unknown


def openssh_fingerprint(public_key: str) -> str | None:
    """SHA256 fingerprint of an OpenSSH public key line, as ssh-keygen -l."""
    import base64
    import hashlib

    parts = public_key.split()
    if len(parts) < 2:
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


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
        sealed_id = body.get("sealed_credential_id")
        if bool(credential) == (sealed_id is not None):
            return _refuse("Give either a probe login or a sealed login.", 400,
                           "invalid_credential")
        sealed_row, envelope = None, None
        if sealed_id is not None:
            sealed = await _sealed(db, probe, sealed_id)
            if isinstance(sealed, JSONResponse):
                return sealed
            sealed_row, envelope = sealed
        elif credential not in agent_commands.capability(probe).get("credentials", []):
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
            payload = {
                "target": target, "host_key_fingerprint": fingerprint,
                # No server URL: the probe binds the new agent to its own
                # server, so a command cannot point an agent elsewhere.
                "enrollment_token": raw,
            }
            if envelope is not None:
                payload["sealed_credential"] = envelope
            else:
                payload["credential"] = credential
            cmd = await agent_commands.enqueue(db, probe, "deploy_agent", payload, created_by=user,
                                               credential_id=sealed_row.id if sealed_row else None)
            job = AgentDeployJob(run_id=run_id, kind=f"probe_{target['kind']}", target=label,
                                 agent_id=agent.id if agent else None, status="queued",
                                 created_by=user, probe_id=probe.id, command_id=cmd.id)
            db.add(job)
            await db.flush()
            views.append(job_view(job))
        if sealed_row is not None:
            sealed_row.last_used_at = datetime.utcnow()
        await log_action(db, request, "agent.deploy", "agent", probe.id, probe.name, {
            "run_id": run_id, "kind": f"probe_{targets[0]['kind']}", "probe_id": probe.id,
            "target": host, "port": port,
            **({"sealed_credential": {"id": sealed_row.id, "name": sealed_row.name}}
               if sealed_row else {"credential": credential}),
            "containers": [t["vmid"] for t in targets if "vmid" in t] or None,
            "agent_id": agent.id if agent else None,
        })
        await db.commit()
    return JSONResponse({"ok": True, "run_id": run_id, "jobs": views}, status_code=202)

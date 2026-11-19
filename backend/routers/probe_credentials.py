"""Sealed deploy logins and the sealing key of a probe (part C).

See ``docs/specs/2026-11-19-probe-sealed-credentials-design.md``. The browser
encrypts a secret to the probe's public key; the server stores that envelope
(under the tenant key on top) and never returns it or sees the plaintext.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models import base as models_base
from models.agent import Agent
from models.probe_credential import ProbeCredential
from ratelimit import rate_limit
from routers.agent_deploy import _refuse
from routers.probe_deploy import _body, _guard, _load_probe
from services import agent_commands, seal_keys, tenant_keys
from services.audit import log_action

router = APIRouter()

_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
DELETED_DETAIL = "The login was deleted before the probe picked up the command."


def _view(row: ProbeCredential, probe: Agent) -> dict:
    try:
        hosts = json.loads(row.hosts)
    except ValueError:
        hosts = []
    return {
        "id": row.id, "name": row.name, "username": row.username, "auth": row.auth,
        "hosts": hosts, "key_fingerprint": row.key_fingerprint,
        "stale": row.key_fingerprint != seal_keys.fingerprint(probe.seal_public_key or ""),
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "created_by": row.created_by,
        "last_used_at": row.last_used_at.isoformat() + "Z" if row.last_used_at else None,
    }


def _hosts(raw) -> list[str] | None:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
        return None
    out = []
    for h in raw:
        if not isinstance(h, str) or not h.strip() or len(h.strip()) > 255:
            return None
        out.append(h.strip())
    return out


@router.get("/api/probes/{probe_id}/credentials")
async def list_credentials(probe_id: int, request: Request):
    if err := _guard(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        rows = (await db.execute(select(ProbeCredential).where(
            ProbeCredential.probe_id == probe.id).order_by(ProbeCredential.id))).scalars().all()
        return {"credentials": [_view(r, probe) for r in rows]}


@router.post("/api/probes/{probe_id}/credentials")
@rate_limit(max_requests=10, window_seconds=60)
async def create_credential(probe_id: int, request: Request):
    from routers.agents import _username

    if err := _guard(request):
        return err
    body = await _body(request)
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        cap = agent_commands.capability(probe)
        if not cap["sealed"]:
            return _refuse("This probe does not support sealed logins (update its agent).", 409,
                           "sealed_not_enabled")
        key_state = seal_keys.state(probe)
        if key_state["state"] == "none":
            return _refuse("The probe has not reported a sealing key yet.", 409, "seal_key_missing")
        if key_state["state"] == "changed":
            return _refuse("The probe reported a different sealing key. Trust it first.", 409,
                           "seal_key_changed")

        name = body.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            return _refuse("The name may use letters, digits and _ . @ - (up to 64).", 400,
                           "invalid_name")
        username = body.get("username")
        if not isinstance(username, str) or not username.strip() or len(username) > 255:
            return _refuse("Enter a user name.", 400, "invalid_username")
        if body.get("auth") not in ("password", "key"):
            return _refuse("The login uses a password or a key.", 400, "invalid_auth")
        hosts = _hosts(body.get("hosts"))
        if hosts is None:
            return _refuse("Give between 1 and 64 hosts, each up to 255 characters.", 400,
                           "invalid_hosts")
        envelope = agent_commands.validate_envelope(body.get("sealed"))
        if envelope is None:
            return _refuse("The sealed login is malformed.", 400, "invalid_envelope")
        if envelope["key"] != key_state["fingerprint"]:
            return _refuse("The login was sealed for another key than the one trusted.", 409,
                           "wrong_key")
        taken = name in cap["credentials"] or (await db.execute(select(ProbeCredential.id).where(
            ProbeCredential.probe_id == probe.id, ProbeCredential.name == name,
        ))).first() is not None
        if taken:
            return _refuse("A login with that name exists already.", 409, "name_taken")

        row = ProbeCredential(
            probe_id=probe.id, name=name, username=username.strip(), auth=body["auth"],
            hosts=json.dumps(hosts), key_fingerprint=envelope["key"],
            sealed=await tenant_keys.encrypt_secret(db, json.dumps(envelope),
                                                    tenant_id=probe.tenant_id),
            created_at=datetime.utcnow(), created_by=_username(request))
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            return _refuse("A login with that name exists already.", 409, "name_taken")
        await log_action(db, request, "probe.credential.create", "agent", probe.id, probe.name,
                         {"credential": name, "credential_id": row.id, "auth": row.auth})
        await db.commit()
        return JSONResponse({"credential": _view(row, probe)}, status_code=201)


@router.delete("/api/probes/{probe_id}/credentials/{credential_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def delete_credential(probe_id: int, credential_id: int, request: Request):
    if err := _guard(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        row = await db.get(ProbeCredential, credential_id)
        if row is None or row.probe_id != probe_id:
            return _refuse("Login not found", 404)
        expired = await agent_commands.expire_for_credential(db, row.id, probe_id, DELETED_DETAIL)
        probe = await db.get(Agent, probe_id)
        await log_action(db, request, "probe.credential.delete", "agent", probe_id,
                         probe.name if probe else None,
                         {"credential": row.name, "credential_id": row.id,
                          "expired_commands": len(expired)})
        await db.delete(row)
        await db.commit()
        return {"ok": True}


@router.post("/api/probes/{probe_id}/seal-key/trust")
@rate_limit(max_requests=10, window_seconds=60)
async def trust_seal_key(probe_id: int, request: Request):
    from routers.agents import _username

    if err := _guard(request):
        return err
    body = await _body(request)
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        reported = probe.seal_key_reported
        fp = seal_keys.fingerprint(reported) if reported else None
        if fp is None or body.get("fingerprint") != fp:
            return _refuse("The fingerprint is not the one the probe reports.", 409,
                           "fingerprint_mismatch")
        seal_keys.trust(probe, reported, _username(request))
        await log_action(db, request, "probe.seal_key.trust", "agent", probe.id, probe.name,
                         {"fingerprint": fp})
        await db.commit()
        return {"ok": True, **seal_keys.state(probe)}


@router.post("/api/probes/{probe_id}/seal-key/rotate")
@rate_limit(max_requests=5, window_seconds=60)
async def rotate_seal_key(probe_id: int, request: Request):
    from routers.agents import _username

    if err := _guard(request):
        return err
    async with models_base.AsyncSessionLocal() as db:
        probe = await _load_probe(db, probe_id)
        if isinstance(probe, JSONResponse):
            return probe
        if not agent_commands.capability(probe)["sealed"]:
            return _refuse("This probe does not support sealed logins (update its agent).", 409,
                           "sealed_not_enabled")
        cmd = await agent_commands.enqueue(db, probe, "rotate_seal_key", {},
                                           created_by=_username(request), target=None)
        await log_action(db, request, "probe.seal_key.rotate", "agent", probe.id, probe.name,
                         {"command_id": cmd.id})
        await db.commit()
        return JSONResponse({"ok": True, "command_id": cmd.id}, status_code=202)

"""Structured commands for probes, carried by the heartbeat (part C).

See ``docs/specs/2026-11-19-probe-agent-deploy-design.md``. A command is
data for a closed set of operations the probe implements itself; there is no
op that runs a command line or fetches a URL. The payload (it can hold an
install token) is encrypted under the tenant key and cleared once delivered;
results are stored redacted.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select, update

from models.agent_deploy import AgentCommand, AgentDeployJob
from services import seal_keys
from services.audit import log_action

log = logging.getLogger(__name__)

OPS = frozenset({"ssh_host_key", "deploy_agent", "rotate_seal_key"})
TTL = timedelta(minutes=10)
# A deploy run of many containers goes out five per heartbeat and installs
# one after another on the probe: give the last of them time.
DEPLOY_TTL = timedelta(minutes=30)
# A delivered command whose result has not come back this long after it
# expired is given up (the probe went away or never answered).
RESULT_GRACE = timedelta(minutes=10)
MAX_PER_HEARTBEAT = 5
RESULT_LIMIT = 4000
FINISHED = ("ok", "failed", "expired")

_INSTALL_TOKEN = re.compile(r"nt_[0-9A-Za-z_-]{8,}")
_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def redact(text: str) -> str:
    """Install tokens reduced to their prefix."""
    return _INSTALL_TOKEN.sub(lambda m: m.group(0)[:8] + "…", text)


async def enqueue(db, probe, op: str, payload: dict, *, created_by: str | None,
                  target: str | None = None, ttl: timedelta | None = None) -> AgentCommand:
    """Queue ``op`` for ``probe`` (flushed, not committed)."""
    from services import tenant_keys

    if op not in OPS:
        raise ValueError(f"unknown op {op!r}")
    if not probe.is_probe:
        raise ValueError("commands are for probes only")
    if ttl is None:
        ttl = DEPLOY_TTL if op == "deploy_agent" else TTL
    now = datetime.utcnow()
    cmd = AgentCommand(
        agent_id=probe.id, op=op, status="queued", created_at=now, expires_at=now + ttl,
        created_by=created_by, target=target,
        payload=await tenant_keys.encrypt_secret(db, json.dumps(payload), tenant_id=probe.tenant_id),
    )
    db.add(cmd)
    await db.flush()
    return cmd


def effective_status(cmd: AgentCommand, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    if cmd.status == "queued" and cmd.expires_at <= now:
        return "expired"
    if cmd.status == "delivered" and cmd.expires_at + RESULT_GRACE <= now:
        return "expired"
    return cmd.status


async def _jobs_of(db, cmd_ids: list[int]) -> list[AgentDeployJob]:
    if not cmd_ids:
        return []
    return list((await db.execute(select(AgentDeployJob).where(
        AgentDeployJob.command_id.in_(cmd_ids)))).scalars().all())


async def _fail_jobs(db, cmd_ids: list[int], detail: str, now: datetime) -> None:
    for job in await _jobs_of(db, cmd_ids):
        if job.status in ("queued", "running"):
            job.status, job.finished_at, job.detail = "failed", now, detail


async def expire_for_credential(db, credential_id: int, probe_id: int, detail: str,
                                now: datetime | None = None) -> list[int]:
    """Expire the still-queued commands that carry ``credential_id`` and fail
    their jobs; returns the expired ids. The conditional update cannot race
    with ``deliver``: a command already claimed there is left alone."""
    now = now or datetime.utcnow()
    rows = await db.execute(
        update(AgentCommand)
        .where(AgentCommand.credential_id == credential_id, AgentCommand.agent_id == probe_id,
               AgentCommand.status == "queued")
        .values(status="expired", payload=None, finished_at=now)
        .returning(AgentCommand.id)
        .execution_options(synchronize_session=False))
    ids = [r[0] for r in rows.all()]
    await _fail_jobs(db, ids, detail, now)
    return ids


async def deliver(db, agent, now: datetime | None = None) -> list[dict]:
    """The commands to hand out with this heartbeat; marks them delivered.

    Each command is claimed with a conditional update, so two heartbeats of
    the same probe at once cannot both hand out the same command. Delivered
    commands whose answer is overdue are written off here as well.
    """
    from services import tenant_keys

    now = now or datetime.utcnow()
    overdue = (await db.execute(select(AgentCommand).where(
        AgentCommand.agent_id == agent.id, AgentCommand.status == "delivered",
        AgentCommand.expires_at <= now - RESULT_GRACE))).scalars().all()
    for cmd in overdue:
        cmd.status, cmd.finished_at = "expired", now
    await _fail_jobs(db, [c.id for c in overdue],
                     "The probe did not answer in time (offline, or an older agent).", now)

    pending = (await db.execute(select(AgentCommand).where(
        AgentCommand.agent_id == agent.id, AgentCommand.status == "queued",
    ).order_by(AgentCommand.id))).scalars().all()
    out, delivered, expired, broken = [], [], [], []
    for cmd in pending:
        if cmd.expires_at <= now:
            cmd.status, cmd.payload, cmd.finished_at = "expired", None, now
            expired.append(cmd.id)
            continue
        if len(out) >= MAX_PER_HEARTBEAT:
            continue
        claimed = await db.execute(
            update(AgentCommand)
            .where(AgentCommand.id == cmd.id, AgentCommand.status == "queued")
            .values(status="delivered", delivered_at=now)
            .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            continue  # another heartbeat took it
        try:
            payload = json.loads(await tenant_keys.decrypt_secret(db, cmd.payload,
                                                                  tenant_id=cmd.tenant_id))
        except Exception:  # noqa: BLE001 — one unreadable command must not stop the heartbeat
            log.error("Command %d for agent %d cannot be decrypted", cmd.id, agent.id)
            cmd.status, cmd.payload, cmd.finished_at = "failed", None, now
            cmd.result = json.dumps({"detail": "The command could not be decrypted."})
            broken.append(cmd.id)
            continue
        out.append({"id": cmd.id, "op": cmd.op, "payload": payload,
                    "expires_at": cmd.expires_at.isoformat() + "Z"})
        cmd.status, cmd.payload, cmd.delivered_at = "delivered", None, now
        delivered.append(cmd.id)
    for job in await _jobs_of(db, delivered):
        if job.status == "queued":
            job.status, job.started_at = "running", now
    await _fail_jobs(db, expired, "The probe did not pick up the command in time.", now)
    await _fail_jobs(db, broken, "The command could not be decrypted on the server.", now)
    return out


async def apply_results(db, agent, results, now: datetime | None = None) -> None:
    """Record what the probe answered. Only commands of this agent that were
    delivered and are not finished can be answered; anything else is ignored."""
    now = now or datetime.utcnow()
    if not isinstance(results, list):
        return
    answers: dict[int, dict] = {}
    for item in results[:50]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        if item.get("status") not in ("ok", "failed"):
            continue
        answers[item["id"]] = item
    if not answers:
        return
    cmds = (await db.execute(select(AgentCommand).where(
        AgentCommand.id.in_(list(answers)), AgentCommand.agent_id == agent.id,
        AgentCommand.status == "delivered",
        # An answer after the UI gave up does not reverse what it showed.
        AgentCommand.expires_at > now - RESULT_GRACE))).scalars().all()
    for cmd in cmds:
        answer = answers[cmd.id]
        result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
        status = answer["status"]
        if cmd.op == "rotate_seal_key" and status == "ok":
            # The answer is the probe's new sealing key: trust it (the admin
            # asked for the rotation) and keep only its fingerprint.
            key = seal_keys.parse_report({"suite": seal_keys.SUITE,
                                          "public_key": result.get("public_key")})
            if key is None:
                status, result = "failed", {"detail": "The probe answered without a valid key."}
            else:
                seal_keys.trust(agent, key, cmd.created_by)
                agent.seal_key_reported = key
                result = {"fingerprint": seal_keys.fingerprint(key)}
                await log_action(db, None, "probe.seal_key.trust", "agent", agent.id, agent.name,
                                 {"fingerprint": result["fingerprint"], "rotation": True})
        text = redact(json.dumps(result))[:RESULT_LIMIT]
        cmd.status, cmd.result, cmd.finished_at = status, text, now
    by_id = {c.id: c for c in cmds}
    for job in await _jobs_of(db, list(by_id)):
        cmd = by_id[job.command_id]
        try:
            detail = json.loads(cmd.result or "{}").get("detail")
        except ValueError:
            detail = None
        job.status = "ok" if cmd.status == "ok" else "failed"
        job.detail = redact(str(detail))[-2000:] if detail else None
        job.finished_at = now


_ENVELOPE_KEYS = frozenset({"v", "suite", "key", "enc", "ct"})
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
MAX_ENVELOPE = 32768


def validate_envelope(raw) -> dict | None:
    """Shape check of a sealed envelope (the server cannot open it): the clean
    five-key dict, or None."""
    if not isinstance(raw, dict):
        return None
    try:
        if len(json.dumps(raw).encode()) > MAX_ENVELOPE:
            return None
    except (TypeError, ValueError):
        return None
    if set(raw) != _ENVELOPE_KEYS:
        return None
    if type(raw["v"]) is not int or raw["v"] != 1 or raw["suite"] != seal_keys.SUITE:
        return None
    key, enc, ct = raw["key"], raw["enc"], raw["ct"]
    if not isinstance(key, str) or not _FINGERPRINT.match(key):
        return None
    if not isinstance(enc, str) or not isinstance(ct, str):
        return None
    try:
        if len(base64.b64decode(enc, validate=True)) != 32:
            return None
        if not base64.b64decode(ct, validate=True):
            return None
    except (binascii.Error, ValueError, TypeError):
        return None
    return {"v": 1, "suite": raw["suite"], "key": key, "enc": enc, "ct": ct}


def sanitize_capability(raw) -> str | None:
    """The probe's ``deploy`` report as stored JSON: only known keys, names
    that look like names, ``enabled`` true only when it says ``true``."""
    if not isinstance(raw, dict):
        return None
    names = [n for n in raw.get("credentials") or [] if isinstance(n, str) and _NAME.match(n)]
    arch = [a for a in raw.get("arch") or [] if isinstance(a, str) and _NAME.match(a)]
    return json.dumps({"enabled": raw.get("enabled") is True, "credentials": names[:32],
                       "arch": arch[:8], "sealed": raw.get("sealed") is True})


def capability(agent) -> dict:
    try:
        cap = json.loads(agent.deploy_capability or "null")
    except ValueError:
        cap = None
    if not isinstance(cap, dict):
        cap = {"enabled": False, "credentials": [], "arch": []}
    # Rows stored before the flag existed read as not sealed.
    return {**cap, "sealed": cap.get("sealed") is True}


async def handle_heartbeat(db, agent, body: dict) -> list[dict] | None:
    """Everything the heartbeat of a probe does with commands; ``None`` for
    an agent that is not a probe (its response carries no ``commands``)."""
    if not agent.is_probe:
        return None
    if "deploy" in body:
        agent.deploy_capability = sanitize_capability(body.get("deploy"))
        deploy = body.get("deploy")
        await seal_keys.observe(
            db, agent, seal_keys.parse_report(deploy.get("seal") if isinstance(deploy, dict) else None))
    now = datetime.utcnow()
    await apply_results(db, agent, body.get("command_results"), now)
    return await deliver(db, agent, now)


# Keys of the heartbeat that belong to commands, not to the metrics snapshot
# (the snapshot is stored and served as is).
HEARTBEAT_KEYS = ("command_results", "deploy")

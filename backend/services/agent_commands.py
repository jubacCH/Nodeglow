"""Structured commands for probes, carried by the heartbeat (part C).

See ``docs/specs/2026-11-19-probe-agent-deploy-design.md``. A command is
data for a closed set of operations the probe implements itself; there is no
op that runs a command line or fetches a URL. The payload (it can hold an
install token) is encrypted under the tenant key and cleared once delivered;
results are stored redacted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from models.agent_deploy import AgentCommand, AgentDeployJob

OPS = frozenset({"ssh_host_key", "deploy_agent"})
TTL = timedelta(minutes=10)
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
                  ttl: timedelta = TTL) -> AgentCommand:
    """Queue ``op`` for ``probe`` (flushed, not committed)."""
    from services import tenant_keys

    if op not in OPS:
        raise ValueError(f"unknown op {op!r}")
    if not probe.is_probe:
        raise ValueError("commands are for probes only")
    now = datetime.utcnow()
    cmd = AgentCommand(
        agent_id=probe.id, op=op, status="queued", created_at=now, expires_at=now + ttl,
        created_by=created_by,
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


async def deliver(db, agent, now: datetime | None = None) -> list[dict]:
    """The commands to hand out with this heartbeat; marks them delivered."""
    from services import tenant_keys

    now = now or datetime.utcnow()
    pending = (await db.execute(select(AgentCommand).where(
        AgentCommand.agent_id == agent.id, AgentCommand.status == "queued",
    ).order_by(AgentCommand.id))).scalars().all()
    out, delivered = [], []
    for cmd in pending:
        if cmd.expires_at <= now:
            cmd.status, cmd.payload, cmd.finished_at = "expired", None, now
            continue
        if len(out) >= MAX_PER_HEARTBEAT:
            continue
        payload = json.loads(await tenant_keys.decrypt_secret(db, cmd.payload,
                                                              tenant_id=cmd.tenant_id))
        out.append({"id": cmd.id, "op": cmd.op, "payload": payload,
                    "expires_at": cmd.expires_at.isoformat() + "Z"})
        cmd.status, cmd.payload, cmd.delivered_at = "delivered", None, now
        delivered.append(cmd.id)
    for job in await _jobs_of(db, delivered):
        if job.status == "queued":
            job.status, job.started_at = "running", now
    expired = [c.id for c in pending if c.status == "expired"]
    for job in await _jobs_of(db, expired):
        if job.status in ("queued", "running"):
            job.status, job.finished_at = "failed", now
            job.detail = "The probe did not pick up the command in time."
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
        AgentCommand.status == "delivered"))).scalars().all()
    for cmd in cmds:
        answer = answers[cmd.id]
        result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
        text = redact(json.dumps(result))[:RESULT_LIMIT]
        cmd.status, cmd.result, cmd.finished_at = answer["status"], text, now
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


def sanitize_capability(raw) -> str | None:
    """The probe's ``deploy`` report as stored JSON: only known keys, names
    that look like names, ``enabled`` true only when it says ``true``."""
    if not isinstance(raw, dict):
        return None
    names = [n for n in raw.get("credentials") or [] if isinstance(n, str) and _NAME.match(n)]
    arch = [a for a in raw.get("arch") or [] if isinstance(a, str) and _NAME.match(a)]
    return json.dumps({"enabled": raw.get("enabled") is True, "credentials": names[:32],
                       "arch": arch[:8]})


def capability(agent) -> dict:
    try:
        cap = json.loads(agent.deploy_capability or "null")
    except ValueError:
        cap = None
    return cap if isinstance(cap, dict) else {"enabled": False, "credentials": [], "arch": []}


async def handle_heartbeat(db, agent, body: dict) -> list[dict] | None:
    """Everything the heartbeat of a probe does with commands; ``None`` for
    an agent that is not a probe (its response carries no ``commands``)."""
    if not agent.is_probe:
        return None
    if "deploy" in body:
        agent.deploy_capability = sanitize_capability(body.get("deploy"))
    await apply_results(db, agent, body.get("command_results"))
    return await deliver(db, agent)

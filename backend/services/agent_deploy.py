"""Deploy runs: the jobs of a run, the background task that works them off,
the gate, and the API view.

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``. A run is created in
the request (rows ``queued``, tokens issued, audit written) and worked off by
an in-process task. Credentials and plaintext tokens exist only in that
task's memory; the rows get the status and redacted output.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from services import ssh_deploy
from services.host_state import iso
from services.tenancy import tenant_scope

log = logging.getLogger(__name__)

CONCURRENCY = 4
TARGET_TIMEOUT = 180.0
# A run without a live task in this process (server restarted) is reported
# as interrupted once it is older than this: long enough for the request
# that created it to have started its task.
ORPHAN_AFTER = timedelta(minutes=2)
ACTIVE = ("queued", "running")

_tasks: set[asyncio.Task] = set()
_live_runs: set[str] = set()


def deploy_enabled() -> bool:
    """``NODEGLOW_SSH_DEPLOY``: ``on``, ``off`` or ``auto`` (default). Auto is
    off where one server holds many tenants: it must not open SSH connections
    into customer networks (that is the probe's job)."""
    from services import agent_release

    value = (os.environ.get("NODEGLOW_SSH_DEPLOY") or "auto").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    return not agent_release.multi_tenant_mode()


@dataclass
class RunTarget:
    job_id: int
    token: str = field(repr=False)
    vmid: int | None = None     # set: a container on the node behind the connection
    fresh: bool = True          # False: a reinstall, whose seal.key must survive


@dataclass
class Run:
    run_id: str
    tenant_id: int
    host: str
    port: int
    pinned_public_key: str
    creds: ssh_deploy.SshCredentials
    server_url: str
    targets: list[RunTarget]
    finished: set[int] = field(default_factory=set)


def is_live(run_id: str) -> bool:
    return run_id in _live_runs


def job_view(job, now: datetime | None = None, *, live: bool = True) -> dict:
    """A job as the API shows it. A job still queued or running whose run has
    no task in this process any more (server restart) reads as failed."""
    now = now or datetime.utcnow()
    status, detail = job.status, job.detail
    if (status in ACTIVE and not live and job.created_at
            and now - job.created_at > ORPHAN_AFTER):
        status, detail = "failed", "interrupted (the server restarted during the deploy)"
    return {
        "id": job.id,
        "run_id": job.run_id,
        "kind": job.kind,
        "target": job.target,
        "agent_id": job.agent_id,
        "status": status,
        "detail": detail,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }


def start(run: Run) -> asyncio.Task:
    _live_runs.add(run.run_id)
    task = asyncio.create_task(_execute(run))
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        _live_runs.discard(run.run_id)

    task.add_done_callback(_done)
    return task


async def drain() -> None:
    """Wait for every running deploy (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _update(tenant_id: int, job_id: int, **values) -> None:
    from models import base as models_base
    from models.agent_deploy import AgentDeployJob

    with tenant_scope(tenant_id):
        async with models_base.AsyncSessionLocal() as db:
            job = await db.get(AgentDeployJob, job_id)
            if job is None:
                return
            for key, value in values.items():
                setattr(job, key, value)
            await db.commit()


async def _finish(run: Run, target: RunTarget, ok: bool, text: str) -> None:
    run.finished.add(target.job_id)
    detail = ssh_deploy.redact(text, tokens=[t.token for t in run.targets],
                               secrets=run.creds.secrets())
    await _update(run.tenant_id, target.job_id, status="ok" if ok else "failed",
                  detail=detail or None, finished_at=datetime.utcnow())


async def _run_target(run: Run, conn, target: RunTarget, gate: asyncio.Semaphore) -> None:
    from routers.agents import render_linux_installer

    async with gate:
        await _update(run.tenant_id, target.job_id, status="running",
                      started_at=datetime.utcnow())
        script = render_linux_installer(run.server_url, target.token, fresh=target.fresh)
        try:
            if target.vmid is None:
                rc, out = await ssh_deploy.run_installer(conn, script, run.creds,
                                                         timeout=TARGET_TIMEOUT)
            else:
                rc, out = await ssh_deploy.run_in_lxc(conn, target.vmid, script, run.creds,
                                                      timeout=TARGET_TIMEOUT)
        except ssh_deploy.SshDeployError as exc:
            await _finish(run, target, False, str(exc))
            return
        except Exception:  # noqa: BLE001 — one target's crash stays its own
            log.exception("Agent deploy target %s crashed", target.job_id)
            await _finish(run, target, False, "internal error, see the server log")
            return
        if rc != 0:
            out = f"{out.rstrip()}\n(exit code {rc})".lstrip()
        await _finish(run, target, rc == 0, out)


async def _execute(run: Run) -> None:
    """One connection to ``run.host``; every target over it, ``CONCURRENCY``
    at a time. A connection failure fails every target with its message."""
    try:
        async with ssh_deploy.connect(run.host, run.port, run.creds,
                                      run.pinned_public_key) as conn:
            gate = asyncio.Semaphore(CONCURRENCY)
            await asyncio.gather(*(_run_target(run, conn, t, gate) for t in run.targets),
                                 return_exceptions=True)
    except ssh_deploy.SshDeployError as exc:
        await _fail_open(run, str(exc))
    except Exception:  # noqa: BLE001 — a crash must not leave jobs "running"
        log.exception("Agent deploy to %s:%s crashed", run.host, run.port)
        await _fail_open(run, "internal error, see the server log")


async def _fail_open(run: Run, message: str) -> None:
    """Fail the targets that have no result yet; finished ones keep theirs."""
    for target in run.targets:
        if target.job_id not in run.finished:
            await _finish(run, target, False, message)

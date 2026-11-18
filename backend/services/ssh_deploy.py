"""SSH transport for the agent deploy.

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``. The installer goes
to the target over stdin, never in a command line; secrets stay in memory
(asyncssh takes keys as strings, nothing is written to disk). A connection
is only made to a host key the admin confirmed: the key check is part of the
key exchange, so no credential is sent to a host that fails it.
"""
from __future__ import annotations

import asyncio
import re
import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

import asyncssh

CONNECT_TIMEOUT = 15.0
DETAIL_LIMIT = 2000
_TMP_PATH = re.compile(r"^/[\w./-]+$")


class SshDeployError(Exception):
    """A target could not be deployed; ``str()`` is safe to show and store."""


class HostKeyMismatch(SshDeployError):
    pass


class SshAuthFailed(SshDeployError):
    pass


class SshTimeout(SshDeployError):
    pass


@dataclass(frozen=True)
class HostKey:
    key_type: str
    public_key: str   # OpenSSH format, one line
    fingerprint: str  # SHA256:...


@dataclass
class SshCredentials:
    """Login for one deploy. Secrets are excluded from ``repr`` so a stray
    log line or traceback cannot print them."""

    username: str
    password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    passphrase: str | None = field(default=None, repr=False)
    sudo_password: str | None = field(default=None, repr=False)

    @property
    def is_root(self) -> bool:
        return self.username == "root"

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.passphrase, self.sudo_password) if s]


def _host_key(key: asyncssh.SSHKey) -> HostKey:
    return HostKey(
        key_type=key.get_algorithm(),
        public_key=key.export_public_key("openssh").decode().strip(),
        fingerprint=key.get_fingerprint("sha256"),
    )


async def fetch_host_key(host: str, port: int = 22, timeout: float = CONNECT_TIMEOUT) -> HostKey:
    """The host key ``host:port`` presents, read from the key exchange alone
    (no authentication), for the admin to confirm."""
    try:
        key = await asyncio.wait_for(
            asyncssh.get_server_host_key(host, port, config=None), timeout)
    except asyncio.TimeoutError as exc:
        raise SshTimeout(f"No answer from {host}:{port} within {timeout:.0f} s") from exc
    except (OSError, asyncssh.Error) as exc:
        raise SshDeployError(f"Cannot reach {host}:{port}: {exc}") from exc
    if key is None:
        raise SshDeployError(f"{host}:{port} presented no host key")
    return _host_key(key)


@asynccontextmanager
async def connect(
    host: str, port: int, creds: SshCredentials, pinned_public_key: str,
    timeout: float = CONNECT_TIMEOUT,
) -> AsyncIterator[asyncssh.SSHClientConnection]:
    """An authenticated connection that trusts ``pinned_public_key`` only."""
    trusted = asyncssh.import_public_key(pinned_public_key)
    client_keys = None
    if creds.private_key:
        try:
            client_keys = [asyncssh.import_private_key(creds.private_key, creds.passphrase)]
        except (asyncssh.KeyImportError, ValueError) as exc:
            raise SshAuthFailed("The private key cannot be read (wrong passphrase?)") from exc
    try:
        conn = await asyncio.wait_for(asyncssh.connect(
            host, port,
            username=creds.username,
            password=creds.password,
            client_keys=client_keys,
            known_hosts=([trusted], [], []),
            config=None,
            agent_path=None,
            preferred_auth="publickey,password,keyboard-interactive",
        ), timeout)
    except asyncio.TimeoutError as exc:
        raise SshTimeout(f"No answer from {host}:{port} within {timeout:.0f} s") from exc
    except asyncssh.HostKeyNotVerifiable as exc:
        raise HostKeyMismatch(
            f"The host key of {host}:{port} changed. Check the host, then remove the stored "
            "key under Settings to trust the new one.") from exc
    except asyncssh.PermissionDenied as exc:
        raise SshAuthFailed(f"Login as {creds.username} on {host}:{port} was refused") from exc
    except (OSError, asyncssh.Error) as exc:
        raise SshDeployError(f"Cannot connect to {host}:{port}: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()
        await conn.wait_closed()


async def _run(conn, command: str, stdin: str, timeout: float) -> tuple[int, str]:
    try:
        result = await conn.run(command, input=stdin, stderr=asyncssh.STDOUT,
                                check=False, timeout=timeout)
    except asyncssh.TimeoutError as exc:
        raise SshTimeout(f"The installer did not finish within {timeout:.0f} s") from exc
    except (OSError, asyncssh.Error) as exc:
        raise SshDeployError(f"The connection failed: {exc}") from exc
    status = result.exit_status if result.exit_status is not None else 255
    return status, str(result.stdout or "")


async def run_installer(conn, script: str, creds: SshCredentials,
                        timeout: float = 180.0) -> tuple[int, str]:
    """Run ``script`` as root on the host behind ``conn``.

    Root: ``bash -s``. Without a sudo password: ``sudo -n`` (NOPASSWD). With
    one, the script is first written to a private temp file, then sudo gets
    the password as its only stdin and runs the file with stdin from
    /dev/null, so the password cannot reach anything but sudo.
    """
    if creds.is_root:
        return await _run(conn, "bash -s", script, timeout)
    if not creds.sudo_password:
        return await _run(conn, "sudo -n bash -s", script, timeout)

    rc, path = await _run(conn, 'f=$(mktemp) && chmod 600 "$f" && cat > "$f" && printf %s "$f"',
                          script, timeout)
    path = path.strip()
    if rc != 0 or not _TMP_PATH.match(path):
        raise SshDeployError("Could not write the installer to a temp file on the host")
    q = shlex.quote(path)
    command = (f"sudo -S -p '' bash -c 'exec bash \"$0\" </dev/null' {q}; "
               f"rc=$?; rm -f {q}; exit $rc")
    return await _run(conn, command, creds.sudo_password + "\n", timeout)


def lxc_command(vmid, username: str) -> str:
    vmid_text = str(vmid)
    if not vmid_text.isdigit():
        raise ValueError(f"Not a container id: {vmid_text!r}")
    command = f"pct exec {int(vmid_text)} -- bash -s"
    return command if username == "root" else f"sudo -n {command}"


async def run_in_lxc(conn, vmid, script: str, creds: SshCredentials,
                     timeout: float = 180.0) -> tuple[int, str]:
    """Run ``script`` inside container ``vmid`` on the Proxmox node behind
    ``conn`` (root, or NOPASSWD sudo for ``pct``)."""
    return await _run(conn, lxc_command(vmid, creds.username), script, timeout)


def redact(text: str, *, tokens: list[str], secrets: list[str] = ()) -> str:
    """Output fit to store and show: install tokens reduced to their prefix,
    other secrets removed, the last ``DETAIL_LIMIT`` characters kept."""
    for token in tokens:
        if token:
            text = text.replace(token, token[:8] + "…")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[-DETAIL_LIMIT:]

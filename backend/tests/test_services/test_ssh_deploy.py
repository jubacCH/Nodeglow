"""SSH transport of the agent deploy, against a real in-process SSH server.

The server records what the client did (auth attempts, commands, stdin), so
the tests can assert what never happened: authentication against an
unconfirmed host key, a sudo password reaching bash, a token in a command.
"""
import asyncio

import asyncssh
import pytest

from services import ssh_deploy as sd

SCRIPT = '#!/bin/bash\nENROLLMENT_KEY="nt_secret-token-value"\necho installed\n'
TOKEN = "nt_secret-token-value"


class _Recorder:
    def __init__(self):
        self.auth_attempts = 0
        self.commands: list[str] = []
        self.stdin: dict[str, str] = {}
        self.hang = False
        self.hang_on: str | None = None


class _Server(asyncssh.SSHServer):
    def __init__(self, rec, client_key):
        self.rec, self.client_key = rec, client_key

    def begin_auth(self, username):
        self.rec.auth_attempts += 1
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return password == "pw"

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return key.export_public_key() == self.client_key.export_public_key()


async def _handle(rec, process):
    cmd = process.command or ""
    rec.commands.append(cmd)
    data = await process.stdin.read()
    rec.stdin[cmd] = data
    if rec.hang or (rec.hang_on and rec.hang_on in cmd):
        await asyncio.sleep(30)
    if cmd.startswith("f=$(mktemp)"):
        process.stdout.write("/tmp/tmp.AbC123")
    elif "sudo -S" in cmd:
        process.stdout.write("installed via sudo\n")
    else:
        # Echo the token line back: real installers print errors like this.
        process.stdout.write("installed\n" + data[:200])
        process.stderr.write("warn: something on stderr\n")
    process.exit(0)


@pytest.fixture
async def ssh_server():
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    rec = _Recorder()
    server = await asyncssh.listen(
        "127.0.0.1", 0, server_host_keys=[host_key],
        server_factory=lambda: _Server(rec, client_key),
        process_factory=lambda p: _handle(rec, p),
    )
    port = server.sockets[0].getsockname()[1]
    pub = host_key.export_public_key("openssh").decode().strip()
    yield {"port": port, "rec": rec, "host_key": host_key, "pub": pub,
           "fingerprint": host_key.get_fingerprint("sha256"), "client_key": client_key}
    server.close()
    await server.wait_closed()


def _creds(**kw):
    base = {"username": "root", "password": "pw"}
    base.update(kw)
    return sd.SshCredentials(**base)


async def test_fetch_host_key_does_not_authenticate(ssh_server):
    key = await sd.fetch_host_key("127.0.0.1", ssh_server["port"])
    assert key.fingerprint == ssh_server["fingerprint"]
    assert key.key_type == "ssh-ed25519"
    assert key.public_key == ssh_server["pub"]
    assert ssh_server["rec"].auth_attempts == 0


async def test_root_gets_the_script_on_stdin(ssh_server):
    async with sd.connect("127.0.0.1", ssh_server["port"], _creds(), ssh_server["pub"]) as conn:
        rc, out = await sd.run_installer(conn, SCRIPT, _creds())
    rec = ssh_server["rec"]
    assert rc == 0 and rec.commands == ["bash -s"]
    assert rec.stdin["bash -s"] == SCRIPT
    assert "installed" in out and "warn: something on stderr" in out


async def test_a_different_host_key_is_refused_before_authentication(ssh_server):
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
    with pytest.raises(sd.HostKeyMismatch):
        async with sd.connect("127.0.0.1", ssh_server["port"], _creds(), other):
            pass
    assert ssh_server["rec"].auth_attempts == 0
    assert ssh_server["rec"].commands == []


async def test_private_key_login(ssh_server):
    pem = ssh_server["client_key"].export_private_key().decode()
    creds = _creds(password=None, private_key=pem)
    async with sd.connect("127.0.0.1", ssh_server["port"], creds, ssh_server["pub"]) as conn:
        rc, _out = await sd.run_installer(conn, SCRIPT, creds)
    assert rc == 0


async def test_wrong_password_is_an_auth_error(ssh_server):
    with pytest.raises(sd.SshAuthFailed):
        async with sd.connect("127.0.0.1", ssh_server["port"], _creds(password="nope"),
                              ssh_server["pub"]):
            pass


async def test_non_root_without_sudo_password_uses_sudo_n(ssh_server):
    creds = _creds(username="deploy")
    async with sd.connect("127.0.0.1", ssh_server["port"], creds, ssh_server["pub"]) as conn:
        await sd.run_installer(conn, SCRIPT, creds)
    assert ssh_server["rec"].commands == ["sudo -n bash -s"]


async def test_sudo_password_only_ever_reaches_sudo(ssh_server):
    creds = _creds(username="deploy", sudo_password="sudo-secret")
    async with sd.connect("127.0.0.1", ssh_server["port"], creds, ssh_server["pub"]) as conn:
        rc, out = await sd.run_installer(conn, SCRIPT, creds)
    rec = ssh_server["rec"]
    assert rc == 0 and len(rec.commands) == 2
    upload, run = rec.commands
    assert rec.stdin[upload] == SCRIPT
    assert rec.stdin[run] == "sudo-secret\n"
    assert "sudo -S" in run and "</dev/null" in run and "/tmp/tmp.AbC123" in run
    assert "rm -f" in run
    assert all("sudo-secret" not in c and TOKEN not in c for c in rec.commands)
    assert "sudo-secret" not in out


async def test_lxc_runs_through_pct_exec(ssh_server):
    async with sd.connect("127.0.0.1", ssh_server["port"], _creds(), ssh_server["pub"]) as conn:
        rc, _out = await sd.run_in_lxc(conn, 105, SCRIPT, _creds())
    assert rc == 0 and ssh_server["rec"].commands == ["pct exec 105 -- bash -s"]
    assert ssh_server["rec"].stdin["pct exec 105 -- bash -s"] == SCRIPT


async def test_lxc_as_non_root_uses_sudo_n(ssh_server):
    creds = _creds(username="deploy")
    async with sd.connect("127.0.0.1", ssh_server["port"], creds, ssh_server["pub"]) as conn:
        await sd.run_in_lxc(conn, 105, SCRIPT, creds)
    assert ssh_server["rec"].commands == ["sudo -n pct exec 105 -- bash -s"]


def test_lxc_vmid_must_be_a_number():
    with pytest.raises(ValueError):
        sd.lxc_command("105; rm -rf /", "root")


async def test_a_hanging_target_times_out(ssh_server):
    ssh_server["rec"].hang = True
    async with sd.connect("127.0.0.1", ssh_server["port"], _creds(), ssh_server["pub"]) as conn:
        with pytest.raises(sd.SshTimeout):
            await sd.run_installer(conn, SCRIPT, _creds(), timeout=0.5)


def test_redact_keeps_the_prefix_and_the_tail():
    text = "x" * 3000 + f" token {TOKEN} pw sudo-secret end"
    out = sd.redact(text, tokens=[TOKEN], secrets=["sudo-secret"])
    assert TOKEN not in out and "sudo-secret" not in out
    assert TOKEN[:8] + "…" in out and "[redacted]" in out
    assert len(out) <= sd.DETAIL_LIMIT and out.endswith("end")


def test_credentials_repr_hides_secrets():
    creds = _creds(password="pw-x", private_key="KEY-x", passphrase="pp-x", sudo_password="sp-x")
    text = repr(creds)
    assert all(s not in text for s in ("pw-x", "KEY-x", "pp-x", "sp-x"))


async def test_the_temp_file_is_removed_when_sudo_times_out(ssh_server):
    ssh_server["rec"].hang_on = "sudo -S"
    creds = _creds(username="deploy", sudo_password="sudo-secret")
    async with sd.connect("127.0.0.1", ssh_server["port"], creds, ssh_server["pub"]) as conn:
        with pytest.raises(sd.SshTimeout):
            await sd.run_installer(conn, SCRIPT, creds, timeout=0.5)
    assert ssh_server["rec"].commands[-1] == "rm -f /tmp/tmp.AbC123"

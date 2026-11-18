# Agent deploy over SSH (part B)

Status: approved 2026-11-18. Follows `2026-11-16-agent-reinstall-design.md`
(part A, bound install tokens).

## Why

Installing or reinstalling an agent means copying a command onto the host.
An admin who already has SSH access to the host wants Nodeglow to do that
step: pick the host and the credentials, and watch it happen. The Proxmox
"deploy agent" button promised exactly this for containers, but it never
worked: it pipes `/install/linux` without an install token (always 401), and
it starts the `ssh` binary, which the backend image does not contain.

## Goals

- Install a new agent, or reinstall an existing one (part A's bound token),
  on a Linux host over SSH, from the UI, with credentials from the
  credential store or entered once.
- Make the Proxmox deploy work: one SSH connection to the node, `pct exec`
  into each running container.
- Show progress per target, also for runs that take minutes.

## Non-goals

- Windows (WinRM). The credential type exists, the installer path differs.
- Deploy through a probe (part C), bulk or scheduled rollouts (enterprise).
- Managing SSH keys on targets, or anything beyond running the installer.

## Anti-goals

- A secret that lands on disk, in the database, in a process list, in a log
  line or in a job result. That includes the install token in the command
  line and the sudo password.
- Silently trusting an unknown or changed host key.
- A multi-tenant (hosted) server opening SSH connections into customer
  networks.

## Approaches

1. **`asyncssh` in the backend (chosen).** Pure Python, async, keys stay in
   memory, host-key checking and timeouts are first class, and tests can run
   a real SSH server in-process.
2. Ship `openssh-client` in the image and keep the subprocess. The key has to
   be written to a temp file, host-key handling goes through files and
   flags, and testing needs a real sshd.
3. Let the agent of a neighbouring host install it. That is part C's model
   (probe), not B.

## Design

### Delivery

The server renders the same Linux installer `/install/linux` serves, with a
fresh token in it, and writes it to the installer's stdin over SSH. The token
never appears in a URL or command line on either side.

- Root: `bash -s`.
- Not root, no password given: `sudo -n bash -s` (needs NOPASSWD).
- Not root, sudo password given: the script goes to a `mktemp` file (mode
  600) in a first command; the second runs
  `sudo -S -p '' bash -c 'exec bash "$0" </dev/null' "$f"` with the password
  as the only stdin line, and removes the file. The password can only ever
  reach sudo; when sudo does not prompt it is discarded.
- Proxmox containers: on the node (root, or `sudo -n`),
  `pct exec <vmid> -- bash -s` with the script on stdin.

Tokens: a new host gets an unbound install token (1 h, note
`ssh deploy <target>`). With `agent_id` (reinstall), the bound single-use
token of part A. One token per target. Output kept for the job is the last
2000 characters, with every issued token replaced by its prefix.

### Host keys

Stored per tenant and `host:port` (`ssh_host_keys`: key type, OpenSSH public
key, SHA256 fingerprint, first seen, created by). A deploy to a host without
a stored key answers `409 host_key_unknown` with the fingerprint, fetched
without authenticating. The admin confirms it in the UI and repeats the
request with `host_key_fingerprint`; the server connects only if the key it
sees has that fingerprint, then stores it. With a stored key, the connection
accepts that key only; a different key fails the target with
`host key changed`. An admin can delete a stored key (audited). No
authentication is attempted before the key is checked.

### Jobs

`agent_deploy_jobs`, one row per target: `run_id` (uuid), `kind`
(`ssh` | `proxmox_lxc`), `target` (host, or `<vmid> <name>`), `agent_id`,
`status` (`queued` | `running` | `ok` | `failed`), `detail`, timestamps,
`created_by`. The request validates, creates the rows and returns `202` with
the `run_id`; an in-process task works through the targets, at most 4 at a
time, 180 s per target. Credentials live only in that task's memory. A job
left `queued` or `running` for more than 15 minutes (server restart) reads
as `failed` / `interrupted`.

### API

- `POST /api/agent-deploy` — `{host, port=22, credential_id | username +
  password/private_key[/passphrase], sudo_password?, agent_id?,
  host_key_fingerprint?}` → `202 {run_id, jobs}` | `409 host_key_unknown
  {fingerprint, key_type}` | `409 host_key_mismatch`.
- `GET /api/agent-deploy/{run_id}` → jobs with status and detail.
- `GET /api/ssh-host-keys`, `DELETE /api/ssh-host-keys/{id}`.
- `POST /api/v1/integrations/proxmox/{id}/deploy-agent` keeps its path; with
  an SSH key in the integration it starts a run (same jobs, `proxmox_lxc`)
  and returns `202 {run_id, …}` (host-key flow as above, `host_key_fingerprint`
  in the body); without, it returns the manual script, now with a token.

All of it: admin only, not through support access, rate limited (10/min),
audited (`agent.deploy` with target, credential id, agent id; never secrets).

### Gate

`NODEGLOW_SSH_DEPLOY` = `auto` (default) | `on` | `off`. `auto` is off in
multi-tenant mode (`agent_release.multi_tenant_mode()`), on otherwise. Off
answers `403 {code: "ssh_deploy_disabled"}`; `GET /api/enrollment-info`
reports `ssh_deploy` so the UI hides the option.

### UI

- Add agent / Reinstall agent dialog: second tab "Install over SSH" with
  host, port, credential (store or once), sudo password. Unknown host key:
  shows the fingerprint and asks to trust it. Then the job's status and
  output tail; for a reinstall, the existing "connected" signal.
- Proxmox detail: the button starts the run and lists the containers with
  their status.

## Left to the builder

Library calls, table indexes, polling interval (2 s while a run is open),
copy wording within the rules above.

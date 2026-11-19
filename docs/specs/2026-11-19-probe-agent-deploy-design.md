# Agent deploy through a probe (part C)

Status: decided 2026-11-19 (owner delegated the calls marked "call").
Follows `2026-11-18-agent-ssh-deploy-design.md` (part B, server-side SSH) and
amends `2026-10-11-probe-side-integrations.md`, section "Proxmox deploy-agent"
(see "Spec amendment" below).

## Why

Part B needs the server to reach the target over SSH. In a hosted (SaaS)
installation it cannot, and must not: the customer's network is behind a
probe. Part C lets the probe do the installation, with credentials that never
leave the probe.

## Goals

- From the UI: "Install via probe" on a Linux host the probe can reach, or in
  containers of a Proxmox node the probe can reach. New agent or reinstall
  (part A's bound token).
- Available in SaaS and on-prem alike.
- Progress and result per target, like part B.

## Non-goals

- Credentials entered in the UI (sealed, S2). Later, on top of this.
- Windows targets, non-x86_64 targets (no signed arm64 binary exists yet).
- sudo with a password. Root or NOPASSWD sudo only.
- A Proxmox integration collected by the probe (P2+ of the probe spec).

## Anti-goals

- Any SSH secret in the SaaS: not in the database, not in a request, not in a
  log. The SaaS knows credential *names* only.
- The probe running anything the server chose: no script, no command line,
  no URL to fetch. Commands carry data; the procedure is compiled in.
- A server that can switch the feature on for a probe.

## Approaches

1. **S3 — credentials local on the probe (chosen, call).** A root-owned 0600
   file on the probe names SSH logins; the server refers to them by name.
   No secret in the SaaS, so a compromised SaaS gets nothing to replay.
2. S2 — credentials sealed in the browser to the probe's key. More
   comfortable, but needs X25519 sealing in browser and agent and a
   fingerprint confirmation flow, and protects only data at rest. Later.
3. The probe runs the server's installer script. Forbidden by the probe spec
   (rule 4), dropped.

## Design

### Probe-local opt-in and credentials

`/opt/nodeglow/deploy.json` (next to `config.json`, root:root 0600, never
written by the agent, never sent to the server):

```json
{
  "allow_deploy_agents": true,
  "credentials": {
    "lab-root": {"username": "root", "private_key_file": "/root/.ssh/id_ed25519",
                 "hosts": ["10.0.20.0/24", "*.lab.example"]},
    "pve": {"username": "deploy", "password": "…", "hosts": ["pve1.lab.example"]}
  }
}
```

`hosts` is required (names, `*.domain`, IPs, CIDRs): the probe uses a
credential only for those addresses, and answers `ssh_host_key` only for an
address some credential allows. Without it, a compromised server could name
any address, confirm its own host key and collect a password. A user other
than `root` goes through `sudo -n`.

Without the file, or with `allow_deploy_agents` not `true`, the probe refuses
every deploy command. The probe reports in its heartbeat
`deploy: {enabled, credentials: [names], arch: ["x86_64"]}`; the server stores
that on the agent row (`deploy_capability`, JSON) and offers the probe in the
UI only when `enabled`. A file the probe cannot read safely (not 0600 or not
owned by root) counts as absent.

### Commands (call: structured commands next to the old string)

`agent_commands` (tenant-owned, RLS): `id`, `agent_id` (the probe), `op`,
`payload` (encrypted under the tenant key, cleared once delivered), `status`
(`queued`, `delivered`, `ok`, `failed`, `expired`), `result` (JSON, redacted),
`created_at`, `expires_at` (10 min), `delivered_at`, `finished_at`,
`created_by`. Queued commands ride the heartbeat response
(`commands: [{id, op, payload, expires_at}]`, at most 5); results come back
in the next heartbeat (`command_results: [{id, status, result}]`). Latency is
one heartbeat interval; a long-poll is left for later. The old
`pending_command` string stays for `uninstall`.

Ops, a closed set:

- `ssh_host_key {host, port}` → `{key_type, fingerprint, public_key}`.
- `deploy_agent {target, credential, host_key_fingerprint, enrollment_token}`
  with `target` either `{"kind": "host", "host", "port"}` or
  `{"kind": "lxc", "host", "port", "vmid"}` (the node). One command per
  target (per container).

### What the probe does for `deploy_agent` (compiled in)

1. Refuse unless opted in, the credential name exists, and the command has
   not expired.
2. Download `/agents/download/linux` and `/api/agent/version/linux` from its
   own server and verify the **release signature** against the compiled-in
   release keys (`verify_release_signature`). No release signature: refuse.
3. SSH to the target (`russh`, call), accepting only the host key with the
   given fingerprint. Run `uname -m`; anything but `x86_64` is refused.
4. Host target, as root or through `sudo -n`, fixed commands only:
   `mkdir -p /opt/nodeglow`, write the binary (stdin), `config.json` (stdin,
   0600: `server` — the probe's own server, never a URL from the command, so a
   command cannot bind an agent to another server — `enrollment_key`,
   `interval`, and `require_release_signature: true`, whatever the probe's
   own setting: an agent a probe installed accepts release-signed updates
   only), the systemd unit (the installer's), then
   `systemctl daemon-reload && systemctl enable --now nodeglow-agent`
   (`restart` when it was running). The new agent enrols itself on start.
   LXC target: the same files through `pct push <vmid>` from a temp dir on the
   node, then `pct exec <vmid> -- systemctl …`.
5. Report `ok`/`failed` with a short detail (no token: the probe replaces it
   with its prefix; the server redacts again).

Reinstall works because a bound token re-enrols the existing agent by
hostname (part A); the enrolling agent needs no old token.

### Host keys

`ssh_host_keys` gets `probe_id` (NULL = the server itself, part B). Unique
per tenant, probe and address. First contact: the UI sends `ssh_host_key`,
waits for the result, shows the fingerprint, and on confirmation stores it
and sends `deploy_agent` with it. A changed key fails the job; the key list
on the Credentials page shows the probe.

### API (admin session, not support access, rate limited, audited)

- `POST /api/probes/{probe_id}/host-key` `{host, port}` → `202 {command_id}`;
  `GET /api/probes/{probe_id}/commands/{command_id}` → status and result.
- `POST /api/probes/{probe_id}/deploy` `{target, credential, agent_id?,
  host_key_fingerprint?}` (`target.vmids` for several containers) → unknown
  host key `409 host_key_unknown` (with a stored key or the confirmed
  fingerprint it proceeds), else `202 {run_id, jobs}`. Jobs are
  `agent_deploy_jobs` rows (`kind` `probe_host` / `probe_lxc`, new columns
  `probe_id`, `command_id`); `GET /api/agent-deploy/{run_id}` follows them.
  The job status mirrors its command; an expired command fails its job.
- Refused when the probe does not report `deploy.enabled`, the credential is
  not among its names, or the target agent is disabled. Not behind
  `NODEGLOW_SSH_DEPLOY`: that gate is about the server opening connections.

### Spec amendment (probe-side integrations, "Proxmox deploy-agent")

The section forbade the deploy in multi-tenant mode. It is allowed in both
modes under these conditions, which replace the old list:

- probe-local opt-in (`deploy.json`, the server cannot change it);
- credentials only on the probe (S3); the SaaS stores names;
- the probe installs the release-signed binary it verified itself, with the
  compiled-in procedure; commands carry data only;
- every run is in the probe's log and in the audit log;
- single-use (bound) or one-hour install tokens, encrypted at rest until
  delivered.

The reasoning: what the old rule guarded against — a compromised SaaS
running arbitrary code as root in the customer's containers — is closed by
the second and third point. A compromised SaaS can at most install the
genuine signed agent, enrolled into a tenant it controls, on hosts the
customer opted in with credentials it never sees.

## Delivery

Three PRs: (1) backend: migration 057, commands, heartbeat, routes, jobs;
(2) agent: `deploy.json`, ops, SSH, signed binary, fixed procedure;
(3) UI, docs, spec amendment applied to the probe spec.

## Left to the builder

Crate choice details, payload encryption helper, UI layout within part B's
components, polling interval.

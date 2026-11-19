# Sealed deploy credentials for probes (part C, S2)

Status: draft 2026-11-19.
Builds on `2026-11-19-probe-agent-deploy-design.md` (part C with probe-local
credentials) and implements the "sealed" credential mode of
`2026-10-11-probe-side-integrations.md` ("Credentials") for deploy logins.

## Why

Part C keeps SSH logins in `/opt/nodeglow/deploy.json` on the probe. Every new
login, every new network means a root shell on the probe. The owner wants this
automated as far as possible: a login is entered once in the UI and used from
then on, with no manual work on the probe.

## Goals

- An admin enters an SSH login (password or private key) in the deploy form.
  The browser seals it to the public key of one probe; the server stores only
  what it cannot open and hands it to the probe with each deploy command.
- No manual step on the probe beyond installing it: the probe makes its own
  sealing key, reports it, and the opt-in is a checkbox on the install command.
- The probe enforces the login's host list, which the server cannot change.
- Logins can be deleted; a new probe key makes all old ones useless.
- `deploy.json` logins keep working unchanged, side by side.

## Non-goals

- Integration secrets (P4 of the probe spec). The sealing code is written so it
  can be reused there, nothing more.
- Sealing one login to several probes at once, or moving logins to another
  probe. One login belongs to one probe; for another probe it is entered again.
- Windows probes (no `deploy.json` there either).
- An out-of-band fingerprint check of the probe key (see "Key trust").

## Anti-goals

- The server, its database, its backups or a support session being able to
  read a stored login. The server holds ciphertext under the probe's key, and
  that again under the tenant key.
- A server that can switch deploying or sealed logins on for a probe.
- A server that can point a stored login at a host the admin did not list.
  (It can seal logins of its own; see "What S2 protects".)
- Hand-rolled cryptography.

## What S2 protects, and what not

Protected: logins at rest in the SaaS (database, backups, an operator reading
tables, an approved support session), and a login's host list against a
compromised backend.

Not protected: a login being typed into a compromised SaaS. The server serves
the JavaScript that seals, so whoever controls it can read the input or serve
its own key. This is the same limit the probe spec states; customers whose
threat model includes a compromised SaaS keep using `deploy.json`.

Accepted risk (owner decision): the probe's public key is public, so a
compromised server can seal logins of its own, with any host list. It gains
no real login from that, but it can use a probe that allows sealed logins to
look for SSH servers in the customer network and to try logins. There is no
local host cap (it would need manual work on the probe for every network);
the probe throttles sealed operations instead (see below). Customers who do
not accept this leave `allow_sealed_credentials` off.

Single-tenant / on-prem: there the installer pins the server's own update
key, so a compromised server can ship an update to the probe and with it read
`seal.key`. The protections against a compromised server hold where the probe
accepts release-signed updates only (multi-tenant, and every probe in it).
On-prem the server is the customer's own; the docs say so.

## Approaches

1. **Server stores the sealed login; each deploy command carries it (chosen).**
   Table `probe_credentials` per probe. The probe opens it in memory per
   command and keeps nothing. Revocation is deleting the row; the probe is
   stateless, nothing to synchronise.
2. Probe stores sealed logins locally; commands name them. The server holds
   nothing after delivery, but needs sync/delete commands and probe state, and
   a revocation only reaches an online probe.
3. Nothing stored; the login is typed per deploy. Safest, but contradicts
   "automated".

## Design

### Probe sealing key

- On start, a probe (Unix only) loads `/opt/nodeglow/seal.key` (next to
  `config.json`), or creates it: a fresh X25519 private key, base64, root:root
  0600, written atomically. A file that is not 0600 or not owned by root is
  not used: the probe logs it and reports no key (sealed logins then fail with
  a clear message). The key never leaves the probe.
- The heartbeat reports, inside `deploy`:
  `seal: {"suite": "hpke-x25519-sha256-chacha20poly1305", "public_key": "<b64>"}`.
  Always reported by a probe that has a key, whatever its opt-in, so the UI can
  show it.
- Fingerprint: `SHA256:` + unpadded base64 of SHA-256 over the 32 raw public
  key bytes (same style as SSH). Shown in the UI and printed by
  `nodeglow-agent seal-key` on the probe, and logged once at start.
- Rotation: `nodeglow-agent seal-key --rotate` (or deleting the file), or the
  command `rotate_seal_key` from the UI ("Revoke all sealed logins"). All
  logins sealed to the old key become useless: that is the only real
  revocation, since the server or a backup may hold copies of deleted logins.
  A rotation the server asked for is trusted automatically when the next
  heartbeat reports the new key (the server cannot gain anything from it but
  a denial of service, which it has anyway).
- The installer rotates the key on a fresh enrolment (an unbound install
  token), so a machine enrolled anew, into another agent row or tenant, never
  keeps a key an old row trusted. A reinstall with a bound token keeps it.

### Key trust (no manual step)

The server learns the key from the heartbeat and trusts the first one it sees
for a probe (trust on first use, audited as `probe.seal_key.trust`). Columns
on `agents`: `seal_public_key` (trusted), `seal_key_reported` (last
reported), `seal_key_trusted_at`, `seal_key_trusted_by` (`NULL` = automatic).

When the reported key differs from the trusted one, the probe is shown as
"key changed": the form shows both fingerprints and seals nothing until an
admin clicks "Trust the new key" (audited). Logins sealed to the old key are
marked "sealed to an old key, enter again" and refused for deploys.

Why no mandatory out-of-band fingerprint check: it would be the manual step
the owner wants gone, and it buys little: an attacker who controls the SaaS
can read the plaintext through the JavaScript anyway. The fingerprint is
shown so a careful admin can compare it with `nodeglow-agent seal-key`.

### Local opt-in, without a manual step

`deploy.json` gets `allow_sealed_credentials` (default false). Sealed logins
are used only when both `allow_deploy_agents` and `allow_sealed_credentials`
are true. Existing `deploy.json` files with local logins keep their behaviour
(sealed off) until changed.

How it gets set without a shell on the probe:

- **Install command:** the "Add agent" dialog (Linux command) gets a checkbox
  "This machine is a probe and may install agents on other hosts". The
  install token gets a `probe` flag (column on `agent_install_tokens`); the
  install URL gets `&deploy=1`; the installer script runs
  `/opt/nodeglow/nodeglow-agent enable-deploy` after placing the binary, and
  the enrolment with a `probe` token sets `agents.is_probe`. No admin step on
  the server afterwards.
- **`nodeglow-agent enable-deploy`** sets both flags in `deploy.json`, keeping
  any local credentials, creates the file root:root 0600 when absent, makes
  sure `seal.key` exists, and prints the seal-key fingerprint.
  `disable-deploy` sets both false.
- **Existing probes:** the same checkbox on the reinstall command, or the one
  command `sudo /opt/nodeglow/nodeglow-agent enable-deploy`. The empty-state
  text of the probe form says so.

The server cannot set either flag: the heartbeat response has no field for it,
and the install script only runs where a person ran the install command.

### Sealing

HPKE (RFC 9180), base mode, suite DHKEM(X25519, HKDF-SHA256),
HKDF-SHA256, ChaCha20-Poly1305. `info` = `nodeglow sealed credential v1`,
`aad` = the key fingerprint (`SHA256:…`) as UTF-8, binding the ciphertext to
the key it names. Libraries: Rust `hpke` (pure Rust, x25519 feature), browser
`@hpke/core` with `@hpke/dhkem-x25519` and `@hpke/chacha20poly1305` (pure JS,
no WebCrypto X25519 needed). Interoperability is tested with a fixture sealed
by the frontend code and opened by the agent tests.

Plaintext (JSON, at most 16 KiB):

```json
{"v": 1, "id": "<uuid from the browser>", "name": "lab-root",
 "username": "root",
 "password": "…" | null,
 "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----…" | null,
 "passphrase": "…" | null,
 "hosts": ["10.0.20.0/24", "*.lab.example"]}
```

Exactly one of `password` and `private_key`. `hosts` uses the same syntax and
matching as `deploy.json` and must not be empty.

Envelope (what is stored and sent):

```json
{"v": 1, "suite": "hpke-x25519-sha256-chacha20poly1305",
 "key": "SHA256:…", "enc": "<b64>", "ct": "<b64>"}
```

`key` names the probe key it was sealed to, so the server can mark stale
logins and the probe can say "sealed to another key" instead of a generic
error.

### Server storage

New table `probe_credentials` (tenant-owned, RLS, migration 058):
`id`, `probe_id` (FK agents, cascade), `name` (unique per probe, same name
rules as today), `username`, `auth` (`password` | `key`), `hosts` (JSON, for
display only), `key_fingerprint`, `sealed` (envelope, encrypted under the
tenant key), `created_at`, `created_by`, `last_used_at`.

`username`, `auth` and `hosts` are stored readable so the UI can show them;
the probe never uses them. It uses only what is inside the envelope, so a
server that edits these columns changes what the UI shows, not what the probe
does.

Sealed names may not collide with a local credential name of the same probe
(refused with 409), so a log line names one login unambiguously.

### Delivery

- `deploy_agent` payload: either `credential` (local name, as today) or
  `sealed_credential` (the envelope), never both.
- `ssh_host_key` payload: optional `sealed_credential`. The probe answers for
  a host allowed by a local credential, or by the host list inside the given
  envelope (after opening it). Without either it refuses, as today.
- New op `rotate_seal_key {}` → `{public_key}`: the probe replaces
  `seal.key` (atomically) and answers with the new public key. Allowed only
  with `allow_sealed_credentials`.
- The command payload stays encrypted under the tenant key and is cleared on
  delivery (unchanged). `agent_commands` gets `credential_id` (nullable) so
  deleting a login expires queued commands that carry it.

### What the probe does with a sealed login

1. Refuse unless `allow_deploy_agents` and `allow_sealed_credentials`.
2. Open the envelope with `seal.key`. Wrong `key` or failed open: "This login
   was sealed to another key of this probe; enter it again."
3. Check the plaintext (version, exactly one secret, hosts non-empty, size).
4. Refuse unless the target host matches the envelope's `hosts`.
5. Throttle (in memory, sealed logins only; local `deploy.json` logins are
   not affected), for `deploy_agent` and `ssh_host_key` alike:
   - at most 20 distinct target hosts per rolling hour;
   - after 5 failures within 15 minutes (login refused, no SSH server, host
     key mismatch), refuse sealed operations for 15 minutes.
   An LXC run of many containers on one node counts as one host. This limits
   a compromised server that seals its own logins from scanning or
   brute-forcing the customer network through the probe.
6. Continue exactly as with a local credential (part C procedure). A private
   key is parsed from memory. Plaintext buffers are zeroised after use; the
   secret never appears in a log line or result.
7. The result names the login (`name`), never its contents.

### API (admin session, not support access, rate limited, audited)

- `GET /api/probes/{probe_id}/credentials`: sealed logins of the probe
  (`id, name, username, auth, hosts, key_fingerprint, stale, created_at,
  created_by, last_used_at`), never the envelope.
- `POST /api/probes/{probe_id}/credentials`
  `{name, username, auth, hosts, sealed}` → 201. Refused when the probe does
  not allow sealed logins, has no trusted key, the envelope names another key,
  the envelope is malformed or too large, or the name is taken.
- `DELETE /api/probes/{probe_id}/credentials/{id}`: deletes and expires queued
  commands carrying it.
- `POST /api/probes/{probe_id}/seal-key/trust` `{fingerprint}`: trust the
  reported key; the fingerprint must match what the probe reports now.
- `POST /api/probes/{probe_id}/seal-key/rotate`: queue `rotate_seal_key`;
  the probe answers with the new public key, the server trusts it and marks
  every sealed login of the probe stale.
- `POST /api/probes/{probe_id}/deploy` and `/host-key` accept
  `sealed_credential_id` instead of `credential`. Refused with
  `credential_stale` when its key is not the trusted one.
- The agent list's `deploy` object gains `sealed` (bool, the probe's opt-in)
  and `seal: {fingerprint, state: "trusted" | "changed" | "none"}`;
  `deployProbes` offers a probe when `enabled`.

Audit actions: `probe.credential.create`, `probe.credential.delete`,
`probe.seal_key.trust`; `agent.deploy` names the login (`credential` or
`sealed_credential` name and id).

### UI

- Deploy form, "Login" select: two groups, "On the probe" (local names) and
  "Saved in Nodeglow" (sealed, with username), plus "Add a login…" when the
  probe allows sealed logins.
- "Add a login": name, username, password or private key (textarea, file
  picker), passphrase for a key, hosts (prefilled with the target host). Shows
  the probe key fingerprint with a note how to compare it. Sealing happens in
  the browser on save; the secret is cleared from state afterwards.
- Saved logins can be deleted from the same place (small list under the
  select, with host list and "sealed to an old key" marker).
- "Key changed" banner with both fingerprints and "Trust the new key".
- "Revoke all sealed logins" (rotates the probe key), with a confirmation.
- Add-agent dialog: the probe opt-in checkbox for the Linux command.

### Docs

OPERATIONS.md: "Deploying agents from a probe" gets the sealed mode, the
opt-in, `enable-deploy`, `seal-key`, rotation, and the threat-model note. The
probe spec's credentials section points here for deploy logins.

## Delivery

One PR, commits per layer: backend (migration 058, model, routes, heartbeat,
installer flag), agent (seal key, CLI, unseal, throttle, payload variants),
frontend (sealing, form, dialog), docs.

## Calls made in review (owner may override)

- The install checkbox marks the machine as a probe (token flag).
- Deleting removes the server copy only; real revocation is key rotation,
  offered as a button; a fresh enrolment rotates the key.
- On-prem is not forced to release-signed updates; the limit is documented.
- Throttle numbers (20 hosts/hour, 5 failures/15 min).

## Left to the builder

Exact UI layout within the existing components, the zeroising mechanism,
error wording, crate and package minor versions.

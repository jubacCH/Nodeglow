# Sealed deploy credentials for probes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin enters an SSH login in the UI; the browser seals it to one probe's X25519 key (HPKE); the server stores and forwards only ciphertext; the probe opens it in memory, enforces the sealed host list and a throttle, and deploys as in part C.

**Architecture:** Backend: migration 058 (table `probe_credentials`, seal-key columns on `agents`, `credential_id` on `agent_commands`, `probe` on `agent_install_tokens`), heartbeat learns the probe key (TOFU), new routes under `/api/probes/{id}/…`, deploy/host-key accept `sealed_credential_id`, installer opt-in. Agent: new `seal.rs` (key file, fingerprint, HPKE open, plaintext checks, throttle), `deploy.rs` gains the sealed path and `rotate_seal_key`, CLI subcommands. Frontend: `lib/seal.ts` (HPKE seal), login form inside the probe deploy form, probe checkbox in the add-agent dialog.

**Tech Stack:** FastAPI async, SQLAlchemy, Alembic, Postgres RLS, pytest · Rust (tokio, russh 0.65.1, `hpke` 0.13 features `x25519`, `zeroize`) · Next.js 15, vitest, `@hpke/core`, `@hpke/dhkem-x25519`, `@hpke/chacha20poly1305`.

**Spec:** `docs/specs/2026-11-19-probe-sealed-credentials-design.md` (read it first; it decides the why).

## Global Constraints

- Suite string, exactly: `hpke-x25519-sha256-chacha20poly1305`. HPKE base mode, KEM DHKEM(X25519, HKDF-SHA256), KDF HKDF-SHA256, AEAD ChaCha20-Poly1305.
- `info` = UTF-8 `nodeglow sealed credential v1`; `aad` = UTF-8 of the key fingerprint (`SHA256:…`).
- Fingerprint = `"SHA256:"` + base64 **without padding** of SHA-256 over the 32 raw public-key bytes. All other base64 (public key, `enc`, `ct`, `seal.key` contents) is standard alphabet **with padding**.
- Envelope: `{"v":1,"suite":…,"key":"SHA256:…","enc":"<b64>","ct":"<b64>"}`; serialized at most 32768 bytes. Plaintext JSON at most 16384 bytes.
- Plaintext: `{"v":1,"id","name","username","password"|null,"private_key"|null,"passphrase"|null,"hosts":[…]}`; exactly one of `password`/`private_key`; `hosts` 1–64 entries, each ≤ 255 chars; `name` matches `^[A-Za-z0-9_.@-]{1,64}$`.
- Throttle (probe, sealed only, `deploy_agent` and `ssh_host_key`): ≤ 20 distinct target hosts per rolling hour; 5 failures within 15 min → refuse sealed ops for 15 min. Failure = connect/login/host-key error (anything `deploy::connect` or `fetch_host_key` returns as `Err`).
- `seal.key` and `deploy.json`: root:root 0600, next to `config.json`, written atomically (temp file + rename).
- New audit actions: `probe.credential.create`, `probe.credential.delete`, `probe.seal_key.trust`, `probe.seal_key.rotate`.
- Routes: admin session, not support access (`_require_access_admin`), rate limited.
- Never log, return or store in clear: password, private key, passphrase, plaintext, `seal.key`.
- Commits as `jubacCH <167111258+jubacCH@users.noreply.github.com>`, no AI attribution anywhere.
- Code comments English; user-facing copy English, matching existing tone.

## Review Focus

1. A probe with an older agent (no `seal` in heartbeat) — the UI must not offer "Add a login" and the server must refuse creating one (`seal_key_missing`), not crash. → test in Task 3.
2. Probe key changes while logins exist — those logins show `stale: true` and deploys with them get 409 `credential_stale`; new key not usable until trusted. → tests in Tasks 2 and 4.
3. Deleting a login while a deploy command carrying it is still queued — the command expires and its job fails with a clear detail. → test in Task 3.
4. A pasted private key with Windows line endings or trailing whitespace — the probe must still parse it. → test in Task 7.
5. Heartbeat reporting garbage in `seal` (wrong length, not base64, huge) — ignored, the stored key untouched. → test in Task 2.

---

### Task 1: Migration 058 and models

**Files:**
- Create: `backend/alembic/versions/058_probe_sealed_credentials.py`
- Create: `backend/models/probe_credential.py`
- Modify: `backend/models/agent.py` (columns), `backend/models/agent_deploy.py` (`AgentCommand.credential_id`), `backend/models/agent_install_token.py` (`probe`), `backend/models/__init__.py` (register model if models are imported there)
- Test: existing `backend/tests/test_alembic_heads.py`, `test_tenancy_classification.py`, `test_migrations_tenancy_pg.py`

**Interfaces:**
- Produces:
  - `ProbeCredential(TenantScoped, Base)`, table `probe_credentials`: `id`, `probe_id` (int, not null, `tenant_fk(... "agents", ondelete="CASCADE")`), `name` String(64), `username` String(255), `auth` String(16) (`password`|`key`), `hosts` Text (JSON list), `key_fingerprint` String(64), `sealed` Text (tenant-key encrypted envelope), `created_at`, `created_by`, `last_used_at`; unique index `uq_probe_credentials_name` on (`tenant_id`, `probe_id`, `name`).
  - `Agent.seal_public_key` Text, `Agent.seal_key_reported` Text, `Agent.seal_key_trusted_at` DateTime, `Agent.seal_key_trusted_by` String(255) — all nullable.
  - `AgentCommand.credential_id` Integer nullable (no FK: the row may be deleted).
  - `AgentInstallToken.probe` Boolean not null default false.

- [ ] **Step 1:** Write the migration following `057_probe_agent_deploy.py` exactly (idempotent `_has_column`/table checks, RLS policy for the new table the way 057 and earlier tenant tables do it, downgrade). Add the model file mirroring `models/agent_deploy.py` style.
- [ ] **Step 2:** Run `cd backend && python -m pytest -q tests/test_alembic_heads.py tests/test_tenancy_classification.py` — expected: classification test fails if the new table is not classified; classify it as tenant-owned where that test's list lives, then PASS.
- [ ] **Step 3:** If Docker/Postgres is available locally, run `python -m pytest -q -rs tests/test_migrations_tenancy_pg.py tests/test_pg_rls.py`; otherwise note it for CI.
- [ ] **Step 4:** Commit `feat(backend): migration 058 for sealed probe credentials`.

### Task 2: Heartbeat learns the seal key; agent view

**Files:**
- Modify: `backend/services/agent_commands.py`, `backend/routers/api_v1.py:1159` (`_deploy_view`)
- Create: `backend/services/seal_keys.py`
- Test: `backend/tests/test_routers/test_probe_seal_keys.py`

**Interfaces:**
- Consumes: Task 1 columns.
- Produces (`services/seal_keys.py`):
  - `SUITE = "hpke-x25519-sha256-chacha20poly1305"`
  - `fingerprint(public_key_b64: str) -> str | None` (None when not 32 bytes of valid base64)
  - `parse_report(raw) -> str | None` — returns the canonical base64 public key from `{"suite": SUITE, "public_key": …}`, else None.
  - `state(agent) -> dict` → `{"fingerprint": str|None, "reported_fingerprint": str|None, "state": "trusted"|"changed"|"none"}`
  - `async observe(db, agent, public_key: str | None) -> None` — sets `seal_key_reported`; when `seal_public_key` is NULL and a key is reported: trust it (`seal_key_trusted_at=now`, `trusted_by=None`) and `log_action(db, None, "probe.seal_key.trust", "agent", agent.id, agent.name, {"fingerprint": …, "automatic": True})`.
  - `trust(agent, public_key: str, by: str | None) -> None`.
- `sanitize_capability` additionally keeps `"sealed": raw.get("sealed") is True`; `capability()` default gains `"sealed": False`. `handle_heartbeat` calls `seal_keys.observe(db, agent, seal_keys.parse_report((body.get("deploy") or {}).get("seal")))` when `"deploy"` is in body.
- `_deploy_view` returns `{**capability(agent), "seal": seal_keys.state(agent)}` for probes.

- [ ] **Step 1: Write failing tests** (style of `test_probe_commands.py`, heartbeat via the agent client fixture used there):
  - `test_first_reported_key_is_trusted_and_audited` — after one heartbeat with a valid key: `seal_public_key == reported`, audit row `probe.seal_key.trust` exists, view `state == "trusted"`, fingerprint matches `"SHA256:" + b64nopad(sha256(raw))`.
  - `test_changed_key_is_reported_not_trusted` — second heartbeat with another key: `seal_public_key` unchanged, `state == "changed"`, both fingerprints in the view.
  - `test_garbage_seal_report_is_ignored` — values `{"suite": SUITE, "public_key": "abc"}`, wrong suite, 33-byte key, a 100 kB string, a list: stored key untouched, no exception.
  - `test_sealed_flag_only_when_true` — `"sealed": "true"` → `False`.
- [ ] **Step 2:** Run `python -m pytest -q tests/test_routers/test_probe_seal_keys.py` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new tests plus `tests/test_routers/test_probe_commands.py tests/test_routers/test_probe_deploy.py` → PASS.
- [ ] **Step 5:** Commit `feat(backend): probes report their sealing key, trusted on first use`.

### Task 3: Credentials and seal-key routes

**Files:**
- Create: `backend/routers/probe_credentials.py` (register in `backend/main.py` next to `probe_deploy_router`)
- Modify: `backend/services/agent_commands.py` (`OPS` += `rotate_seal_key`; `apply_results` hook)
- Test: `backend/tests/test_routers/test_probe_credentials.py`

**Interfaces:**
- Consumes: Task 2 `seal_keys.*`; `probe_deploy._guard`, `_refuse`, `_body`; `agent_commands.enqueue`.
- Produces:
  - `GET /api/probes/{probe_id}/credentials` → `{"credentials": [{id, name, username, auth, hosts, key_fingerprint, stale, created_at, created_by, last_used_at}]}` (`stale = key_fingerprint != seal_keys.fingerprint(probe.seal_public_key)`).
  - `POST /api/probes/{probe_id}/credentials` `{name, username, auth, hosts, sealed}` → 201 `{credential: …}`. Errors (409 unless noted, `code` field): `not_a_probe`, `deploy_not_enabled`, `sealed_not_enabled` (capability `sealed` false), `seal_key_missing`, `seal_key_changed` (state `changed`), `wrong_key` (envelope `key` ≠ trusted fingerprint), 400 `invalid_envelope` (shape per Global Constraints, `enc` 32 bytes, size), 400 `invalid_name`, 400 `invalid_hosts`, `name_taken` (sealed row exists **or** name in capability `credentials`).
  - `DELETE /api/probes/{probe_id}/credentials/{id}` → 200; sets every `queued` `AgentCommand` with that `credential_id` to `expired`, clears its payload, fails its jobs with detail "The login was deleted before the probe picked up the command."
  - `POST /api/probes/{probe_id}/seal-key/trust` `{fingerprint}` → 200; 409 `fingerprint_mismatch` unless it equals the reported one.
  - `POST /api/probes/{probe_id}/seal-key/rotate` → 202 `{command_id}`; requires capability `sealed`.
  - `agent_commands.validate_envelope(raw) -> dict | None` (shape check only, used here and in Task 4).
  - In `apply_results`: an `ok` answer to `rotate_seal_key` with `result.public_key` valid → `seal_keys.trust(probe, key, cmd.created_by)`; the stored result keeps only the fingerprint.
  - `sealed` stored via `tenant_keys.encrypt_secret(db, json.dumps(envelope), tenant_id=probe.tenant_id)`.

- [ ] **Step 1: Write failing tests:** create/list (no `sealed` field in any response), each error code above, delete expires a queued command with that `credential_id` and fails its job, stale after key change, trust with wrong fingerprint → 409, rotate result trusts the new key and makes existing rows stale, support-access session refused (copy the support-access test pattern from `test_probe_deploy.py`), tenant isolation (a second tenant cannot list/delete — follow `test_tenant_isolation.py` pattern), `test_probe_without_seal_key_refuses_create` (Review Focus 1).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(backend): sealed logins per probe, key trust and rotation`.

### Task 4: Deploy and host-key with a sealed login

**Files:**
- Modify: `backend/routers/probe_deploy.py`
- Test: `backend/tests/test_routers/test_probe_deploy.py` (add)

**Interfaces:**
- Consumes: Task 3 rows, `seal_keys.state`.
- Produces: both routes accept `sealed_credential_id` (int) instead of `credential`; both given or neither → 400 `invalid_credential`. Sealed row missing → 400 `unknown_credential`; stale → 409 `credential_stale`; capability `sealed` false → 409 `sealed_not_enabled`. Payload carries `"sealed_credential": <envelope>` (decrypted from the row) and no `credential`; `AgentCommand.credential_id` set; `last_used_at` updated on deploy. Audit `agent.deploy` details: `credential` (local) or `sealed_credential: {"id", "name"}`. `ask_host_key` sets `credential_id` too.

- [ ] **Step 1: Failing tests:** `test_deploy_with_sealed_login_carries_envelope` (payload has envelope, no `credential`, `credential_id` set), `test_host_key_with_sealed_login`, `test_both_credentials_refused`, `test_stale_sealed_login_refused` (Review Focus 2), `test_sealed_refused_when_probe_did_not_opt_in`.
- [ ] **Step 2–4:** Run (FAIL) → implement → run whole `tests/test_routers/test_probe_*.py` (PASS).
- [ ] **Step 5:** Commit `feat(backend): deploy through a probe with a sealed login`.

### Task 5: Probe opt-in through the install command

**Files:**
- Modify: `backend/routers/agents.py` (`issue_install_token`, `create_install_token`, `reinstall_agent`, enrolment near line 257–385, `render_linux_installer`, `install_linux`, `_install_token_response`), `backend/services/ssh_deploy.py` / `agent_deploy.py` callers of `render_linux_installer` if signature changes
- Test: `backend/tests/test_routers/test_install_probe_optin.py`

**Interfaces:**
- Produces: `issue_install_token(..., probe: bool = False)`; `POST /api/agents/install-tokens` and `/api/agents/{id}/reinstall` accept `probe: bool` (audited in details); enrolment with a `probe` token sets `agent.is_probe = True` (new and re-enrolled agents). `render_linux_installer(server_url, enrollment_key, *, probe: bool = False, fresh: bool = True) -> str`:
  - `fresh` (token not bound to an agent): `rm -f "$INSTALL_DIR/seal.key"` before the service starts.
  - `probe`: after binary and config are in place, before `systemctl restart`: `if [ -x "$INSTALL_DIR/nodeglow-agent" ]; then "$INSTALL_DIR/nodeglow-agent" --config "$CONFIG_FILE" enable-deploy || echo "  Warning: could not enable agent deploys on this probe"; fi`.
  - `/install/linux` derives both from the token row.
- [ ] **Step 1: Failing tests:** script for a probe token contains `enable-deploy`; for a normal token not; `seal.key` removal present for unbound, absent for bound token; enrolment with probe token → `is_probe` true; token API returns/accepts `probe`.
- [ ] **Step 2–4:** FAIL → implement → `python -m pytest -q tests` (full backend) PASS; `ruff check .` clean.
- [ ] **Step 5:** Commit `feat(backend): the install command can make a machine a probe that deploys`.

### Task 6: Browser sealing and the interop fixture

**Files:**
- Modify: `frontend/package.json` (+ lock): `@hpke/core`, `@hpke/dhkem-x25519`, `@hpke/chacha20poly1305` (pinned exact current versions)
- Create: `frontend/src/lib/seal.ts`, `frontend/src/lib/seal.test.ts`, `agent/tests/fixtures/sealed_v1.json`

**Interfaces:**
- Produces (`lib/seal.ts`):
  - `export const SEAL_SUITE = 'hpke-x25519-sha256-chacha20poly1305'`
  - `export interface LoginPlaintext { v: 1; id: string; name: string; username: string; password: string | null; private_key: string | null; passphrase: string | null; hosts: string[] }`
  - `export interface Envelope { v: 1; suite: string; key: string; enc: string; ct: string }`
  - `export async function sealFingerprint(publicKeyB64: string): Promise<string>`
  - `export async function sealLogin(publicKeyB64: string, login: LoginPlaintext): Promise<Envelope>` — throws on a key that is not 32 bytes or plaintext > 16384 bytes.
- Fixture JSON: `{"private_key": "<b64 32 bytes>", "public_key": "<b64>", "plaintext": {…}, "envelope": {…}}` with a password login and hosts `["10.0.20.0/24","*.lab.example"]`.

- [ ] **Step 1: Failing tests:** `sealFingerprint` of a known key equals the value computed with Node `crypto` in the test; `sealLogin` round-trips (open with `@hpke/core` using `info`/`aad` from Global Constraints and the recipient private key); wrong `aad` fails to open; the committed fixture opens to its `plaintext`. A test guarded by `process.env.WRITE_SEAL_FIXTURE === '1'` (skipped otherwise) writes the fixture.
- [ ] **Step 2:** `cd frontend && npm i … && WRITE_SEAL_FIXTURE=1 npx vitest run src/lib/seal.test.ts` writes the fixture; then `npm run test` → PASS; `npx tsc --noEmit`; `npm run lint`.
- [ ] **Step 3:** Commit `feat(frontend): seal logins to a probe key (HPKE)`.

### Task 7: Agent seal module

**Files:**
- Modify: `agent/Cargo.toml` (`hpke = { version = "0.13", default-features = false, features = ["x25519", "alloc"] }`, `zeroize = "1"`, `base64` if not already transitively usable — add `base64 = "0.22"`), `agent/src/main.rs` (`mod seal;`)
- Create: `agent/src/seal.rs`

**Interfaces:**
- Produces:
  - `pub const SUITE: &str`, `pub fn key_path_for(config_path: &Path) -> PathBuf` (`seal.key` next to config)
  - `pub struct SealKey` (private key bytes in `Zeroizing<[u8; 32]>`, public key) with `load(path, require_root: bool) -> anyhow::Result<Option<SealKey>>` (None when absent; Err when not private/owned by root), `load_or_create(path, require_root) -> anyhow::Result<SealKey>`, `rotate(path, require_root) -> anyhow::Result<SealKey>`, `public_b64(&self) -> String`, `fingerprint(&self) -> String`, `report(&self) -> serde_json::Value` (`{"suite", "public_key"}`).
  - `pub struct SealedLogin { pub id: String, pub name: String, pub credential: deploy::Credential }` with `Drop` zeroizing secrets.
  - `pub fn open(key: &SealKey, envelope: &serde_json::Value) -> Result<SealedLogin, OpenError>`; `OpenError::{WrongKey, Malformed, Invalid(String)}` with user-facing `Display` ("This login was sealed to another key of this probe; enter it again." for `WrongKey`/decrypt failure).
  - `pub struct Throttle` with `admit(&mut self, host: &str, now: Instant) -> Result<(), String>` and `failed(&mut self, now: Instant)`; values from Global Constraints.
- `deploy::Credential` gains `#[serde(skip)] pub private_key: Option<String>`; `deploy::connect` uses `russh::keys::decode_secret_key(pem.trim().replace("\r\n", "\n"), passphrase)` when set.

- [ ] **Step 1: Failing tests (in `seal.rs`):** `opens_frontend_fixture` (`include_str!("../tests/fixtures/sealed_v1.json")`, decoded plaintext equals fixture), `fingerprint_matches_fixture_style`, `wrong_key_is_wrong_key`, `tampered_ct_is_wrong_key`, `rejects_two_secrets`, `rejects_empty_hosts`, `rejects_oversized`, `key_file_created_0600_and_reloaded`, `key_file_world_readable_refused`, `rotate_changes_fingerprint`, `throttle_twenty_hosts_per_hour` (21st new host refused, a repeat host admitted, admitted again after 1 h), `throttle_blocks_after_five_failures` (blocked 15 min, then admitted), `crlf_private_key_parses` (Review Focus 4: generate an ed25519 key with `russh::keys` in-test, encode OpenSSH, add `\r\n` and trailing spaces, `decode_secret_key` path succeeds).
- [ ] **Step 2:** `cd agent && cargo test seal` → FAIL. **Step 3:** Implement. **Step 4:** `cargo test` → PASS; `cargo clippy --all-targets` clean.
- [ ] **Step 5:** Commit `feat(agent): probe sealing key and opening sealed logins`.

### Task 8: Agent opt-in, CLI, sealed commands

**Files:**
- Modify: `agent/src/deploy.rs`, `agent/src/config.rs`, `agent/src/main.rs`

**Interfaces:**
- Consumes: Task 7.
- Produces:
  - `DeployFile.allow_sealed_credentials: bool` (`#[serde(default)]`); `pub fn sealed_enabled(&self) -> bool` (both flags).
  - `capability(file: Option<&DeployFile>, seal: Option<&SealKey>) -> Value` adds `"sealed": bool` and, whenever a key exists, `"seal": key.report()` (also when deploying is off).
  - `ProbeContext` gains `seal_key_path: PathBuf`, `throttle: Arc<std::sync::Mutex<seal::Throttle>>`.
  - `DeployPayload` / `HostKeyPayload`: `credential: Option<String>`, `sealed_credential: Option<Value>`; for deploy exactly one, else "Invalid deploy command."; sealed path: refuse unless `sealed_enabled()` ("Sealed logins are not switched on on this probe (deploy.json)."), open, check `allows(host)`, `throttle.admit(host)`, run, `throttle.failed()` on a connect/login/host-key error. Result detail names the login (`name`), never secrets. Host-key: allowed if a local credential allows the host or the opened envelope does.
  - op `rotate_seal_key`: only with `sealed_enabled()`; `seal::SealKey::rotate`; result `{"public_key": b64}`.
  - CLI (`config.rs`): `#[command(subcommand)] command: Option<CliCommand>`; `pub enum CliCommand { EnableDeploy, DisableDeploy, SealKey { #[arg(long)] rotate: bool } }`; exposed as `Config.command` (`#[serde(skip)]`). `main` handles it right after `Config::load` and exits: `enable-deploy` (must be root on Unix) reads `deploy.json` as `serde_json::Value` (keeps other keys/credentials), sets `allow_deploy_agents` and `allow_sealed_credentials` true, writes atomically 0600, `SealKey::load_or_create`, prints `Seal key fingerprint: SHA256:…`; `disable-deploy` sets both false; `seal-key [--rotate]` prints the fingerprint. Config loading must not require `server` for these subcommands (if `Config::load` errors without a server, handle the subcommand before validation).
  - `main` loads `seal::SealKey::load_or_create` once at start on Unix probes (log fingerprint), re-reads `SealKey::load` each interval for the report.

- [ ] **Step 1: Failing tests (in `deploy.rs` tests):** `sealed_refused_without_flag`, `sealed_host_not_in_list_refused`, `both_credentials_refused`, `capability_reports_seal_and_sealed_flag`, `enable_deploy_keeps_local_credentials` (temp dir, existing file with a credential → flags true, credential still there, mode 0600), `disable_deploy_turns_both_off`, `rotate_refused_without_flag`, `throttle_counts_failed_sealed_connect` (unreachable `127.0.0.1:1` five times with a fixture-sealed login → sixth refused with the throttle message; build the envelope in-test with the `hpke` crate for a temp key).
- [ ] **Step 2:** `cargo test` → FAIL. **Step 3:** Implement. **Step 4:** `cargo test`, `cargo clippy --all-targets`, `cargo build --release` → PASS.
- [ ] **Step 5:** Commit `feat(agent): deploy with sealed logins, enable-deploy and seal-key commands`.

### Task 9: UI

**Files:**
- Modify: `frontend/src/types/index.ts` (`deploy` gains `sealed: boolean; seal?: { fingerprint: string | null; reported_fingerprint: string | null; state: 'trusted' | 'changed' | 'none' }`), `frontend/src/lib/probeDeploy.ts`, `frontend/src/app/(app)/agents/_components/ProbeDeployForm.tsx`, `frontend/src/app/(app)/agents/_components/AddAgentDialog.tsx`
- Create: `frontend/src/app/(app)/agents/_components/SealedLoginForm.tsx`, `frontend/src/lib/probeCredentials.ts` (+ `.test.ts`)

**Interfaces:**
- Consumes: Task 6 `sealLogin`, `sealFingerprint`; Task 3/4 routes.
- Produces:
  - `ProbeForm.credential` holds a local name or `sealed:<id>`; `probeDeployBody` sends `sealed_credential_id: <id>` for the latter (and no `credential`). `probeFormProblem` message stays "Pick a login." when empty.
  - `probeCredentials.ts`: `interface ProbeCredential {id; name; username; auth: 'password'|'key'; hosts: string[]; key_fingerprint; stale: boolean; created_at; created_by; last_used_at}`; `loginFormProblem(f: LoginForm, localNames: string[], sealedNames: string[]): string | null`; `parseHosts(text: string): string[]` (split on commas/whitespace, dedupe).
  - `SealedLoginForm` props `{ probe: Agent; defaultHost: string; takenNames: string[]; onSaved(c: ProbeCredential): void; onCancel(): void }`: fields name, username, password | private key (textarea + file input), passphrase (key only), hosts; shows probe fingerprint with "Compare with `sudo /opt/nodeglow/nodeglow-agent seal-key` on the probe."; seals with `crypto.randomUUID()` id, POSTs, then clears secret state.
  - `ProbeDeployForm`: select with `<optgroup>` "On the probe" / "Saved in Nodeglow"; "Add a login…" when `deploy.sealed && seal.state === 'trusted'`; list of saved logins with delete (confirm) and "sealed to an old key" marker; key-changed banner with both fingerprints and "Trust the new key" (POST trust); "Revoke all sealed logins" (confirm, POST rotate). Empty-state copy mentions the probe checkbox on the install command and `sudo /opt/nodeglow/nodeglow-agent enable-deploy`.
  - `deployProbes` unchanged (`is_probe && deploy.enabled`).
  - `AddAgentDialog`: checkbox "This machine is a probe and may install agents on other hosts" for the Linux command; sends `probe: true` with token creation / reinstall.
- [ ] **Step 1: Failing tests (`probeDeploy.test.ts`, `probeCredentials.test.ts`):** body with `sealed:7` → `{sealed_credential_id: 7}` and no `credential`; local name unchanged; `parseHosts('10.0.0.0/24, a.lab  a.lab')` → `['10.0.0.0/24','a.lab']`; `loginFormProblem` for missing secret, both secrets, empty hosts, name taken locally, invalid name.
- [ ] **Step 2–4:** FAIL → implement → `npm run test && npx tsc --noEmit && npm run lint && npm run build` PASS.
- [ ] **Step 5:** Verify in the browser against the dev server if one can run with a seeded probe; otherwise state it as unverified.
- [ ] **Step 6:** Commit `feat(frontend): enter deploy logins sealed to the probe`.

### Task 10: Docs and PR

**Files:**
- Modify: `docs/OPERATIONS.md` (section "Deploying agents from a probe"), `docs/API.md` (new routes), `docs/specs/2026-10-11-probe-side-integrations.md` (one line in "Credentials" pointing to the new spec for deploy logins), `CHANGELOG.md`

- [ ] **Step 1:** Write: sealed mode, opt-in via install checkbox or `enable-deploy`, `seal-key [--rotate]`, revocation semantics, throttle, threat-model note (incl. on-prem limit), API routes.
- [ ] **Step 2:** Full local run: `cd backend && python -m pytest -q tests && ruff check .`; `cd agent && cargo test && cargo clippy --all-targets`; `cd frontend && npm run test && npx tsc --noEmit && npm run lint && npm run build`.
- [ ] **Step 3:** Commit `docs: sealed deploy logins`, push, open PR, wait for green CI.

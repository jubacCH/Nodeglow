# Agent release key

The release key signs every agent binary CI builds (spec
`docs/specs/2026-10-11-probe-side-integrations.md`, rule 4 and P2a). Its
private half exists in exactly two places — the owner's vault and the GitHub
Actions secret `AGENT_RELEASE_SIGNING_KEY` — and **never on a Nodeglow
server**. Its public half is compiled into the agent
(`agent/src/release_keys.rs`), so no server, install script or config file can
make an agent trust another key.

| What | Format |
|---|---|
| Private key (vault, GH secret) | Ed25519, PKCS#8 PEM (`-----BEGIN PRIVATE KEY-----`), unencrypted. The secret may also hold that PEM base64-encoded (one line). |
| Public key (agent) | 64 hex chars, the raw 32-byte Ed25519 public key |
| Key id | first 8 bytes of SHA-256(raw public key), hex (16 chars) — derived, never typed |
| `<binary>.sig` | one line of JSON: `format` `nodeglow-agent-signature-v1`, `alg` `ed25519`, `platform` (`linux`/`windows`), `key_id`, `sha256`, `signature` (128 hex) |
| Signed message | `nodeglow-agent-binary:v1:<platform>:<sha256 hex of the binary>` (pure Ed25519) |

`sign.sh` (needs OpenSSL 3) is the only signing implementation; CI runs it in
`.github/workflows/agent-build.yml` (job `sign`).

## Installing the key (once)

Do this on a trusted machine, not on a server. Needs OpenSSL 3 (Git Bash on
Windows ships it) and the `gh` CLI logged in as a repo admin.

1. **Generate** the key in a private temporary directory:

   ```sh
   umask 077
   openssl genpkey -algorithm ed25519 -out agent-release-key.pem
   ```

2. **Print the public key and key id:**

   ```sh
   sh tools/agent-release-key/sign.sh pubkey agent-release-key.pem
   # -> <64 hex public key> <16 hex key id>
   ```

3. **Store the private key in the vault** (Vaultwarden, e.g. item
   `nodeglow/agent-release-signing-key`, the PEM as a secure note; put the
   public key and key id in the item too).

4. **Set the GitHub Actions secret:**

   ```sh
   gh secret set AGENT_RELEASE_SIGNING_KEY --repo jubacCH/Nodeglow < agent-release-key.pem
   ```

   Optional hardening: create an environment (e.g. `agent-release`) restricted
   to `main`, store the secret there and add `environment: agent-release` to
   the `sign` job — then only builds of `main` can sign.

5. **Paste the public key** into `agent/src/release_keys.rs`: replace the
   string `"REPLACE WITH RELEASE KEY"` in `RELEASE_PUBLIC_KEYS` with the
   64-hex value from step 2 (keep the quotes and the trailing comma). That is
   the only place. Commit and push to `main`; the agent build signs the new
   binaries and commits them together with `backend/static/*.sig`.

6. **Delete** the local PEM file (`shred -u agent-release-key.pem` or delete
   it and empty the trash).

7. **Check** the run of "Build Rust Agent" on `main`: the summary says
   "release-signed (key <id>)". A server running that commit reports
   `release_key_id` in `GET /api/agent/version/linux`.

Order matters: the secret without the pasted public key makes the `sign` job
fail on purpose (CI never signs with a key the binaries do not trust); the
public key without the secret makes the commit to `main` fail on purpose (no
unsigned binaries on main of this repository).

## Rotation

Agents in the field trust only the keys compiled into *their* build.

1. Generate the new key (steps 1–3), **add** its public key as the first entry
   of `RELEASE_PUBLIC_KEYS`, keep the old one. Release (still signed with the
   old key).
2. Once agents run that release, switch the secret to the new key.
3. In a later release remove the old public key.

If the private key leaked: add a new key and remove the leaked one in the same
release, switch the secret, and treat every agent that cannot be updated to it
as needing a reinstall.

## Without the secret

Forks and pull requests from forks have no secret: the build still passes,
the artifact is named `nodeglow-agent-UNSIGNED-dev-build` and contains
`UNSIGNED-DEV-BUILD.txt`. A fork's `main` still commits its unsigned binaries
(and removes stale `.sig` files); this repository's `main` refuses to.

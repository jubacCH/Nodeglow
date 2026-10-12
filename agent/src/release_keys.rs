//! Release public keys compiled into the agent (spec 2026-10-11 rule 4, P2a).
//!
//! Agent updates are signed in CI (`.github/workflows/agent-build.yml`) with the
//! release signing key, whose private half lives only in the owner's vault and
//! in the GitHub Actions secret `AGENT_RELEASE_SIGNING_KEY` — never on a
//! Nodeglow server. The public halves below are the only release trust anchors
//! of this binary: no server response, install script or config file can add,
//! replace or remove one. See tools/agent-release-key/README.md.
//!
//! Format: 64 hex chars, the raw 32-byte Ed25519 public key. The key id the
//! signature files name is derived, never typed: the first 8 bytes of
//! SHA-256(raw public key), hex (16 chars). An entry that is not a valid key —
//! the placeholder in particular — is ignored, i.e. a build with only the
//! placeholder trusts no release key at all and, where release verification is
//! mandatory, refuses every update (fail closed).
//!
//! Rotation: add the new key here (keep the old one), release, wait until the
//! agents run that release, then switch the CI secret to the new key; drop the
//! old key in a later release.

/// The placeholder value. CI refuses to sign a build on `main` while it is
/// still the only entry; see `release_key_configured` in agent-build.yml.
pub const PLACEHOLDER: &str = "REPLACE WITH RELEASE KEY";

/// Trusted release public keys, newest first.
pub const RELEASE_PUBLIC_KEYS: &[&str] = &[
    // REPLACE WITH RELEASE KEY — paste the 64-hex-char public key printed by
    // tools/agent-release-key/README.md, step 2, in place of the placeholder.
    "REPLACE WITH RELEASE KEY",
];

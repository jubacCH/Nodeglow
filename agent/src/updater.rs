use std::path::{Path, PathBuf};

use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tracing::{debug, info, warn};

use crate::client::{ApiClient, VersionResponse};
use crate::config::Config;
use crate::release_keys;

// ── Trust model (spec 2026-10-11 "Signing", rule 4, P2a) ─────────────────────
//
// An update is applied only after its SHA-256 matches the hash the server
// announced (the download was not corrupted) AND one of these holds:
//
//   1. Release signature: CI signed the binary with the release key, whose
//      public half is compiled into this agent (release_keys.rs). Always
//      sufficient. The server only relays the signature; it cannot produce one.
//   2. Local key (single-tenant only): the operator's `update_public_key` from
//      the agent's local config.json verifies the server's `signature` — the
//      pre-P2a, server-held key (services/agent_signing.py), kept for
//      self-built on-prem installs where operator and customer are one party.
//   3. Hash only (single-tenant non-probe agents without `update_public_key`
//      only): the pre-P2a behaviour, kept during the transition.
//
// Which of them count depends on the agent's TrustMode:
//
//   ReleaseOnly  1 only.  Multi-tenant installation (ever reported by the
//                server), or `require_release_signature: true` in config.json.
//   Probe        1 or 2.  The agent is (or ever was) a probe.
//   Standard     1, 2 or 3.
//
// The mode is latched in `update-trust.json` next to config.json: once the
// server said "multi-tenant" or "probe", a later answer saying otherwise never
// relaxes it, so a compromised server cannot talk an agent out of mandatory
// verification. No server response can add or replace a release key.

/// Domain separation for the release signature: the release key signs
/// `nodeglow-agent-binary:v1:<platform>:<sha256 hex of the binary>`, never the
/// raw bytes, so a signature cannot be replayed for another platform or for a
/// different kind of artifact (collector packs will get their own prefix).
pub const RELEASE_MESSAGE_PREFIX: &str = "nodeglow-agent-binary:v1";

/// The exact message CI signs (see .github/workflows/agent-build.yml).
pub fn release_message(platform: &str, sha256_hex: &str) -> String {
    format!("{RELEASE_MESSAGE_PREFIX}:{platform}:{}", sha256_hex.to_ascii_lowercase())
}

/// Key id of a raw Ed25519 public key: first 8 bytes of its SHA-256, hex.
pub fn key_id(public_key: &[u8; 32]) -> String {
    hex::encode(&Sha256::digest(public_key)[..8])
}

/// A release public key this agent trusts.
pub struct TrustedKey {
    pub id: String,
    key: VerifyingKey,
}

/// Parse a list of hex public keys. Entries that are not a valid, non-weak
/// Ed25519 key (the placeholder in particular) are skipped.
pub fn parse_release_keys(list: &[&str]) -> Vec<TrustedKey> {
    list.iter()
        .filter_map(|entry| {
            let bytes = hex::decode(entry.trim()).ok()?;
            let arr: [u8; 32] = bytes.as_slice().try_into().ok()?;
            let key = VerifyingKey::from_bytes(&arr).ok()?;
            if key.is_weak() {
                return None;
            }
            Some(TrustedKey { id: key_id(&arr), key })
        })
        .collect()
}

/// The release keys compiled into this binary.
pub fn compiled_release_keys() -> Vec<TrustedKey> {
    parse_release_keys(release_keys::RELEASE_PUBLIC_KEYS)
}

/// Return the first 8 bytes of a server-provided string for logging without
/// panicking if it is shorter (the value is fully server-controlled).
fn short(s: &str) -> &str {
    s.get(..8).unwrap_or(s)
}

/// True if `s` is a non-empty, even-length lowercase/uppercase hex string.
fn is_hex(s: &str) -> bool {
    !s.is_empty() && s.len() % 2 == 0 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

fn nonempty(v: Option<&str>) -> Option<&str> {
    v.map(str::trim).filter(|s| !s.is_empty())
}

fn decode_signature(signature_hex: &str) -> anyhow::Result<Signature> {
    let sig_bytes = hex::decode(signature_hex.trim())
        .map_err(|_| anyhow::anyhow!("signature is not valid hex"))?;
    let sig_arr: [u8; 64] = sig_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow::anyhow!("ed25519 signature must be 64 bytes"))?;
    Ok(Signature::from_bytes(&sig_arr))
}

/// Verify the legacy (server-key) signature: pure Ed25519 over the raw binary
/// bytes with a hex public key from the local config. Err on any failure.
fn verify_signature(public_key_hex: &str, signature_hex: &str, data: &[u8]) -> anyhow::Result<()> {
    let key_bytes = hex::decode(public_key_hex.trim())
        .map_err(|_| anyhow::anyhow!("update_public_key is not valid hex"))?;
    let key_arr: [u8; 32] = key_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow::anyhow!("update_public_key must be 32 bytes"))?;
    let verifying_key = VerifyingKey::from_bytes(&key_arr)
        .map_err(|e| anyhow::anyhow!("invalid ed25519 public key: {e}"))?;
    if verifying_key.is_weak() {
        anyhow::bail!("update_public_key is a weak ed25519 key");
    }
    let signature = decode_signature(signature_hex)?;
    verifying_key
        .verify_strict(data, &signature)
        .map_err(|e| anyhow::anyhow!("ed25519 signature verification failed: {e}"))
}

/// Verify a release signature over `data` for `platform` with the key named by
/// `key_id`. Returns the key id on success.
pub fn verify_release_signature(
    keys: &[TrustedKey],
    key_id: &str,
    signature_hex: &str,
    platform: &str,
    data: &[u8],
) -> anyhow::Result<String> {
    let key_id = key_id.trim();
    let key = keys
        .iter()
        .find(|k| k.id.eq_ignore_ascii_case(key_id))
        .ok_or_else(|| anyhow::anyhow!("release key {key_id} is not trusted by this agent"))?;
    let signature = decode_signature(signature_hex)?;
    let message = release_message(platform, &hex::encode(Sha256::digest(data)));
    key.key
        .verify_strict(message.as_bytes(), &signature)
        .map_err(|e| anyhow::anyhow!("release signature verification failed: {e}"))?;
    Ok(key.id.clone())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrustMode {
    /// Release signature only; no local key, no hash-only.
    ReleaseOnly,
    /// Release signature or the local `update_public_key`; never hash-only.
    Probe,
    /// Release signature, the local key, or (no local key) hash-only.
    Standard,
}

/// Why an update was accepted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Authorization {
    Release(String),
    LocalKey,
    HashOnly,
}

/// What the server offers for an update, as far as trust is concerned.
pub struct UpdateOffer<'a> {
    pub platform: &'a str,
    pub release_signature: Option<&'a str>,
    pub release_key_id: Option<&'a str>,
    /// Signature by the server-held key (pre-P2a path).
    pub legacy_signature: Option<&'a str>,
}

impl<'a> UpdateOffer<'a> {
    pub fn from_version(platform: &'a str, v: &'a VersionResponse) -> Self {
        UpdateOffer {
            platform,
            release_signature: v.release_signature.as_deref(),
            release_key_id: v.release_key_id.as_deref(),
            legacy_signature: v.signature.as_deref(),
        }
    }
}

/// Decide whether downloaded `data` (hash already checked) may be applied.
pub fn authorize_update(
    mode: TrustMode,
    keys: &[TrustedKey],
    local_key_hex: &str,
    offer: &UpdateOffer,
    data: &[u8],
) -> anyhow::Result<Authorization> {
    if let Some(sig) = nonempty(offer.release_signature) {
        let kid = nonempty(offer.release_key_id).unwrap_or("");
        let known = keys.iter().any(|k| k.id.eq_ignore_ascii_case(kid));
        if known {
            // A release signature by a key we trust must verify — a failure
            // here means tampering, never fall back to a weaker path.
            let id = verify_release_signature(keys, kid, sig, offer.platform, data)?;
            return Ok(Authorization::Release(id));
        }
        if mode == TrustMode::ReleaseOnly {
            anyhow::bail!(
                "update is signed with release key {:?}, which this agent does not trust; refusing",
                kid
            );
        }
        // Single-tenant: a dev build (no or another release key compiled in)
        // cannot check this signature; continue with the local paths.
        debug!("release key {kid:?} unknown to this build, using the local trust path");
    } else if mode == TrustMode::ReleaseOnly {
        anyhow::bail!("update carries no release signature; this agent accepts release-signed updates only");
    }

    let local = local_key_hex.trim();
    if !local.is_empty() {
        return match nonempty(offer.legacy_signature) {
            Some(sig) => {
                verify_signature(local, sig, data)?;
                Ok(Authorization::LocalKey)
            }
            None => anyhow::bail!(
                "update_public_key is configured but the update has no signature for it; refusing"
            ),
        };
    }

    match mode {
        TrustMode::Standard => Ok(Authorization::HashOnly),
        _ => anyhow::bail!(
            "update has no release signature this agent can check and no update_public_key \
             is configured; a probe never applies an unsigned update"
        ),
    }
}

/// Latched trust state, persisted next to config.json. Fields only ever go
/// from false to true.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TrustState {
    #[serde(default)]
    pub multi_tenant: bool,
    #[serde(default)]
    pub probe: bool,
}

impl TrustState {
    pub fn path_for(config_path: &Path) -> PathBuf {
        config_path.with_file_name("update-trust.json")
    }

    /// Load the state. A missing file is the empty state; a file that cannot
    /// be read or parsed fails closed (multi-tenant).
    pub fn load(path: &Path) -> Self {
        match std::fs::read_to_string(path) {
            Ok(s) => serde_json::from_str(&s).unwrap_or_else(|e| {
                warn!("{} is unreadable ({e}); requiring release-signed updates", path.display());
                TrustState { multi_tenant: true, probe: true }
            }),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => TrustState::default(),
            Err(e) => {
                warn!("cannot read {} ({e}); requiring release-signed updates", path.display());
                TrustState { multi_tenant: true, probe: true }
            }
        }
    }

    pub fn save(&self, path: &Path) -> anyhow::Result<()> {
        std::fs::write(path, serde_json::to_string_pretty(self)?)?;
        Ok(())
    }

    /// Fold in what the server said. Returns true if the state changed.
    pub fn latch(&mut self, multi_tenant: bool, probe: bool) -> bool {
        let before = self.clone();
        self.multi_tenant |= multi_tenant;
        self.probe |= probe;
        *self != before
    }

    /// Latch and persist on change (in-memory latch holds even if saving fails).
    pub fn observe(&mut self, path: &Path, multi_tenant: bool, probe: bool) {
        if self.latch(multi_tenant, probe) {
            info!(
                multi_tenant = self.multi_tenant,
                probe = self.probe,
                "Update trust tightened: release-signed updates are now required{}",
                if self.multi_tenant { "" } else { " (or the local update_public_key)" }
            );
            if let Err(e) = self.save(path) {
                warn!("Failed to persist update trust state to {}: {e}", path.display());
            }
        }
    }

    pub fn mode(&self, require_release_signature: bool) -> TrustMode {
        if require_release_signature || self.multi_tenant {
            TrustMode::ReleaseOnly
        } else if self.probe {
            TrustMode::Probe
        } else {
            TrustMode::Standard
        }
    }
}

/// Check for updates and apply if available. Returns true if an update was applied.
pub async fn check_and_update(
    api: &ApiClient,
    cfg: &Config,
    trust: &mut TrustState,
    trust_path: &Path,
) -> anyhow::Result<bool> {
    let platform = if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };

    let version = api.get_version_info(platform).await?;
    // Latch before deciding anything: "multi-tenant" can only tighten.
    trust.observe(trust_path, version.multi_tenant.unwrap_or(false), false);

    let server_hash = version.hash.trim().to_ascii_lowercase();
    debug!("Server hash: {server_hash}");
    if !is_hex(&server_hash) {
        anyhow::bail!("Server returned an invalid version hash");
    }

    let exe_path = std::env::current_exe()?;
    let exe_data = tokio::fs::read(&exe_path).await?;
    let local_hash = hex::encode(Sha256::digest(&exe_data));
    debug!("Local hash: {local_hash}");

    if local_hash == server_hash {
        return Ok(false);
    }

    info!("Update available (local={} server={})", short(&local_hash), short(&server_hash));

    let new_data = api.download_agent(platform).await?;

    let download_hash = hex::encode(Sha256::digest(&new_data));
    if download_hash != server_hash {
        warn!(
            "Download hash mismatch (expected={} got={}), aborting update",
            short(&server_hash),
            short(&download_hash)
        );
        anyhow::bail!("Hash verification failed");
    }

    let mode = trust.mode(cfg.require_release_signature);
    let keys = compiled_release_keys();
    let offer = UpdateOffer::from_version(platform, &version);
    match authorize_update(mode, &keys, &cfg.update_public_key, &offer, &new_data)? {
        Authorization::Release(id) => info!("Update release signature verified (key {id})"),
        Authorization::LocalKey => info!("Update verified with the local update_public_key"),
        Authorization::HashOnly => warn!(
            "Update is not signed for this agent; applying on the SHA-256 check only \
             (single-tenant transition path)"
        ),
    }

    apply_update(&exe_path, &new_data).await?;

    Ok(true)
}

// The agent ships for Linux (musl) and Windows only. Any other platform — macOS
// in particular, where the agent is developed and its tests are run — has no
// self-update path. Refuse explicitly instead of failing to compile there.
#[cfg(not(any(target_os = "linux", target_os = "windows")))]
async fn apply_update(_exe_path: &std::path::Path, _data: &[u8]) -> anyhow::Result<()> {
    anyhow::bail!("Self-update is not supported on {}", std::env::consts::OS)
}

#[cfg(target_os = "linux")]
async fn apply_update(exe_path: &std::path::Path, data: &[u8]) -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let tmp_path = exe_path.with_extension("new");

    // Write to temp file
    tokio::fs::write(&tmp_path, data).await?;

    // Make executable
    let mut perms = tokio::fs::metadata(&tmp_path).await?.permissions();
    perms.set_mode(0o755);
    tokio::fs::set_permissions(&tmp_path, perms).await?;

    // Atomic replace
    tokio::fs::rename(&tmp_path, exe_path).await?;

    info!("Update applied to {}", exe_path.display());
    Ok(())
}

#[cfg(target_os = "windows")]
async fn apply_update(exe_path: &std::path::Path, data: &[u8]) -> anyhow::Result<()> {
    let dir = exe_path.parent().unwrap_or(std::path::Path::new("."));
    let new_path = dir.join("nodeglow-agent.exe.new");
    let old_path = dir.join("nodeglow-agent.exe.old");

    // Write new binary
    tokio::fs::write(&new_path, data).await?;

    // Try direct rename (may fail if running)
    let _ = tokio::fs::remove_file(&old_path).await;
    match tokio::fs::rename(exe_path, &old_path).await {
        Ok(()) => {
            tokio::fs::rename(&new_path, exe_path).await?;
            info!("Update applied via direct rename");
        }
        Err(_) => {
            // Deferred swap: the wrapper batch file will handle it on restart
            info!("Deferred update: .exe.new written, will be swapped on restart");
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    // Produced by the server's signer (Python `cryptography`, as in
    // backend/services/agent_signing.py) with the private key bytes 0..32, so
    // this pins cross-implementation compatibility, not just round-tripping.
    const PUBLIC_KEY: &str = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8";
    const SIGNATURE: &str = "c4c745a721d01c44f45488a8816085565c94dfa0cd90cd5722ab8f8e0b807fd9e873017cd3d9a50eccc4f61a9d96799e66aebbf9fddc1387dd68d55e679a9507";
    const MESSAGE: &[u8] = b"nodeglow-agent test binary v1";

    // Same throwaway key (bytes 0..32, never a real key): key id and a release
    // signature over release_message("linux", sha256(MESSAGE)), made with
    // Python `cryptography` — the format CI produces with openssl.
    const KEY_ID: &str = "56475aa75463474c";
    const RELEASE_SIGNATURE: &str = "cef5b9f712e05917b4b1fee6a61b670191de215f06f2a67c346e0f919ee876c3e98cc82499024551e1e8611acafd1fe200f21cb479e15096c8e4324bad793501";

    fn throwaway(seed: u8) -> (SigningKey, String) {
        let sk = SigningKey::from_bytes(&[seed; 32]);
        let pk_hex = hex::encode(sk.verifying_key().to_bytes());
        (sk, pk_hex)
    }

    fn release_sign(sk: &SigningKey, platform: &str, data: &[u8]) -> String {
        let msg = release_message(platform, &hex::encode(Sha256::digest(data)));
        hex::encode(sk.sign(msg.as_bytes()).to_bytes())
    }

    fn offer<'a>(rel: Option<&'a str>, kid: Option<&'a str>, legacy: Option<&'a str>) -> UpdateOffer<'a> {
        UpdateOffer { platform: "linux", release_signature: rel, release_key_id: kid, legacy_signature: legacy }
    }

    // ── legacy (server-key) signature ────────────────────────────────────────

    #[test]
    fn accepts_a_signature_from_the_server_signer() {
        assert!(verify_signature(PUBLIC_KEY, SIGNATURE, MESSAGE).is_ok());
    }

    #[test]
    fn rejects_tampered_data() {
        assert!(verify_signature(PUBLIC_KEY, SIGNATURE, b"nodeglow-agent test binary v2").is_err());
    }

    #[test]
    fn rejects_a_flipped_signature_byte() {
        let mut sig = SIGNATURE.to_string();
        sig.replace_range(0..2, "c5");
        assert!(verify_signature(PUBLIC_KEY, &sig, MESSAGE).is_err());
    }

    #[test]
    fn rejects_malformed_inputs() {
        assert!(verify_signature("zz", SIGNATURE, MESSAGE).is_err());
        assert!(verify_signature(&PUBLIC_KEY[..62], SIGNATURE, MESSAGE).is_err());
        assert!(verify_signature(PUBLIC_KEY, &SIGNATURE[..126], MESSAGE).is_err());
    }

    #[test]
    fn rejects_a_weak_local_key() {
        // The identity point: a low-order key any signature could satisfy.
        let mut weak = [0u8; 32];
        weak[0] = 1;
        assert!(verify_signature(&hex::encode(weak), SIGNATURE, MESSAGE).is_err());
    }

    #[test]
    fn sha256_hex_matches_known_digest() {
        assert_eq!(
            hex::encode(Sha256::digest(b"abc")),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    // ── release keys ─────────────────────────────────────────────────────────

    #[test]
    fn key_id_is_derived_from_the_public_key() {
        let keys = parse_release_keys(&[PUBLIC_KEY]);
        assert_eq!(keys.len(), 1);
        assert_eq!(keys[0].id, KEY_ID);
    }

    #[test]
    fn placeholder_and_garbage_are_not_keys() {
        let mut weak = [0u8; 32];
        weak[0] = 1;
        let weak_hex = hex::encode(weak);
        assert!(parse_release_keys(&[release_keys::PLACEHOLDER, "", "zz", &PUBLIC_KEY[..62], weak_hex.as_str()]).is_empty());
    }

    #[test]
    fn every_compiled_release_key_is_valid() {
        // Catches a mistyped paste: every entry is either the placeholder or a
        // real key. (A build with only the placeholder trusts nothing.)
        for entry in release_keys::RELEASE_PUBLIC_KEYS {
            if *entry == release_keys::PLACEHOLDER {
                continue;
            }
            assert_eq!(parse_release_keys(&[*entry]).len(), 1, "invalid release key {entry:?}");
        }
        assert!(!release_keys::RELEASE_PUBLIC_KEYS.is_empty());
    }

    #[test]
    fn release_message_format_is_pinned() {
        assert_eq!(release_message("linux", "ABCD"), "nodeglow-agent-binary:v1:linux:abcd");
    }

    #[test]
    fn accepts_a_release_signature_made_outside_rust() {
        let keys = parse_release_keys(&[PUBLIC_KEY]);
        assert_eq!(verify_release_signature(&keys, KEY_ID, RELEASE_SIGNATURE, "linux", MESSAGE).unwrap(), KEY_ID);
    }

    #[test]
    fn release_signature_is_bound_to_platform_and_bytes() {
        let keys = parse_release_keys(&[PUBLIC_KEY]);
        assert!(verify_release_signature(&keys, KEY_ID, RELEASE_SIGNATURE, "windows", MESSAGE).is_err());
        assert!(verify_release_signature(&keys, KEY_ID, RELEASE_SIGNATURE, "linux", b"other").is_err());
        // A raw-bytes signature (the legacy format) is not a release signature.
        assert!(verify_release_signature(&keys, KEY_ID, SIGNATURE, "linux", MESSAGE).is_err());
    }

    #[test]
    fn rotation_picks_the_key_by_id() {
        let (old_sk, old_pk) = throwaway(7);
        let (new_sk, new_pk) = throwaway(9);
        let keys = parse_release_keys(&[new_pk.as_str(), old_pk.as_str()]);
        let data = b"binary";
        for sk in [&old_sk, &new_sk] {
            let id = key_id(&sk.verifying_key().to_bytes());
            let sig = release_sign(sk, "linux", data);
            assert_eq!(verify_release_signature(&keys, &id, &sig, "linux", data).unwrap(), id);
        }
        // Signed by one key but labelled with the other's id: refused.
        let old_id = key_id(&old_sk.verifying_key().to_bytes());
        let sig = release_sign(&new_sk, "linux", data);
        assert!(verify_release_signature(&keys, &old_id, &sig, "linux", data).is_err());
    }

    // ── authorize_update ─────────────────────────────────────────────────────

    #[test]
    fn a_valid_release_signature_is_accepted_in_every_mode() {
        let (sk, pk) = throwaway(7);
        let keys = parse_release_keys(&[pk.as_str()]);
        let id = key_id(&sk.verifying_key().to_bytes());
        let data = b"new agent";
        let sig = release_sign(&sk, "linux", data);
        for mode in [TrustMode::ReleaseOnly, TrustMode::Probe, TrustMode::Standard] {
            let got = authorize_update(mode, &keys, "", &offer(Some(sig.as_str()), Some(id.as_str()), None), data).unwrap();
            assert_eq!(got, Authorization::Release(id.clone()));
        }
    }

    #[test]
    fn a_bad_signature_by_a_trusted_key_never_falls_back() {
        let (sk, pk) = throwaway(7);
        let keys = parse_release_keys(&[pk.as_str()]);
        let id = key_id(&sk.verifying_key().to_bytes());
        let sig = release_sign(&sk, "linux", b"what was signed");
        // Even with a valid local-key signature and in Standard mode.
        let o = offer(Some(sig.as_str()), Some(id.as_str()), Some(SIGNATURE));
        for mode in [TrustMode::ReleaseOnly, TrustMode::Probe, TrustMode::Standard] {
            assert!(authorize_update(mode, &keys, PUBLIC_KEY, &o, MESSAGE).is_err());
        }
    }

    #[test]
    fn release_only_refuses_everything_else() {
        let (_, pk) = throwaway(7);
        let keys = parse_release_keys(&[pk.as_str()]);
        // No release signature, a valid server-key signature + local key: refused
        // (no server-delivered or local key override in multi-tenant mode).
        assert!(authorize_update(TrustMode::ReleaseOnly, &keys, PUBLIC_KEY, &offer(None, None, Some(SIGNATURE)), MESSAGE).is_err());
        // Hash only: refused.
        assert!(authorize_update(TrustMode::ReleaseOnly, &keys, "", &offer(None, None, None), MESSAGE).is_err());
        // Signed by a key this build does not trust: refused.
        let (other, _) = throwaway(9);
        let other_id = key_id(&other.verifying_key().to_bytes());
        let sig = release_sign(&other, "linux", MESSAGE);
        assert!(authorize_update(TrustMode::ReleaseOnly, &keys, PUBLIC_KEY, &offer(Some(sig.as_str()), Some(other_id.as_str()), Some(SIGNATURE)), MESSAGE).is_err());
        // A build with only the placeholder trusts nothing: refused (fail closed).
        let none = parse_release_keys(&[release_keys::PLACEHOLDER]);
        assert!(authorize_update(TrustMode::ReleaseOnly, &none, "", &offer(Some(RELEASE_SIGNATURE), Some(KEY_ID), None), MESSAGE).is_err());
    }

    #[test]
    fn a_probe_accepts_the_local_key_but_never_hash_only() {
        let none = parse_release_keys(&[]);
        assert_eq!(
            authorize_update(TrustMode::Probe, &none, PUBLIC_KEY, &offer(None, None, Some(SIGNATURE)), MESSAGE).unwrap(),
            Authorization::LocalKey
        );
        assert!(authorize_update(TrustMode::Probe, &none, "", &offer(None, None, Some(SIGNATURE)), MESSAGE).is_err());
        assert!(authorize_update(TrustMode::Probe, &none, "", &offer(None, None, None), MESSAGE).is_err());
    }

    #[test]
    fn standard_keeps_the_pre_p2a_behaviour() {
        let none = parse_release_keys(&[]);
        // Local key configured: its signature is required and checked.
        assert_eq!(
            authorize_update(TrustMode::Standard, &none, PUBLIC_KEY, &offer(None, None, Some(SIGNATURE)), MESSAGE).unwrap(),
            Authorization::LocalKey
        );
        assert!(authorize_update(TrustMode::Standard, &none, PUBLIC_KEY, &offer(None, None, None), MESSAGE).is_err());
        assert!(authorize_update(TrustMode::Standard, &none, PUBLIC_KEY, &offer(None, None, Some(SIGNATURE)), b"tampered").is_err());
        // No local key: hash only, as before.
        assert_eq!(
            authorize_update(TrustMode::Standard, &none, "", &offer(None, None, None), MESSAGE).unwrap(),
            Authorization::HashOnly
        );
        // A release signature by a key this (dev) build does not know falls
        // through to the local path instead of blocking the update.
        assert_eq!(
            authorize_update(TrustMode::Standard, &none, PUBLIC_KEY, &offer(Some(RELEASE_SIGNATURE), Some(KEY_ID), Some(SIGNATURE)), MESSAGE).unwrap(),
            Authorization::LocalKey
        );
    }

    // ── trust state ──────────────────────────────────────────────────────────

    #[test]
    fn trust_state_only_tightens() {
        let mut s = TrustState::default();
        assert_eq!(s.mode(false), TrustMode::Standard);
        assert_eq!(s.mode(true), TrustMode::ReleaseOnly);
        assert!(s.latch(false, true));
        assert_eq!(s.mode(false), TrustMode::Probe);
        assert!(!s.latch(false, false), "a later 'not a probe' changes nothing");
        assert_eq!(s.mode(false), TrustMode::Probe);
        assert!(s.latch(true, false));
        assert_eq!(s.mode(false), TrustMode::ReleaseOnly);
        assert!(!s.latch(false, false));
        assert_eq!(s.mode(false), TrustMode::ReleaseOnly);
    }

    #[test]
    fn trust_state_persists_and_fails_closed() {
        let dir = std::env::temp_dir().join(format!("nodeglow-trust-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = TrustState::path_for(&dir.join("config.json"));
        assert_eq!(path, dir.join("update-trust.json"));
        let _ = std::fs::remove_file(&path);

        assert_eq!(TrustState::load(&path), TrustState::default());

        let mut s = TrustState::default();
        s.observe(&path, true, false);
        assert_eq!(TrustState::load(&path), TrustState { multi_tenant: true, probe: false });

        std::fs::write(&path, "not json").unwrap();
        assert_eq!(TrustState::load(&path).mode(false), TrustMode::ReleaseOnly);

        let _ = std::fs::remove_dir_all(&dir);
    }
}

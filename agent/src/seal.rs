//! Sealed deploy logins (probe side).
//!
//! The probe owns an X25519 key pair in `seal.key` next to its config. The
//! public half goes to the server in the heartbeat; the browser seals a login
//! to it (HPKE, RFC 9180, base mode), so the server stores and relays only
//! ciphertext. The probe opens the envelope when a deploy command carries one.
//!
//! Nothing here logs or returns secrets in clear: the private key lives in a
//! `Zeroizing` buffer and decrypted logins are zeroized on drop.

use std::collections::HashMap;
use std::fmt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context};
use base64::engine::general_purpose::{STANDARD as B64, STANDARD_NO_PAD as B64_NO_PAD};
use base64::Engine;
use hpke::aead::ChaCha20Poly1305;
use hpke::kdf::HkdfSha256;
use hpke::kem::X25519HkdfSha256;
use hpke::{Deserializable, Kem as KemTrait, OpModeR, Serializable};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use zeroize::{Zeroize, Zeroizing};

use crate::deploy::{self, Credential};

pub const SUITE: &str = "hpke-x25519-sha256-chacha20poly1305";
const INFO: &[u8] = b"nodeglow sealed credential v1";
const MAX_ENVELOPE: usize = 32768;
const MAX_PLAINTEXT: usize = 16384;
const MAX_HOSTS: usize = 64;
const MAX_HOST_LEN: usize = 255;

type Kem = X25519HkdfSha256;

/// `seal.key` next to the agent config.
pub fn key_path_for(config_path: &Path) -> PathBuf {
    config_path.with_file_name("seal.key")
}

// ── Probe key ───────────────────────────────────────────────────────────────

/// The probe's sealing key pair. No `Debug` derive: it would print the key.
pub struct SealKey {
    private: Zeroizing<[u8; 32]>,
    public: [u8; 32],
}

impl fmt::Debug for SealKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SealKey").field("fingerprint", &self.fingerprint()).finish()
    }
}

impl SealKey {
    fn from_private_bytes(private: Zeroizing<[u8; 32]>) -> Self {
        // from_bytes only fails on a wrong length, which the array rules out.
        let sk = <Kem as KemTrait>::PrivateKey::from_bytes(private.as_slice())
            .expect("32-byte X25519 scalar");
        let pk = Kem::sk_to_pk(&sk);
        let mut public = [0u8; 32];
        public.copy_from_slice(pk.to_bytes().as_slice());
        SealKey { private, public }
    }

    fn generate() -> anyhow::Result<Self> {
        let mut bytes = Zeroizing::new([0u8; 32]);
        getrandom::fill(bytes.as_mut_slice()).map_err(|e| anyhow!("No randomness available: {e}"))?;
        Ok(Self::from_private_bytes(bytes))
    }

    /// The stored key, `None` when the file is absent. Errors when the file is
    /// not private (root-owned 0600) or does not hold a key.
    pub fn load(path: &Path, require_root: bool) -> anyhow::Result<Option<SealKey>> {
        let meta = match std::fs::metadata(path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e).with_context(|| format!("Cannot read {}", path.display())),
        };
        if !deploy::private_enough(&meta, require_root) {
            bail!("{} must be owned by root and not readable by others (chmod 600)", path.display());
        }
        let text = Zeroizing::new(
            std::fs::read_to_string(path).with_context(|| format!("Cannot read {}", path.display()))?);
        let raw = Zeroizing::new(
            B64.decode(text.trim()).map_err(|_| anyhow!("{} is not a seal key", path.display()))?);
        if raw.len() != 32 {
            bail!("{} is not a seal key", path.display());
        }
        let mut bytes = Zeroizing::new([0u8; 32]);
        bytes.copy_from_slice(&raw);
        Ok(Some(Self::from_private_bytes(bytes)))
    }

    /// The stored key, or a fresh one written to `path`.
    pub fn load_or_create(path: &Path, require_root: bool) -> anyhow::Result<SealKey> {
        if let Some(key) = Self::load(path, require_root)? {
            return Ok(key);
        }
        let key = Self::generate()?;
        key.store(path)?;
        Ok(key)
    }

    /// Replace the stored key with a fresh one.
    pub fn rotate(path: &Path, require_root: bool) -> anyhow::Result<SealKey> {
        // Refuse to touch a file that is not private, like load does.
        Self::load(path, require_root)?;
        let key = Self::generate()?;
        key.store(path)?;
        Ok(key)
    }

    /// Atomic write: a 0600 temp file in the same directory, fsync, rename.
    fn store(&self, path: &Path) -> anyhow::Result<()> {
        use std::io::Write;
        let tmp = path.with_file_name(format!(".seal.key.tmp-{}", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let text = Zeroizing::new(format!("{}\n", B64.encode(self.private.as_slice())));
        let written = (|| -> std::io::Result<()> {
            let mut file = opts.open(&tmp)?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
            std::fs::rename(&tmp, path)?;
            Ok(())
        })();
        if let Err(e) = written {
            let _ = std::fs::remove_file(&tmp);
            return Err(e).with_context(|| format!("Cannot write {}", path.display()));
        }
        // Make the rename itself durable. The new key is already in place, so
        // a failing directory fsync is a warning, not an error.
        #[cfg(unix)]
        if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
            if let Err(e) = std::fs::File::open(dir).and_then(|d| d.sync_all()) {
                tracing::warn!("Cannot fsync {}: {e}", dir.display());
            }
        }
        Ok(())
    }

    pub fn public_b64(&self) -> String {
        B64.encode(self.public)
    }

    /// `SHA256:` + unpadded base64 of SHA-256 over the raw public key.
    pub fn fingerprint(&self) -> String {
        format!("SHA256:{}", B64_NO_PAD.encode(Sha256::digest(self.public)))
    }

    /// What the heartbeat reports.
    pub fn report(&self) -> Value {
        json!({"suite": SUITE, "public_key": self.public_b64()})
    }
}

// ── Opening ─────────────────────────────────────────────────────────────────

#[derive(Debug, PartialEq, Eq)]
pub enum OpenError {
    /// Sealed to another key, or the ciphertext does not authenticate.
    WrongKey,
    /// Not an envelope this probe understands.
    Malformed,
    /// Decrypted, but the content breaks the rules.
    Invalid(String),
}

impl fmt::Display for OpenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OpenError::WrongKey => {
                f.write_str("This login was sealed to another key of this probe; enter it again.")
            }
            OpenError::Malformed => f.write_str("The sealed login is not in a format this probe understands."),
            OpenError::Invalid(why) => write!(f, "The sealed login is invalid: {why}"),
        }
    }
}

impl std::error::Error for OpenError {}

/// A decrypted login. Secrets are zeroized on drop.
pub struct SealedLogin {
    /// The login's id on the server; only read by tests.
    #[allow(dead_code)]
    pub id: String,
    pub name: String,
    pub credential: Credential,
}

impl Drop for SealedLogin {
    fn drop(&mut self) {
        self.credential.password.zeroize();
        self.credential.private_key.zeroize();
        self.credential.passphrase.zeroize();
    }
}

fn valid_name(name: &str) -> bool {
    (1..=64).contains(&name.len())
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'@' | b'-'))
}

/// The decrypted JSON, read straight into types that zeroize their secrets
/// on every path (no `Value` intermediate, no clones).
#[derive(serde::Deserialize)]
struct RawPlaintext {
    v: Option<u64>,
    id: Option<String>,
    name: Option<String>,
    username: Option<String>,
    password: Option<Zeroizing<String>>,
    private_key: Option<Zeroizing<String>>,
    passphrase: Option<Zeroizing<String>>,
    hosts: Option<Vec<String>>,
}

/// An empty string counts as absent; the secret moves out, the husk zeroizes.
fn take_secret(field: Option<Zeroizing<String>>) -> Option<String> {
    field.filter(|s| !s.is_empty()).map(|mut s| std::mem::take(&mut *s))
}

fn required(field: Option<String>, name: &str) -> Result<String, OpenError> {
    field.filter(|s| !s.is_empty()).ok_or_else(|| OpenError::Invalid(format!("`{name}` is missing")))
}

/// Check the decrypted JSON against the plaintext rules.
fn parse_plaintext(bytes: &[u8]) -> Result<SealedLogin, OpenError> {
    if bytes.len() > MAX_PLAINTEXT {
        return Err(OpenError::Invalid("too large".into()));
    }
    // The serde message is dropped: it could quote part of a secret.
    let raw: RawPlaintext = serde_json::from_slice(bytes)
        .map_err(|_| OpenError::Invalid("not a valid login".into()))?;
    if raw.v != Some(1) {
        return Err(OpenError::Invalid("unsupported version".into()));
    }
    let name = required(raw.name, "name")?;
    if !valid_name(&name) {
        return Err(OpenError::Invalid("bad name".into()));
    }
    let id = required(raw.id, "id")?;
    let username = required(raw.username, "username")?;
    let hosts = raw.hosts.ok_or_else(|| OpenError::Invalid("`hosts` is missing".into()))?;
    if hosts.is_empty() || hosts.len() > MAX_HOSTS {
        return Err(OpenError::Invalid(format!("1 to {MAX_HOSTS} hosts are required")));
    }
    if hosts.iter().any(|h| h.trim().is_empty() || h.len() > MAX_HOST_LEN) {
        return Err(OpenError::Invalid("bad host entry".into()));
    }
    let password = take_secret(raw.password);
    let private_key = take_secret(raw.private_key);
    let passphrase = take_secret(raw.passphrase);
    let login = SealedLogin {
        id,
        name,
        credential: Credential { username, password, private_key_file: None, private_key, passphrase, hosts },
    };
    // On rejection `login` drops here and zeroizes its secrets.
    if login.credential.password.is_some() == login.credential.private_key.is_some() {
        return Err(OpenError::Invalid("exactly one of password and private key is required".into()));
    }
    Ok(login)
}

/// Open an envelope sealed to `key`.
pub fn open(key: &SealKey, envelope: &Value) -> Result<SealedLogin, OpenError> {
    let obj = envelope.as_object().ok_or(OpenError::Malformed)?;
    if serde_json::to_vec(envelope).map_err(|_| OpenError::Malformed)?.len() > MAX_ENVELOPE {
        return Err(OpenError::Malformed);
    }
    let text = |k: &str| obj.get(k).and_then(Value::as_str).ok_or(OpenError::Malformed);
    if obj.get("v") != Some(&json!(1)) || text("suite")? != SUITE {
        return Err(OpenError::Malformed);
    }
    let fingerprint = key.fingerprint();
    if text("key")? != fingerprint {
        return Err(OpenError::WrongKey);
    }
    let enc = B64.decode(text("enc")?).map_err(|_| OpenError::Malformed)?;
    let ct = B64.decode(text("ct")?).map_err(|_| OpenError::Malformed)?;
    // The AEAD adds a 16-byte tag.
    if ct.len() < 16 || ct.len() > MAX_PLAINTEXT + 16 {
        return Err(OpenError::Malformed);
    }
    let encapped = <Kem as KemTrait>::EncappedKey::from_bytes(&enc).map_err(|_| OpenError::Malformed)?;
    let sk = <Kem as KemTrait>::PrivateKey::from_bytes(key.private.as_slice())
        .map_err(|_| OpenError::Malformed)?;
    let plain = hpke::single_shot_open::<ChaCha20Poly1305, HkdfSha256, Kem>(
        &OpModeR::Base, &sk, &encapped, INFO, &ct, fingerprint.as_bytes())
        .map_err(|_| OpenError::WrongKey)?;
    let plain = Zeroizing::new(plain);
    parse_plaintext(&plain)
}

// ── Throttle ────────────────────────────────────────────────────────────────

const HOST_WINDOW: Duration = Duration::from_secs(3600);
const MAX_HOSTS_PER_WINDOW: usize = 20;
const FAIL_WINDOW: Duration = Duration::from_secs(15 * 60);
const MAX_FAILURES: usize = 5;
const BLOCK_FOR: Duration = Duration::from_secs(15 * 60);

/// Limits for sealed operations: at most 20 distinct target hosts per hour,
/// and a 15 minute pause after 5 failures within 15 minutes.
#[derive(Default)]
pub struct Throttle {
    hosts: HashMap<String, Instant>,
    failures: Vec<Instant>,
    blocked_until: Option<Instant>,
}

impl Throttle {
    pub fn admit(&mut self, host: &str, now: Instant) -> Result<(), String> {
        if let Some(until) = self.blocked_until {
            if now < until {
                return Err("Sealed logins are paused for a few minutes after repeated failures.".into());
            }
            self.blocked_until = None;
        }
        self.hosts.retain(|_, seen| now.saturating_duration_since(*seen) < HOST_WINDOW);
        let host = deploy::connect_host(host).to_ascii_lowercase();
        if !self.hosts.contains_key(&host) && self.hosts.len() >= MAX_HOSTS_PER_WINDOW {
            return Err(format!("Sealed logins are limited to {MAX_HOSTS_PER_WINDOW} different hosts per hour."));
        }
        self.hosts.insert(host, now);
        Ok(())
    }

    pub fn failed(&mut self, now: Instant) {
        self.failures.retain(|t| now.saturating_duration_since(*t) < FAIL_WINDOW);
        self.failures.push(now);
        if self.failures.len() >= MAX_FAILURES {
            self.failures.clear();
            self.blocked_until = Some(now + BLOCK_FOR);
        }
    }
}

/// Seal `plaintext` to `key` the way the browser does (tests only).
#[cfg(test)]
pub(crate) fn seal_for_test(key: &SealKey, plaintext: &Value) -> Value {
    use hpke::rand_core::{CryptoRng, RngCore};
    struct Sys;
    impl RngCore for Sys {
        fn next_u32(&mut self) -> u32 {
            let mut b = [0u8; 4];
            self.fill_bytes(&mut b);
            u32::from_le_bytes(b)
        }
        fn next_u64(&mut self) -> u64 {
            let mut b = [0u8; 8];
            self.fill_bytes(&mut b);
            u64::from_le_bytes(b)
        }
        fn fill_bytes(&mut self, dest: &mut [u8]) {
            getrandom::fill(dest).unwrap();
        }
    }
    impl CryptoRng for Sys {}
    let pk = <Kem as KemTrait>::PublicKey::from_bytes(&key.public).unwrap();
    let fp = key.fingerprint();
    let (enc, ct) = hpke::single_shot_seal::<ChaCha20Poly1305, HkdfSha256, Kem, _>(
        &hpke::OpModeS::Base, &pk, INFO, &serde_json::to_vec(plaintext).unwrap(), fp.as_bytes(), &mut Sys)
        .unwrap();
    json!({"v": 1, "suite": SUITE, "key": fp,
           "enc": B64.encode(enc.to_bytes()), "ct": B64.encode(ct)})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    const FIXTURE: &str = include_str!("../tests/fixtures/sealed_v1.json");

    fn fixture() -> (SealKey, Value, Value) {
        let f: Value = serde_json::from_str(FIXTURE).unwrap();
        let sk = B64.decode(f["private_key"].as_str().unwrap()).unwrap();
        let mut bytes = Zeroizing::new([0u8; 32]);
        bytes.copy_from_slice(&sk);
        let key = SealKey::from_private_bytes(bytes);
        (key, f["envelope"].clone(), f["plaintext"].clone())
    }

    #[cfg(unix)]
    fn tempdir() -> PathBuf {
        let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir().join(format!("ng-seal-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn plain(extra: Value) -> Vec<u8> {
        let mut base = json!({"v": 1, "id": "i", "name": "lab-root", "username": "root",
            "password": "pw", "private_key": null, "passphrase": null, "hosts": ["10.0.0.1"]});
        for (k, v) in extra.as_object().unwrap() {
            base[k] = v.clone();
        }
        serde_json::to_vec(&base).unwrap()
    }

    #[test]
    fn opens_frontend_fixture() {
        let (key, env, pt) = fixture();
        let login = open(&key, &env).expect("opens");
        assert_eq!(login.id, pt["id"]);
        assert_eq!(login.name, pt["name"]);
        assert_eq!(login.credential.username, "root");
        assert_eq!(login.credential.password.as_deref(), Some("correct horse battery staple"));
        assert!(login.credential.private_key.is_none());
        assert_eq!(login.credential.hosts, vec!["10.0.20.0/24", "*.lab.example"]);
    }

    #[test]
    fn fingerprint_matches_fixture_style() {
        let (key, env, _) = fixture();
        assert_eq!(key.fingerprint(), env["key"]);
        let f: Value = serde_json::from_str(FIXTURE).unwrap();
        assert_eq!(key.public_b64(), f["public_key"].as_str().unwrap());
        assert_eq!(key.report(), json!({"suite": SUITE, "public_key": key.public_b64()}));
    }

    #[test]
    fn wrong_key_is_wrong_key() {
        let (_, env, _) = fixture();
        let other = SealKey::generate().unwrap();
        assert_eq!(open(&other, &env).err(), Some(OpenError::WrongKey));
        assert_eq!(OpenError::WrongKey.to_string(),
                   "This login was sealed to another key of this probe; enter it again.");
    }

    #[test]
    fn tampered_ct_is_wrong_key() {
        let (key, mut env, _) = fixture();
        let mut ct = B64.decode(env["ct"].as_str().unwrap()).unwrap();
        ct[3] ^= 1;
        env["ct"] = json!(B64.encode(ct));
        assert_eq!(open(&key, &env).err(), Some(OpenError::WrongKey));
    }

    #[test]
    fn malformed_envelopes() {
        let (key, env, _) = fixture();
        assert_eq!(open(&key, &json!([1])).err(), Some(OpenError::Malformed));
        let mut e = env.clone();
        e["suite"] = json!("x");
        assert_eq!(open(&key, &e).err(), Some(OpenError::Malformed));
        let mut e = env.clone();
        e["enc"] = json!("!!!");
        assert_eq!(open(&key, &e).err(), Some(OpenError::Malformed));
        let mut e = env;
        e["ct"] = json!("A".repeat(40000));
        assert_eq!(open(&key, &e).err(), Some(OpenError::Malformed));
    }

    #[test]
    fn rejects_two_secrets() {
        let both = plain(json!({"private_key": "KEY"}));
        assert!(matches!(parse_plaintext(&both), Err(OpenError::Invalid(_))));
        let none = plain(json!({"password": null}));
        assert!(matches!(parse_plaintext(&none), Err(OpenError::Invalid(_))));
        assert!(parse_plaintext(&plain(json!({}))).is_ok());
    }

    #[test]
    fn rejects_empty_hosts() {
        assert!(matches!(parse_plaintext(&plain(json!({"hosts": []}))), Err(OpenError::Invalid(_))));
        let many: Vec<String> = (0..65).map(|i| format!("h{i}")).collect();
        assert!(matches!(parse_plaintext(&plain(json!({"hosts": many}))), Err(OpenError::Invalid(_))));
        assert!(matches!(parse_plaintext(&plain(json!({"hosts": ["a".repeat(256)]}))),
                         Err(OpenError::Invalid(_))));
        assert!(matches!(parse_plaintext(&plain(json!({"name": "bad name"}))), Err(OpenError::Invalid(_))));
        assert!(matches!(parse_plaintext(&plain(json!({"v": 2}))), Err(OpenError::Invalid(_))));
    }

    #[test]
    fn rejects_oversized() {
        let big = plain(json!({"passphrase": "x".repeat(MAX_PLAINTEXT)}));
        assert!(big.len() > MAX_PLAINTEXT);
        assert!(matches!(parse_plaintext(&big), Err(OpenError::Invalid(_))));
    }

    #[test]
    fn debug_hides_secrets() {
        let (key, env, _) = fixture();
        let login = open(&key, &env).unwrap();
        assert!(!format!("{:?}", login.credential).contains("correct horse"));
        assert!(!format!("{key:?}").contains(&B64.encode(key.private.as_slice())));
    }

    #[cfg(unix)]
    #[test]
    fn key_file_created_0600_and_reloaded() {
        let dir = tempdir();
        let path = dir.join("seal.key");
        assert!(SealKey::load(&path, false).unwrap().is_none());
        let key = SealKey::load_or_create(&path, false).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        let again = SealKey::load_or_create(&path, false).unwrap();
        assert_eq!(key.fingerprint(), again.fingerprint());
        assert_eq!(SealKey::load(&path, false).unwrap().unwrap().public_b64(), key.public_b64());
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1, "no temp file left behind");
        assert_eq!(key_path_for(&dir.join("config.json")), path);
    }

    #[cfg(unix)]
    #[test]
    fn key_file_world_readable_refused() {
        let dir = tempdir();
        let path = dir.join("seal.key");
        SealKey::load_or_create(&path, false).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(SealKey::load(&path, false).is_err());
        assert!(SealKey::load_or_create(&path, false).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn rotate_changes_fingerprint() {
        let dir = tempdir();
        let path = dir.join("seal.key");
        let first = SealKey::load_or_create(&path, false).unwrap();
        let second = SealKey::rotate(&path, false).unwrap();
        assert_ne!(first.fingerprint(), second.fingerprint());
        assert_eq!(SealKey::load(&path, false).unwrap().unwrap().fingerprint(), second.fingerprint());
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn throttle_twenty_hosts_per_hour() {
        let mut t = Throttle::default();
        let t0 = Instant::now();
        for i in 0..20 {
            t.admit(&format!("h{i}"), t0).unwrap();
        }
        assert!(t.admit("h20", t0).is_err());
        t.admit("h3", t0).expect("a repeat host is admitted");
        t.admit("h20", t0 + Duration::from_secs(3601)).expect("admitted after an hour");
    }

    #[test]
    fn throttle_blocks_after_five_failures() {
        let mut t = Throttle::default();
        let t0 = Instant::now();
        for i in 0..4 {
            t.failed(t0 + Duration::from_secs(i));
        }
        t.admit("a", t0 + Duration::from_secs(5)).expect("four failures do not block");
        t.failed(t0 + Duration::from_secs(6));
        assert!(t.admit("a", t0 + Duration::from_secs(7)).is_err());
        assert!(t.admit("a", t0 + Duration::from_secs(6 + 14 * 60)).is_err());
        t.admit("a", t0 + Duration::from_secs(6 + 15 * 60 + 1)).expect("admitted after 15 minutes");
    }

    #[test]
    fn crlf_private_key_parses() {
        use russh::keys::ssh_key::{private::Ed25519Keypair, LineEnding};
        use russh::keys::PrivateKey;
        let mut seed = [0u8; 32];
        getrandom::fill(&mut seed).unwrap();
        let key = PrivateKey::from(Ed25519Keypair::from_seed(&seed));
        let pem = key.to_openssh(LineEnding::LF).unwrap();
        let messy: String = pem.lines().map(|l| format!("{l}  \r\n")).collect::<String>() + "\r\n  ";
        let parsed = russh::keys::decode_secret_key(&deploy::normalize_pem(&messy), None).expect("parses");
        assert_eq!(parsed.public_key(), key.public_key());
    }
}

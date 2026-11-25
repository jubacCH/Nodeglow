//! Deploying the agent onto other hosts from a probe (part C).
//!
//! See docs/specs/2026-11-19-probe-agent-deploy-design.md. The server sends
//! data only (target, credential *name*, host key fingerprint, enrolment
//! token); everything that runs on a target is fixed here. The probe:
//!
//! * acts only when `deploy.json` next to its config opts in. The file is
//!   root-owned 0600, never written by the agent and never sent anywhere;
//!   it also holds the SSH credentials, which the server knows by name only;
//! * installs the release-signed Linux binary it downloads from its own
//!   server and verifies against the compiled-in release keys;
//! * binds the new agent to its own server (never a URL from a command), with
//!   `require_release_signature` on.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context};
use russh::keys::{HashAlg, PrivateKeyWithHashAlg, PublicKey};
use russh::{client, ChannelMsg};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;
use tracing::{info, warn};

use crate::client::ApiClient;
use crate::seal::{self, SealKey};
use crate::updater;

/// Targets this probe can install on: the only signed Linux build is x86_64.
pub const SUPPORTED_ARCH: &[&str] = &["x86_64"];
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const COMMAND_TIMEOUT: Duration = Duration::from_secs(180);
const ENROL_WAIT: Duration = Duration::from_secs(45);
const DETAIL_LIMIT: usize = 1500;

// ── deploy.json ─────────────────────────────────────────────────────────────

/// An SSH login. One taken from a `seal::SealedLogin` carries decrypted
/// secrets and must not be cloned out of it: the copy would not be zeroized.
#[derive(Clone, Deserialize)]
pub struct Credential {
    pub username: String,
    #[serde(default)]
    pub password: Option<String>,
    #[serde(default)]
    pub private_key_file: Option<PathBuf>,
    /// A pasted private key (PEM text), set only for logins sealed to this
    /// probe; never read from deploy.json.
    #[serde(skip)]
    pub private_key: Option<String>,
    #[serde(default)]
    pub passphrase: Option<String>,
    /// Where this login may be used: host names, `*.domain`, IP addresses or
    /// CIDR networks. Required — without it the credential reaches nothing,
    /// so the server cannot send it to an address of its choosing.
    #[serde(default)]
    pub hosts: Vec<String>,
}

impl std::fmt::Debug for Credential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Credential")
            .field("username", &self.username)
            .field("password", &self.password.as_ref().map(|_| "***"))
            .field("private_key_file", &self.private_key_file)
            .field("private_key", &self.private_key.as_ref().map(|_| "***"))
            .field("passphrase", &self.passphrase.as_ref().map(|_| "***"))
            .field("hosts", &self.hosts)
            .finish()
    }
}

impl Credential {
    /// Whether `host` (as the command names it) is on this credential's list.
    pub fn allows(&self, host: &str) -> bool {
        let host = connect_host(host);
        self.hosts.iter().any(|entry| host_matches(entry.trim(), host))
    }
}

/// The address to connect to: `[v6]` without the brackets.
pub fn connect_host(host: &str) -> &str {
    host.strip_prefix('[').and_then(|h| h.strip_suffix(']')).unwrap_or(host)
}

fn host_matches(entry: &str, host: &str) -> bool {
    use std::net::IpAddr;
    if let Ok(ip) = host.parse::<IpAddr>() {
        if let Some((net, bits)) = entry.split_once('/') {
            let (Ok(net), Ok(bits)) = (net.parse::<IpAddr>(), bits.parse::<u32>()) else { return false };
            return in_network(ip, net, bits);
        }
        return entry.parse::<IpAddr>().is_ok_and(|e| e == ip);
    }
    if let Some(suffix) = entry.strip_prefix("*.") {
        let host = host.to_ascii_lowercase();
        let suffix = suffix.to_ascii_lowercase();
        return host.len() > suffix.len() + 1 && host.ends_with(&format!(".{suffix}"));
    }
    entry.eq_ignore_ascii_case(host)
}

fn in_network(ip: std::net::IpAddr, net: std::net::IpAddr, bits: u32) -> bool {
    use std::net::IpAddr::{V4, V6};
    match (ip, net) {
        (V4(ip), V4(net)) if bits <= 32 => {
            let mask = if bits == 0 { 0 } else { u32::MAX << (32 - bits) };
            u32::from(ip) & mask == u32::from(net) & mask
        }
        (V6(ip), V6(net)) if bits <= 128 => {
            let mask = if bits == 0 { 0 } else { u128::MAX << (128 - bits) };
            u128::from(ip) & mask == u128::from(net) & mask
        }
        _ => false,
    }
}

/// Names the server accepts (it drops others); see agent_commands._NAME.
fn reportable_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 64
        && name.chars().all(|c| c.is_ascii_alphanumeric() || "_.@-".contains(c))
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct DeployFile {
    #[serde(default)]
    pub allow_deploy_agents: bool,
    /// Second local switch: logins sealed to this probe's key are accepted.
    #[serde(default)]
    pub allow_sealed_credentials: bool,
    #[serde(default)]
    pub credentials: BTreeMap<String, Credential>,
}

impl DeployFile {
    pub fn path_for(config_path: &Path) -> PathBuf {
        config_path.with_file_name("deploy.json")
    }

    /// The local opt-in, or `None` when absent, unreadable or not private.
    /// `require_root` is false in tests only.
    pub fn load(path: &Path, require_root: bool) -> Option<Self> {
        let meta = std::fs::metadata(path).ok()?;
        if !private_enough(&meta, require_root) {
            warn!("{} is ignored: it must be owned by root and not readable by others (chmod 600)",
                  path.display());
            return None;
        }
        let text = std::fs::read_to_string(path).ok()?;
        match serde_json::from_str::<DeployFile>(&text) {
            Ok(file) => Some(file),
            Err(e) => {
                warn!("{} is ignored: {e}", path.display());
                None
            }
        }
    }

    pub fn enabled(&self) -> bool {
        self.allow_deploy_agents
    }

    /// Deploying is on and sealed logins are allowed (both local flags).
    pub fn sealed_enabled(&self) -> bool {
        self.allow_deploy_agents && self.allow_sealed_credentials
    }
}

/// Whether the process runs as root (always false off Unix).
pub fn running_as_root() -> bool {
    #[cfg(unix)]
    {
        // SAFETY: geteuid has no preconditions and cannot fail.
        unsafe { libc::geteuid() == 0 }
    }
    #[cfg(not(unix))]
    {
        false
    }
}

/// Root ownership of `deploy.json` and `seal.key` is enforced everywhere but
/// in tests, which run as an ordinary user.
const REQUIRE_ROOT: bool = !cfg!(test);

/// Turn deploying (and sealed logins) on or off in `deploy.json`, keeping
/// every other key. Atomic write (temp file + rename), mode 0600.
pub fn set_deploy_flags(path: &Path, on: bool) -> anyhow::Result<()> {
    use std::io::Write;
    static WRITE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let _guard = WRITE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let mut doc: Value = match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text)
            .with_context(|| format!("{} is not valid JSON; fix or remove it first", path.display()))?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => json!({}),
        Err(e) => return Err(e).with_context(|| format!("Cannot read {}", path.display())),
    };
    let obj = doc.as_object_mut()
        .ok_or_else(|| anyhow!("{} must hold a JSON object", path.display()))?;
    obj.insert("allow_deploy_agents".into(), json!(on));
    obj.insert("allow_sealed_credentials".into(), json!(on));
    let text = Zeroizing::new(serde_json::to_string_pretty(&doc)? + "\n");
    let n = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = path.with_file_name(format!(".deploy.json.tmp-{}-{n}", std::process::id()));
    let _ = std::fs::remove_file(&tmp);
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let written = (|| -> std::io::Result<()> {
        let mut file = opts.open(&tmp)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        std::fs::rename(&tmp, path)
    })();
    if let Err(e) = written {
        let _ = std::fs::remove_file(&tmp);
        return Err(e).with_context(|| format!("Cannot write {}", path.display()));
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn private_enough(meta: &std::fs::Metadata, require_root: bool) -> bool {
    use std::os::unix::fs::MetadataExt;
    meta.is_file() && meta.mode() & 0o077 == 0 && (!require_root || meta.uid() == 0)
}

#[cfg(not(unix))]
pub(crate) fn private_enough(_meta: &std::fs::Metadata, _require_root: bool) -> bool {
    // Not offered on Windows probes yet: no equivalent check is in place.
    false
}

/// What the heartbeat reports: whether deploying is on, the credential
/// names (never their contents), the target architectures, whether sealed
/// logins are accepted and, whenever a key exists, its public half.
pub fn capability(file: Option<&DeployFile>, seal: Option<&SealKey>) -> Value {
    let mut cap = match file {
        Some(f) if f.enabled() => json!({
            "enabled": true,
            "credentials": f.credentials.keys().filter(|n| reportable_name(n)).collect::<Vec<_>>(),
            "arch": SUPPORTED_ARCH,
            "sealed": f.sealed_enabled(),
        }),
        _ => json!({"enabled": false, "credentials": [], "arch": [], "sealed": false}),
    };
    if let Some(key) = seal {
        cap["seal"] = key.report();
    }
    cap
}

// ── Commands ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
pub struct Command {
    pub id: i64,
    pub op: String,
    #[serde(default)]
    pub payload: Value,
    #[serde(default)]
    pub expires_at: Option<String>,
}

/// Answers waiting for the next heartbeat.
pub type Answers = Arc<std::sync::Mutex<Vec<CommandResult>>>;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct CommandResult {
    pub id: i64,
    pub status: &'static str,
    pub result: Value,
}

impl CommandResult {
    fn ok(id: i64, result: Value) -> Self {
        Self { id, status: "ok", result }
    }
    fn failed(id: i64, detail: impl Into<String>) -> Self {
        Self { id, status: "failed", result: json!({"detail": detail.into()}) }
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Target {
    Host { host: String, #[serde(default = "default_port")] port: u16 },
    Lxc { host: String, #[serde(default = "default_port")] port: u16, vmid: u32 },
}

fn default_port() -> u16 {
    22
}

impl Target {
    fn address(&self) -> (&str, u16) {
        match self {
            Target::Host { host, port } | Target::Lxc { host, port, .. } => (host, *port),
        }
    }
}

#[derive(Debug, Deserialize)]
struct DeployPayload {
    target: Target,
    #[serde(default)]
    credential: Option<String>,
    #[serde(default)]
    sealed_credential: Option<Value>,
    host_key_fingerprint: String,
    enrollment_token: String,
}

#[derive(Debug, Deserialize)]
struct HostKeyPayload {
    host: String,
    #[serde(default = "default_port")]
    port: u16,
    #[serde(default)]
    sealed_credential: Option<Value>,
}

/// A host name or address; never something a shell or ssh could read as an
/// option.
fn valid_host(host: &str) -> bool {
    !host.is_empty()
        && host.len() <= 255
        && !host.starts_with('-')
        && host.chars().all(|c| c.is_ascii_alphanumeric() || "._:-[]%".contains(c))
}

pub fn expired(cmd: &Command, now: chrono::DateTime<chrono::Utc>) -> bool {
    cmd.expires_at
        .as_deref()
        .and_then(|t| chrono::DateTime::parse_from_rfc3339(t).ok())
        .is_some_and(|t| t.with_timezone(&chrono::Utc) <= now)
}

/// What the probe needs from its own setup to deploy.
#[derive(Clone)]
pub struct ProbeContext {
    pub deploy_file: Option<DeployFile>,
    /// The probe accepts an invalid TLS certificate from its server; an
    /// agent it installs would not (that setting is not carried over).
    pub insecure_tls: bool,
    /// The probe's own server: the only one a deployed agent is bound to.
    pub server: String,
    pub api: Arc<ApiClient>,
    /// `seal.key` next to the config.
    pub seal_key_path: PathBuf,
    /// Shared by all commands: limits sealed operations.
    pub throttle: Arc<std::sync::Mutex<seal::Throttle>>,
}

const SEALED_OFF: &str = "Sealed logins are not switched on on this probe (deploy.json).";

/// Open a sealed envelope with this probe's key.
fn open_sealed(ctx: &ProbeContext, envelope: &Value) -> Result<seal::SealedLogin, String> {
    let key = match SealKey::load(&ctx.seal_key_path, REQUIRE_ROOT) {
        Ok(Some(k)) => k,
        Ok(None) => return Err("This probe has no seal key.".into()),
        Err(e) => return Err(format!("{e:#}")),
    };
    seal::open(&key, envelope).map_err(|e| e.to_string())
}

fn admit(ctx: &ProbeContext, host: &str) -> Result<(), String> {
    ctx.throttle.lock().expect("throttle").admit(host, std::time::Instant::now())
}

fn note_failure(ctx: &ProbeContext) {
    ctx.throttle.lock().expect("throttle").failed(std::time::Instant::now());
}

/// Run one command and say how it went. Never panics, never returns secrets.
pub async fn handle(ctx: &ProbeContext, cmd: Command) -> CommandResult {
    let id = cmd.id;
    if expired(&cmd, chrono::Utc::now()) {
        return CommandResult::failed(id, "The command expired before the probe got to it.");
    }
    if cmd.op == "rotate_seal_key" {
        return rotate_seal_key(ctx, id);
    }
    let Some(file) = ctx.deploy_file.as_ref().filter(|f| f.enabled()) else {
        return CommandResult::failed(id, "Deploying agents is not switched on on this probe (deploy.json).");
    };
    match cmd.op.as_str() {
        "ssh_host_key" => host_key(ctx, file, id, cmd.payload).await,
        "deploy_agent" => {
            let payload: DeployPayload = match serde_json::from_value(cmd.payload) {
                Ok(p) => p,
                Err(_) => return CommandResult::failed(id, "Invalid deploy command."),
            };
            let token = payload.enrollment_token.clone();
            let outcome = deploy(ctx, file, &payload).await;
            match outcome {
                Ok(detail) => CommandResult::ok(id, json!({"detail": redact(&detail, &token)})),
                Err(e) => CommandResult::failed(id, redact(&format!("{e:#}"), &token)),
            }
        }
        other => CommandResult::failed(id, format!("Unknown command {other:?}.")),
    }
}

fn rotate_seal_key(ctx: &ProbeContext, id: i64) -> CommandResult {
    if !ctx.deploy_file.as_ref().is_some_and(|f| f.sealed_enabled()) {
        return CommandResult::failed(id, SEALED_OFF);
    }
    match SealKey::rotate(&ctx.seal_key_path, REQUIRE_ROOT) {
        Ok(key) => {
            info!(fingerprint = %key.fingerprint(), "Seal key rotated");
            CommandResult::ok(id, json!({"public_key": key.public_b64()}))
        }
        Err(e) => CommandResult::failed(id, format!("{e:#}")),
    }
}

async fn host_key(ctx: &ProbeContext, file: &DeployFile, id: i64, payload: Value) -> CommandResult {
    let Ok(p) = serde_json::from_value::<HostKeyPayload>(payload) else {
        return CommandResult::failed(id, "Invalid host.");
    };
    if !valid_host(&p.host) {
        return CommandResult::failed(id, "Invalid host.");
    }
    let Some(envelope) = p.sealed_credential.as_ref() else {
        if !file.credentials.values().any(|c| c.allows(&p.host)) {
            return CommandResult::failed(
                id, format!("{} is not allowed by any credential in deploy.json (\"hosts\").", p.host));
        }
        return match fetch_host_key(&p.host, p.port).await {
            Ok(key) => CommandResult::ok(id, key),
            Err(e) => CommandResult::failed(id, format!("{e:#}")),
        };
    };
    if !file.sealed_enabled() {
        return CommandResult::failed(id, SEALED_OFF);
    }
    let login = match open_sealed(ctx, envelope) {
        Ok(l) => l,
        Err(e) => return CommandResult::failed(id, e),
    };
    if !login.credential.allows(&p.host) {
        return CommandResult::failed(
            id, format!("{} is not allowed for the login {:?}.", p.host, login.name));
    }
    if let Err(e) = admit(ctx, &p.host) {
        return CommandResult::failed(id, e);
    }
    match fetch_host_key(&p.host, p.port).await {
        Ok(key) => CommandResult::ok(id, key),
        Err(e) => {
            note_failure(ctx);
            CommandResult::failed(id, format!("{e:#}"))
        }
    }
}

/// The token reduced to its prefix, and the text cut to the last part.
pub fn redact(text: &str, token: &str) -> String {
    let mut out = if token.len() >= 8 {
        text.replace(token, &format!("{}…", &token[..8]))
    } else {
        text.to_string()
    };
    if out.len() > DETAIL_LIMIT {
        let mut cut = out.len() - DETAIL_LIMIT;
        while !out.is_char_boundary(cut) {
            cut += 1;
        }
        out = out[cut..].to_string();
    }
    out
}

// ── SSH ─────────────────────────────────────────────────────────────────────

/// Accepts exactly one fingerprint; with none, records the key and refuses
/// (used to read a host key without logging in).
struct PinnedKey {
    expected: Option<String>,
    seen: Arc<std::sync::Mutex<Option<PublicKey>>>,
}

impl client::Handler for PinnedKey {
    type Error = russh::Error;

    async fn check_server_key(
        &mut self,
        presented: &russh::keys::PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        // Host certificates are not part of the confirmation flow: refused.
        let russh::keys::PublicKeyOrCertificate::PublicKey { key, .. } = presented else {
            return Ok(false);
        };
        *self.seen.lock().expect("host key slot") = Some(key.clone());
        Ok(match &self.expected {
            Some(fp) => fingerprint(key) == *fp,
            None => false,
        })
    }
}

pub fn fingerprint(key: &PublicKey) -> String {
    key.fingerprint(HashAlg::Sha256).to_string()
}

fn ssh_config() -> Arc<client::Config> {
    Arc::new(client::Config {
        inactivity_timeout: Some(COMMAND_TIMEOUT),
        ..Default::default()
    })
}

/// The host key `host:port` presents, without authenticating.
pub async fn fetch_host_key(host: &str, port: u16) -> anyhow::Result<Value> {
    let seen = Arc::new(std::sync::Mutex::new(None));
    let handler = PinnedKey { expected: None, seen: seen.clone() };
    let attempt = tokio::time::timeout(
        CONNECT_TIMEOUT, client::connect(ssh_config(), (connect_host(host), port), handler)).await;
    if attempt.is_err() {
        bail!("No SSH server answered on {host}:{port}.");
    }
    let key = seen.lock().expect("host key slot").take()
        .ok_or_else(|| anyhow!("No SSH server answered on {host}:{port}."))?;
    Ok(json!({
        "key_type": key.algorithm().as_str(),
        "fingerprint": fingerprint(&key),
        "public_key": key.to_openssh().map_err(|e| anyhow!("{e}"))?,
    }))
}

pub struct Session {
    handle: client::Handle<PinnedKey>,
    sudo: &'static str,
}

/// A pasted key as the SSH parser wants it: LF line endings, no trailing
/// whitespace on any line, no blank lines around it.
pub fn normalize_pem(pem: &str) -> String {
    pem.trim().lines().map(str::trim_end).collect::<Vec<_>>().join("\n")
}

/// Log in to `host:port`, trusting only the key with `expected_fp`.
pub async fn connect(host: &str, port: u16, cred: &Credential, expected_fp: &str)
    -> anyhow::Result<Session>
{
    let seen = Arc::new(std::sync::Mutex::new(None));
    let handler = PinnedKey { expected: Some(expected_fp.to_string()), seen: seen.clone() };
    let mut handle = match tokio::time::timeout(
        CONNECT_TIMEOUT, client::connect(ssh_config(), (connect_host(host), port), handler)).await
    {
        Err(_) => bail!("No SSH server answered on {host}:{port}."),
        Ok(Ok(h)) => h,
        Ok(Err(e)) => {
            let presented = seen.lock().expect("host key slot").as_ref().map(fingerprint);
            if let Some(fp) = presented.filter(|fp| fp != expected_fp) {
                bail!("The host key of {host}:{port} changed (now {fp}). Check the host, then remove \
                       the stored key in Nodeglow to confirm the new one.");
            }
            bail!("Cannot connect to {host}:{port}: {e}");
        }
    };
    let auth = if let Some(pem) = &cred.private_key {
        let key = russh::keys::decode_secret_key(&zeroize::Zeroizing::new(normalize_pem(pem)), cred.passphrase.as_deref())
            .context("Cannot read the private key")?;
        let hash = handle.best_supported_rsa_hash().await?.flatten();
        handle.authenticate_publickey(&cred.username,
                                      PrivateKeyWithHashAlg::new(Arc::new(key), hash)).await?
    } else if let Some(path) = &cred.private_key_file {
        let key = russh::keys::load_secret_key(path, cred.passphrase.as_deref())
            .with_context(|| format!("Cannot read the private key {}", path.display()))?;
        let hash = handle.best_supported_rsa_hash().await?.flatten();
        handle.authenticate_publickey(&cred.username,
                                      PrivateKeyWithHashAlg::new(Arc::new(key), hash)).await?
    } else if let Some(pw) = &cred.password {
        handle.authenticate_password(&cred.username, pw).await?
    } else {
        bail!("The credential has neither a private key file nor a password.");
    };
    if !auth.success() {
        bail!("Login as {} on {host}:{port} was refused.", cred.username);
    }
    let sudo = if cred.username == "root" { "" } else { "sudo -n " };
    Ok(Session { handle, sudo })
}

impl Session {
    /// Run a fixed command with `stdin`; returns (exit code, combined output).
    pub async fn run(&self, command: &str, stdin: &[u8]) -> anyhow::Result<(u32, String)> {
        let work = async {
            let mut ch = self.handle.channel_open_session().await?;
            ch.exec(true, command.as_bytes()).await?;
            if !stdin.is_empty() {
                ch.data(stdin).await?;
            }
            ch.eof().await?;
            let mut out = Vec::new();
            let mut code = None;
            while let Some(msg) = ch.wait().await {
                match msg {
                    ChannelMsg::Data { ref data } | ChannelMsg::ExtendedData { ref data, .. } => {
                        if out.len() < 64 * 1024 {
                            out.extend_from_slice(data);
                        }
                    }
                    ChannelMsg::ExitStatus { exit_status } => code = Some(exit_status),
                    _ => {}
                }
            }
            anyhow::Ok((code.unwrap_or(255), String::from_utf8_lossy(&out).into_owned()))
        };
        tokio::time::timeout(COMMAND_TIMEOUT, work)
            .await
            .map_err(|_| anyhow!("The target did not finish within {} s.", COMMAND_TIMEOUT.as_secs()))?
    }

    async fn must(&self, command: &str, stdin: &[u8], what: &str) -> anyhow::Result<String> {
        let (code, out) = self.run(command, stdin).await?;
        if code != 0 {
            bail!("{what} failed (exit {code}): {}", out.trim());
        }
        Ok(out)
    }

    pub async fn close(self) {
        let _ = self.handle.disconnect(russh::Disconnect::ByApplication, "", "en").await;
    }
}

// ── The fixed install procedure ─────────────────────────────────────────────

const DIR: &str = "/opt/nodeglow";
const UNIT_PATH: &str = "/etc/systemd/system/nodeglow-agent.service";

/// The installer's systemd unit, unchanged.
pub const UNIT: &str = "[Unit]
Description=Nodeglow Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/nodeglow
ExecStart=/opt/nodeglow/nodeglow-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
";

/// Swap in the new files and (re)start the service, in one shell line.
/// Everything after the stop is chained with `&&`: a failed step must not
/// restart the old agent and pass for a successful install.
const ACTIVATE: &str = "systemctl stop nodeglow-agent 2>/dev/null; \
mv -f /opt/nodeglow/nodeglow-agent.new /opt/nodeglow/nodeglow-agent && \
mv -f /opt/nodeglow/config.json.new /opt/nodeglow/config.json && \
mv -f /etc/systemd/system/nodeglow-agent.service.new /etc/systemd/system/nodeglow-agent.service && \
systemctl daemon-reload && { systemctl enable nodeglow-agent >/dev/null 2>&1 || true; } && \
systemctl restart nodeglow-agent";

/// The config a deployed agent starts with: the probe's server, the
/// enrolment token, release-signed updates only.
pub fn target_config(server: &str, token: &str) -> String {
    serde_json::to_string_pretty(&json!({
        "server": server,
        "enrollment_key": token,
        "interval": 30,
        "require_release_signature": true,
    }))
    .expect("static json")
}

/// The command lines, given the sudo prefix and (for a container) its id.
pub struct Procedure {
    prefix: String,
    lxc: Option<u32>,
}

impl Procedure {
    pub fn host(sudo: &str) -> Self {
        Self { prefix: sudo.to_string(), lxc: None }
    }

    pub fn lxc(sudo: &str, vmid: u32) -> Self {
        Self { prefix: sudo.to_string(), lxc: Some(vmid) }
    }

    /// `cmd` run as root on the target (in the container for LXC).
    pub fn on_target(&self, cmd: &str) -> String {
        match self.lxc {
            None => format!("{}sh -c '{cmd}'", self.prefix),
            Some(v) => format!("{}pct exec {v} -- sh -c '{cmd}'", self.prefix),
        }
    }

    pub fn arch(&self) -> String {
        self.on_target("uname -m")
    }

    /// Exits non-zero on a target without systemd.
    pub fn has_systemd(&self) -> String {
        self.on_target("command -v systemctl >/dev/null")
    }

    pub fn read_config(&self) -> String {
        self.on_target("cat /opt/nodeglow/config.json")
    }

    pub fn journal(&self) -> String {
        self.on_target("journalctl -u nodeglow-agent -n 8 --no-pager 2>/dev/null")
    }

    pub fn activate(&self) -> String {
        self.on_target(ACTIVATE)
    }

    /// Host: write a file on the target from stdin.
    pub fn write(&self, path: &str, mode: &str) -> String {
        self.on_target(&format!("umask 077 && mkdir -p {DIR} && cat > {path} && chmod {mode} {path}"))
    }

    /// LXC: push a file from the node's temp dir into the container.
    pub fn push(&self, from: &str, to: &str, mode: &str) -> String {
        let v = self.lxc.expect("push is for containers");
        format!("{}pct push {v} {from} {to} --perms {mode}", self.prefix)
    }
}

/// A temp dir `mktemp -d` printed, before it is used in a command.
fn valid_tmp(path: &str) -> bool {
    path.starts_with('/') && path.chars().all(|c| c.is_ascii_alphanumeric() || "/._-".contains(c))
}

/// Download the Linux binary from the probe's server and verify its release
/// signature against the compiled-in keys.
pub async fn verified_binary(api: &ApiClient) -> anyhow::Result<Vec<u8>> {
    let info = api.get_version_info("linux").await?;
    let (Some(sig), Some(key_id)) = (info.release_signature.as_deref(), info.release_key_id.as_deref())
    else {
        bail!("The server offers no release-signed agent binary; refusing to install an unsigned one.");
    };
    let data = api.download_agent("linux").await?;
    if !hex::encode(Sha256::digest(&data)).eq_ignore_ascii_case(info.hash.trim()) {
        bail!("The downloaded agent binary does not match the published hash.");
    }
    updater::verify_release_signature(&updater::compiled_release_keys(), key_id, sig, "linux", &data)?;
    Ok(data)
}

fn token_of(config_json: &str) -> Option<String> {
    serde_json::from_str::<Value>(config_json)
        .ok()
        .and_then(|v| v.get("token").and_then(|t| t.as_str()).map(str::to_string))
        .filter(|t| !t.is_empty())
}

#[cfg(test)]
fn enrolled(config_json: &str) -> bool {
    token_of(config_json).is_some()
}

/// The new agent wrote a token, and not the one an agent there had before.
fn enrolled_anew(config_json: &str, before: Option<&str>) -> bool {
    token_of(config_json).is_some_and(|t| Some(t.as_str()) != before)
}

async fn deploy(ctx: &ProbeContext, file: &DeployFile, p: &DeployPayload) -> anyhow::Result<String> {
    match (&p.credential, &p.sealed_credential) {
        (Some(name), None) => {
            let cred = file.credentials.get(name)
                .ok_or_else(|| anyhow!("This probe has no credential named {:?}.", name))?;
            run_deploy(ctx, p, cred, name, false).await
        }
        (None, Some(envelope)) => {
            if !file.sealed_enabled() {
                bail!(SEALED_OFF);
            }
            let login = open_sealed(ctx, envelope).map_err(|e| anyhow!(e))?;
            let name = login.name.clone();
            // The login is zeroized when it drops at the end of this arm.
            run_deploy(ctx, p, &login.credential, &name, true).await
                .map(|d| format!("{d} (login {name:?})"))
                .map_err(|e| anyhow!("{e:#} (login {name:?})"))
        }
        _ => bail!("Invalid deploy command."),
    }
}

/// Log in to the target. For a sealed login a failed connect or login counts
/// towards the throttle's pause.
async fn login(ctx: &ProbeContext, p: &DeployPayload, cred: &Credential, sealed: bool)
    -> anyhow::Result<Session>
{
    let (host, port) = p.target.address();
    let session = connect(host, port, cred, &p.host_key_fingerprint).await;
    if sealed && session.is_err() {
        note_failure(ctx);
    }
    session
}

async fn run_deploy(ctx: &ProbeContext, p: &DeployPayload, cred: &Credential, label: &str, sealed: bool)
    -> anyhow::Result<String>
{
    let (host, _) = p.target.address();
    if !valid_host(host) {
        bail!("Invalid host.");
    }
    if !cred.allows(host) {
        if sealed {
            bail!("{host} is not allowed for this login (its host list).");
        }
        bail!("{host} is not allowed for the credential {label:?} (\"hosts\" in deploy.json).");
    }
    if sealed {
        admit(ctx, host).map_err(|e| anyhow!(e))?;
    }
    if ctx.insecure_tls {
        bail!("This probe accepts an invalid TLS certificate from its server (allow_insecure_tls); \
               an agent it installs would not. Give the server a valid certificate first.");
    }
    let binary = verified_binary(&ctx.api).await?;
    let config = target_config(&ctx.server, &p.enrollment_token);
    let session = login(ctx, p, cred, sealed).await?;
    let result = install(&session, &p.target, &binary, &config).await;
    session.close().await;
    let where_ = match &p.target {
        Target::Host { host, .. } => host.clone(),
        Target::Lxc { host, vmid, .. } => format!("CT {vmid} on {host}"),
    };
    info!(target = %where_, ok = result.is_ok(), "Agent deploy finished");
    result
}

async fn install(s: &Session, target: &Target, binary: &[u8], config: &str) -> anyhow::Result<String> {
    let proc = match target {
        Target::Host { .. } => Procedure::host(s.sudo),
        Target::Lxc { vmid, .. } => Procedure::lxc(s.sudo, *vmid),
    };
    let arch = s.must(&proc.arch(), b"", "Reading the architecture").await?;
    if !SUPPORTED_ARCH.contains(&arch.trim()) {
        bail!("The target is {}; only x86_64 is supported for now.", arch.trim());
    }
    if s.run(&proc.has_systemd(), b"").await?.0 != 0 {
        bail!("The target has no systemd; the agent is installed as a systemd service.");
    }
    // An agent already there: its token, to tell the new enrolment from it.
    let before = match s.run(&proc.read_config(), b"").await {
        Ok((0, cfg)) => token_of(&cfg),
        _ => None,
    };
    match target {
        Target::Host { .. } => {
            s.must(&proc.write("/opt/nodeglow/nodeglow-agent.new", "755"), binary, "Copying the agent").await?;
            s.must(&proc.write("/opt/nodeglow/config.json.new", "600"), config.as_bytes(), "Writing the config").await?;
            s.must(&proc.write(&format!("{UNIT_PATH}.new"), "644"), UNIT.as_bytes(), "Writing the service").await?;
        }
        Target::Lxc { .. } => {
            let tmp = s.must("umask 077 && mktemp -d", b"", "Creating a temp dir on the node").await?;
            let tmp = tmp.trim().to_string();
            if !valid_tmp(&tmp) {
                bail!("Unexpected temp dir on the node.");
            }
            let staged = async {
                s.must(&format!("cat > {tmp}/agent"), binary, "Copying the agent to the node").await?;
                s.must(&format!("cat > {tmp}/config.json"), config.as_bytes(), "Writing the config").await?;
                s.must(&format!("cat > {tmp}/unit"), UNIT.as_bytes(), "Writing the service").await?;
                s.must(&proc.on_target(&format!("mkdir -p {DIR}")), b"", "Creating /opt/nodeglow").await?;
                s.must(&proc.push(&format!("{tmp}/agent"), "/opt/nodeglow/nodeglow-agent.new", "0755"), b"", "pct push").await?;
                s.must(&proc.push(&format!("{tmp}/config.json"), "/opt/nodeglow/config.json.new", "0600"), b"", "pct push").await?;
                s.must(&proc.push(&format!("{tmp}/unit"), &format!("{UNIT_PATH}.new"), "0644"), b"", "pct push").await?;
                anyhow::Ok(())
            }.await;
            let _ = s.run(&format!("rm -rf {tmp}"), b"").await;
            staged?;
        }
    }
    s.must(&proc.activate(), b"", "Starting the agent").await?;

    // The new agent enrols itself on start and writes its token.
    let deadline = tokio::time::Instant::now() + ENROL_WAIT;
    while tokio::time::Instant::now() < deadline {
        tokio::time::sleep(Duration::from_secs(3)).await;
        if let Ok((0, cfg)) = s.run(&proc.read_config(), b"").await {
            if enrolled_anew(&cfg, before.as_deref()) {
                return Ok("Installed; the agent enrolled and is running.".into());
            }
        }
    }
    let journal = s.run(&proc.journal(), b"").await.map(|(_, o)| o).unwrap_or_default();
    bail!("The agent was installed but did not enrol within {} s. Last log lines:\n{}",
          ENROL_WAIT.as_secs(), journal.trim())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_file(dir: &Path, text: &str, mode: u32) -> PathBuf {
        let path = dir.join("deploy.json");
        std::fs::write(&path, text).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
        }
        path
    }

    fn tempdir() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ng-deploy-{}-{}", std::process::id(),
                                                   rand_suffix()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn rand_suffix() -> u128 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
    }

    const FILE: &str = r#"{"allow_deploy_agents": true, "credentials": {
        "lab-root": {"username": "root", "private_key_file": "/root/.ssh/id_ed25519"},
        "pve": {"username": "deploy", "password": "secret-pw"}}}"#;

    const ALLOW: &str = r#"{"allow_deploy_agents": true, "credentials": {
        "lab": {"username": "root", "password": "pw", "hosts": ["web01.lab", "10.0.0.0/24", "*.lab.example", "2001:db8::/32"]},
        "none": {"username": "root", "password": "pw"}}}"#;

    #[test]
    fn a_credential_only_reaches_its_allowed_hosts() {
        let file: DeployFile = serde_json::from_str(ALLOW).unwrap();
        let lab = &file.credentials["lab"];
        for ok in ["web01.lab", "WEB01.lab", "10.0.0.7", "db.lab.example", "[2001:db8::5]"] {
            assert!(lab.allows(ok), "{ok}");
        }
        for bad in ["evil.example", "10.0.1.7", "lab.example", "web01.lab.evil", "x.lab.example.evil", "2001:db9::1"] {
            assert!(!lab.allows(bad), "{bad}");
        }
        // No list: the credential reaches nothing.
        assert!(!file.credentials["none"].allows("web01.lab"));
    }

    #[tokio::test]
    async fn the_server_cannot_send_a_credential_elsewhere() {
        let cfg = crate::config::Config::for_test();
        let file: DeployFile = serde_json::from_str(ALLOW).unwrap();
        let ctx = ProbeContext { deploy_file: Some(file), server: cfg.server.clone(), insecure_tls: false,
                                 api: Arc::new(ApiClient::new(&cfg)),
                                 seal_key_path: PathBuf::new(), throttle: Default::default() };
        let deploy = Command { id: 1, op: "deploy_agent".into(), expires_at: None, payload: json!({
            "target": {"kind": "host", "host": "evil.example", "port": 22}, "credential": "lab",
            "host_key_fingerprint": "SHA256:x", "enrollment_token": "nt_0123456789"})};
        let res = handle(&ctx, deploy).await;
        assert_eq!(res.status, "failed");
        assert!(res.result["detail"].as_str().unwrap().contains("not allowed"), "{:?}", res.result);
        let ask = Command { id: 2, op: "ssh_host_key".into(), expires_at: None,
                            payload: json!({"host": "evil.example", "port": 22})};
        let res = handle(&ctx, ask).await;
        assert_eq!(res.status, "failed");
        assert!(res.result["detail"].as_str().unwrap().contains("not allowed"));
    }

    #[test]
    fn credentials_never_print_their_secrets() {
        let file: DeployFile = serde_json::from_str(FILE).unwrap();
        let text = format!("{:?}", file);
        assert!(!text.contains("secret-pw"), "{text}");
    }

    #[test]
    fn capability_skips_names_the_server_would_drop() {
        let file: DeployFile = serde_json::from_str(r#"{"allow_deploy_agents": true, "credentials": {
            "ok-name": {"username": "root"}, "bad name": {"username": "root"}}}"#).unwrap();
        assert_eq!(capability(Some(&file), None)["credentials"], json!(["ok-name"]));
    }

    #[test]
    fn activation_stops_at_the_first_failure() {
        // Everything up to the restart is chained with &&: a failed mv must
        // not restart the old agent and look like success.
        let restart = ACTIVATE.find("systemctl restart").unwrap();
        let tail = &ACTIVATE[..restart];
        assert!(tail.trim_end().ends_with("&&"), "{ACTIVATE}");
        assert!(!ACTIVATE[ACTIVATE.find("mv -f").unwrap()..].contains("; systemctl restart"));
    }

    #[test]
    fn a_reinstall_needs_a_new_token() {
        let old = r#"{"server":"s","token":"old"}"#;
        assert!(!enrolled_anew(old, Some("old")));
        assert!(enrolled_anew(r#"{"server":"s","token":"new"}"#, Some("old")));
        assert!(enrolled_anew(r#"{"server":"s","token":"new"}"#, None));
        assert!(!enrolled_anew(r#"{"server":"s","token":""}"#, None));
    }

    #[test]
    fn brackets_are_dropped_for_the_connection() {
        assert_eq!(connect_host("[fe80::1]"), "fe80::1");
        assert_eq!(connect_host("web01"), "web01");
    }

    #[cfg(unix)]
    #[test]
    fn deploy_file_needs_private_permissions() {
        let dir = tempdir();
        assert!(DeployFile::load(&write_file(&dir, FILE, 0o644), false).is_none());
        let file = DeployFile::load(&write_file(&dir, FILE, 0o600), false).unwrap();
        assert!(file.enabled());
        assert_eq!(file.credentials["pve"].password.as_deref(), Some("secret-pw"));
    }

    #[cfg(unix)]
    #[test]
    fn deploy_file_must_belong_to_root_in_production() {
        use std::os::unix::fs::MetadataExt;
        let dir = tempdir();
        let path = write_file(&dir, FILE, 0o600);
        let is_root = std::fs::metadata(&path).unwrap().uid() == 0;
        assert_eq!(DeployFile::load(&path, true).is_some(), is_root);
    }

    #[test]
    fn missing_or_broken_file_means_off() {
        let dir = tempdir();
        assert!(DeployFile::load(&dir.join("deploy.json"), false).is_none());
        #[cfg(unix)]
        assert!(DeployFile::load(&write_file(&dir, "{not json", 0o600), false).is_none());
    }

    #[test]
    fn capability_names_credentials_but_never_their_contents() {
        let file: DeployFile = serde_json::from_str(FILE).unwrap();
        let cap = capability(Some(&file), None);
        assert_eq!(cap, json!({"enabled": true, "credentials": ["lab-root", "pve"], "arch": ["x86_64"], "sealed": false}));
        assert!(!cap.to_string().contains("secret-pw"));
        let off = DeployFile { allow_deploy_agents: false, ..file };
        assert_eq!(capability(Some(&off), None)["enabled"], json!(false));
        assert_eq!(capability(None, None)["credentials"], json!([]));
    }

    #[test]
    fn targets_parse_from_the_command() {
        let host: Target = serde_json::from_value(json!({"kind": "host", "host": "10.0.0.5", "port": 2222})).unwrap();
        assert_eq!(host, Target::Host { host: "10.0.0.5".into(), port: 2222 });
        let lxc: Target = serde_json::from_value(json!({"kind": "lxc", "host": "pve1", "vmid": 105})).unwrap();
        assert_eq!(lxc, Target::Lxc { host: "pve1".into(), port: 22, vmid: 105 });
        assert!(serde_json::from_value::<Target>(json!({"kind": "lxc", "host": "pve1", "vmid": "105; rm"})).is_err());
        assert!(serde_json::from_value::<Target>(json!({"kind": "shell", "host": "x"})).is_err());
    }

    #[test]
    fn hosts_cannot_be_options_or_shell() {
        assert!(valid_host("web01.example.com") && valid_host("10.0.0.5") && valid_host("[fe80::1%eth0]"));
        for bad in ["", "-oProxyCommand=x", "a b", "a;b", "a'b", "$(id)"] {
            assert!(!valid_host(bad), "{bad}");
        }
    }

    #[test]
    fn the_target_config_is_bound_to_the_probes_server() {
        let cfg: Value = serde_json::from_str(&target_config("https://ng.example", "nt_tok")).unwrap();
        assert_eq!(cfg, json!({"server": "https://ng.example", "enrollment_key": "nt_tok",
                               "interval": 30, "require_release_signature": true}));
    }

    #[test]
    fn commands_are_fixed_strings() {
        let host = Procedure::host("");
        assert_eq!(host.arch(), "sh -c 'uname -m'");
        assert_eq!(host.write("/opt/nodeglow/config.json.new", "600"),
                   "sh -c 'umask 077 && mkdir -p /opt/nodeglow && cat > /opt/nodeglow/config.json.new && chmod 600 /opt/nodeglow/config.json.new'");
        let lxc = Procedure::lxc("sudo -n ", 105);
        assert_eq!(lxc.arch(), "sudo -n pct exec 105 -- sh -c 'uname -m'");
        assert_eq!(lxc.push("/tmp/tmp.x/agent", "/opt/nodeglow/nodeglow-agent.new", "0755"),
                   "sudo -n pct push 105 /tmp/tmp.x/agent /opt/nodeglow/nodeglow-agent.new --perms 0755");
        assert!(lxc.activate().starts_with("sudo -n pct exec 105 -- sh -c 'systemctl stop"));
        assert!(!ACTIVATE.contains('\''), "ACTIVATE is wrapped in single quotes");
    }

    #[test]
    fn temp_dirs_are_checked() {
        assert!(valid_tmp("/tmp/tmp.AbC123"));
        assert!(!valid_tmp("tmp") && !valid_tmp("/tmp/x; rm -rf /") && !valid_tmp("/tmp/$(id)"));
    }

    #[test]
    fn enrolment_is_read_from_the_token() {
        assert!(enrolled(r#"{"server":"s","token":"abc"}"#));
        assert!(!enrolled(r#"{"server":"s","token":""}"#));
        assert!(!enrolled(r#"{"server":"s","enrollment_key":"nt_x"}"#));
        assert!(!enrolled("garbage"));
    }

    #[test]
    fn redact_hides_the_token_and_keeps_the_tail() {
        let token = "nt_0123456789abcdef";
        let text = format!("{}enrol with {token} failed", "x".repeat(3000));
        let out = redact(&text, token);
        assert!(!out.contains(token) && out.contains("nt_01234…") && out.ends_with("failed"));
        assert!(out.len() <= DETAIL_LIMIT + 3);
    }

    #[test]
    fn expiry_is_read_from_the_command() {
        let now = chrono::Utc::now();
        let mk = |t: &str| Command { id: 1, op: "x".into(), payload: Value::Null, expires_at: Some(t.into()) };
        assert!(expired(&mk(&(now - chrono::Duration::seconds(1)).to_rfc3339()), now));
        assert!(!expired(&mk(&(now + chrono::Duration::seconds(60)).to_rfc3339()), now));
        assert!(!expired(&Command { expires_at: None, ..mk("") }, now));
    }

    #[tokio::test]
    async fn nothing_runs_without_the_local_opt_in() {
        let cfg = crate::config::Config::for_test();
        let ctx = ProbeContext { deploy_file: None, server: cfg.server.clone(), insecure_tls: false,
                                 api: Arc::new(ApiClient::new(&cfg)),
                                 seal_key_path: PathBuf::new(), throttle: Default::default() };
        let cmd = Command { id: 7, op: "deploy_agent".into(), payload: json!({}), expires_at: None };
        let res = handle(&ctx, cmd).await;
        assert_eq!(res.status, "failed");
        assert!(res.result["detail"].as_str().unwrap().contains("not switched on"));
    }

    // ── sealed logins ───────────────────────────────────────────────────────

    const SEALED_ON: &str = r#"{"allow_deploy_agents": true, "allow_sealed_credentials": true}"#;

    fn sealed_ctx(file: &str) -> (ProbeContext, SealKey) {
        let dir = tempdir();
        let path = dir.join("seal.key");
        let key = SealKey::load_or_create(&path, false).unwrap();
        let cfg = crate::config::Config::for_test();
        let ctx = ProbeContext { deploy_file: Some(serde_json::from_str(file).unwrap()),
                                 server: cfg.server.clone(), insecure_tls: false,
                                 api: Arc::new(ApiClient::new(&cfg)), seal_key_path: path,
                                 throttle: Default::default() };
        (ctx, key)
    }

    fn login_for(key: &SealKey, hosts: &[&str]) -> Value {
        seal::seal_for_test(key, &json!({"v": 1, "id": "i1", "name": "lab-root", "username": "root",
            "password": "pw", "private_key": null, "passphrase": null, "hosts": hosts}))
    }

    fn deploy_cmd(extra: Value) -> Command {
        let mut payload = json!({"target": {"kind": "host", "host": "127.0.0.1", "port": 1},
            "host_key_fingerprint": "SHA256:x", "enrollment_token": "nt_0123456789"});
        for (k, v) in extra.as_object().unwrap() {
            payload[k] = v.clone();
        }
        Command { id: 1, op: "deploy_agent".into(), payload, expires_at: None }
    }

    #[tokio::test]
    async fn sealed_refused_without_flag() {
        let (ctx, key) = sealed_ctx(r#"{"allow_deploy_agents": true}"#);
        let env = login_for(&key, &["127.0.0.1"]);
        let res = handle(&ctx, deploy_cmd(json!({"sealed_credential": env.clone()}))).await;
        assert_eq!(res.status, "failed");
        assert_eq!(res.result["detail"], json!("Sealed logins are not switched on on this probe (deploy.json)."));
        let ask = Command { id: 2, op: "ssh_host_key".into(), expires_at: None,
                            payload: json!({"host": "127.0.0.1", "port": 1, "sealed_credential": env})};
        let res = handle(&ctx, ask).await;
        assert_eq!(res.result["detail"], json!("Sealed logins are not switched on on this probe (deploy.json)."));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn sealed_host_not_in_list_refused() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let env = login_for(&key, &["10.9.9.9"]);
        let res = handle(&ctx, deploy_cmd(json!({"sealed_credential": env.clone()}))).await;
        assert_eq!(res.status, "failed");
        assert!(res.result["detail"].as_str().unwrap().contains("not allowed"), "{:?}", res.result);
        let ask = Command { id: 2, op: "ssh_host_key".into(), expires_at: None,
                            payload: json!({"host": "127.0.0.1", "port": 1, "sealed_credential": env})};
        let res = handle(&ctx, ask).await;
        assert!(res.result["detail"].as_str().unwrap().contains("not allowed"), "{:?}", res.result);
    }

    #[tokio::test]
    async fn both_credentials_refused() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let env = login_for(&key, &["127.0.0.1"]);
        let res = handle(&ctx, deploy_cmd(json!({"credential": "lab", "sealed_credential": env}))).await;
        assert_eq!(res.status, "failed");
        assert_eq!(res.result["detail"], json!("Invalid deploy command."));
        let res = handle(&ctx, deploy_cmd(json!({}))).await;
        assert_eq!(res.result["detail"], json!("Invalid deploy command."));
    }

    #[test]
    fn capability_reports_seal_and_sealed_flag() {
        let dir = tempdir();
        let key = SealKey::load_or_create(&dir.join("seal.key"), false).unwrap();
        let on: DeployFile = serde_json::from_str(SEALED_ON).unwrap();
        let cap = capability(Some(&on), Some(&key));
        assert_eq!(cap["sealed"], json!(true));
        assert_eq!(cap["seal"], key.report());
        let half: DeployFile = serde_json::from_str(r#"{"allow_deploy_agents": true}"#).unwrap();
        assert_eq!(capability(Some(&half), Some(&key))["sealed"], json!(false));
        // The key is reported even while deploying is off.
        let off = capability(None, Some(&key));
        assert_eq!(off["enabled"], json!(false));
        assert_eq!(off["seal"], key.report());
        assert!(capability(None, None).get("seal").is_none());
        let only_sealed: DeployFile = serde_json::from_str(r#"{"allow_sealed_credentials": true}"#).unwrap();
        assert_eq!(capability(Some(&only_sealed), None)["sealed"], json!(false));
    }

    #[cfg(unix)]
    #[test]
    fn enable_deploy_keeps_local_credentials() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempdir();
        let path = write_file(&dir, FILE, 0o600);
        set_deploy_flags(&path, true).unwrap();
        let doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(doc["allow_deploy_agents"], json!(true));
        assert_eq!(doc["allow_sealed_credentials"], json!(true));
        assert_eq!(doc["credentials"]["pve"]["password"], json!("secret-pw"));
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        let file = DeployFile::load(&path, false).unwrap();
        assert!(file.sealed_enabled());
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1, "no temp file left behind");
        // A missing file is created.
        let fresh = dir.join("sub");
        std::fs::create_dir_all(&fresh).unwrap();
        let new = fresh.join("deploy.json");
        set_deploy_flags(&new, true).unwrap();
        assert!(DeployFile::load(&new, false).unwrap().sealed_enabled());
    }

    #[cfg(unix)]
    #[test]
    fn disable_deploy_turns_both_off() {
        let dir = tempdir();
        let path = write_file(&dir, FILE, 0o600);
        set_deploy_flags(&path, true).unwrap();
        set_deploy_flags(&path, false).unwrap();
        let file = DeployFile::load(&path, false).unwrap();
        assert!(!file.enabled() && !file.sealed_enabled());
        assert!(file.credentials.contains_key("pve"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn rotate_refused_without_flag() {
        let (ctx, key) = sealed_ctx(r#"{"allow_deploy_agents": true}"#);
        let cmd = Command { id: 3, op: "rotate_seal_key".into(), payload: json!({}), expires_at: None };
        let res = handle(&ctx, cmd).await;
        assert_eq!(res.status, "failed");
        let after = SealKey::load(&ctx.seal_key_path, false).unwrap().unwrap();
        assert_eq!(after.fingerprint(), key.fingerprint(), "key untouched");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn rotate_replaces_the_key() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let cmd = Command { id: 3, op: "rotate_seal_key".into(), payload: json!({}), expires_at: None };
        let res = handle(&ctx, cmd).await;
        assert_eq!(res.status, "ok");
        let after = SealKey::load(&ctx.seal_key_path, false).unwrap().unwrap();
        assert_ne!(after.fingerprint(), key.fingerprint());
        assert_eq!(res.result["public_key"], json!(after.public_b64()));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn throttle_counts_failed_sealed_connect() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let env = login_for(&key, &["127.0.0.1"]);
        let login_ = seal::open(&key, &env).unwrap();
        let payload: DeployPayload = serde_json::from_value(json!({
            "target": {"kind": "host", "host": "127.0.0.1", "port": 1},
            "sealed_credential": env, "host_key_fingerprint": "SHA256:x",
            "enrollment_token": "nt_0123456789"})).unwrap();
        for _ in 0..5 {
            admit(&ctx, "127.0.0.1").expect("admitted");
            let err = login(&ctx, &payload, &login_.credential, true).await.err().expect("refused");
            assert!(!format!("{err:#}").contains("paused"), "{err:#}");
        }
        assert!(admit(&ctx, "127.0.0.1").unwrap_err().contains("paused"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn local_connect_failures_are_not_throttled() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let env = login_for(&key, &["127.0.0.1"]);
        let login_ = seal::open(&key, &env).unwrap();
        let payload: DeployPayload = serde_json::from_value(json!({
            "target": {"kind": "host", "host": "127.0.0.1", "port": 1},
            "credential": "lab", "host_key_fingerprint": "SHA256:x",
            "enrollment_token": "nt_0123456789"})).unwrap();
        for _ in 0..6 {
            login(&ctx, &payload, &login_.credential, false).await.err().expect("refused");
        }
        admit(&ctx, "127.0.0.1").expect("not paused");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn sealed_host_key_failures_are_throttled() {
        let (ctx, key) = sealed_ctx(SEALED_ON);
        let env = login_for(&key, &["127.0.0.1"]);
        let ask = |id| Command { id, op: "ssh_host_key".into(), expires_at: None,
                                 payload: json!({"host": "127.0.0.1", "port": 1, "sealed_credential": env.clone()}) };
        for i in 0..5 {
            let res = handle(&ctx, ask(i)).await;
            assert_eq!(res.status, "failed");
            assert!(!res.result["detail"].as_str().unwrap().contains("paused"), "{:?}", res.result);
        }
        let res = handle(&ctx, ask(6)).await;
        assert!(res.result["detail"].as_str().unwrap().contains("paused"), "{:?}", res.result);
    }

    #[tokio::test]
    async fn unknown_ops_are_refused() {
        let cfg = crate::config::Config::for_test();
        let file: DeployFile = serde_json::from_str(FILE).unwrap();
        let ctx = ProbeContext { deploy_file: Some(file), server: cfg.server.clone(), insecure_tls: false,
                                 api: Arc::new(ApiClient::new(&cfg)),
                                 seal_key_path: PathBuf::new(), throttle: Default::default() };
        let cmd = Command { id: 8, op: "run_shell".into(), payload: json!({"cmd": "id"}), expires_at: None };
        assert_eq!(handle(&ctx, cmd).await.status, "failed");
    }
}

/// Against a real SSH server (russh, in process): the key check happens
/// before any login, commands and stdin arrive as sent.
#[cfg(test)]
mod ssh_tests {
    use super::*;
    use russh::server::{self, Auth, ChannelOpenHandle, Msg, Server as _};
    use russh::{Channel, ChannelId};
    use std::collections::HashMap;
    use std::sync::Mutex as StdMutex;

    #[derive(Default)]
    struct Seen {
        auth_attempts: usize,
        commands: Vec<String>,
        stdin: HashMap<String, Vec<u8>>,
    }

    #[derive(Clone)]
    struct TestServer {
        seen: Arc<StdMutex<Seen>>,
        current: Arc<StdMutex<HashMap<ChannelId, (String, Vec<u8>)>>>,
    }

    impl server::Server for TestServer {
        type Handler = Self;
        fn new_client(&mut self, _: Option<std::net::SocketAddr>) -> Self {
            self.clone()
        }
    }

    impl server::Handler for TestServer {
        type Error = russh::Error;

        async fn auth_password(&mut self, _user: &str, password: &str) -> Result<Auth, Self::Error> {
            self.seen.lock().unwrap().auth_attempts += 1;
            Ok(if password == "pw" { Auth::Accept } else { Auth::reject() })
        }

        async fn auth_none(&mut self, _user: &str) -> Result<Auth, Self::Error> {
            self.seen.lock().unwrap().auth_attempts += 1;
            Ok(Auth::reject())
        }

        async fn channel_open_session(&mut self, _channel: Channel<Msg>, reply: ChannelOpenHandle,
                                      _session: &mut server::Session) -> Result<(), Self::Error> {
            reply.accept().await;
            Ok(())
        }

        async fn exec_request(&mut self, channel: ChannelId, data: &[u8],
                              session: &mut server::Session) -> Result<(), Self::Error> {
            let cmd = String::from_utf8_lossy(data).into_owned();
            self.seen.lock().unwrap().commands.push(cmd.clone());
            self.current.lock().unwrap().insert(channel, (cmd, Vec::new()));
            session.channel_success(channel)?;
            Ok(())
        }

        async fn data(&mut self, channel: ChannelId, data: &[u8],
                      _session: &mut server::Session) -> Result<(), Self::Error> {
            if let Some((_, buf)) = self.current.lock().unwrap().get_mut(&channel) {
                buf.extend_from_slice(data);
            }
            Ok(())
        }

        async fn channel_eof(&mut self, channel: ChannelId,
                             session: &mut server::Session) -> Result<(), Self::Error> {
            let (cmd, stdin) = self.current.lock().unwrap().remove(&channel).unwrap_or_default();
            let reply = if cmd.contains("uname -m") { "x86_64\n".to_string() } else { format!("ran {cmd}\n") };
            self.seen.lock().unwrap().stdin.insert(cmd, stdin);
            session.data(channel, reply.into_bytes())?;
            session.exit_status_request(channel, 0)?;
            session.eof(channel)?;
            session.close(channel)?;
            Ok(())
        }
    }

    fn host_key(seed: u8) -> russh::keys::PrivateKey {
        russh::keys::PrivateKey::from(russh::keys::ssh_key::private::Ed25519Keypair::from_seed(&[seed; 32]))
    }

    async fn start() -> (u16, Arc<StdMutex<Seen>>, String) {
        let key = host_key(7);
        let fp = fingerprint(key.public_key());
        let config = Arc::new(server::Config {
            keys: vec![key],
            auth_rejection_time: Duration::from_millis(1),
            auth_rejection_time_initial: Some(Duration::from_millis(0)),
            ..Default::default()
        });
        let seen = Arc::new(StdMutex::new(Seen::default()));
        let mut srv = TestServer { seen: seen.clone(), current: Default::default() };
        let socket = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = socket.local_addr().unwrap().port();
        tokio::spawn(async move {
            let _ = srv.run_on_socket(config, &socket).await;
        });
        (port, seen, fp)
    }

    fn root() -> Credential {
        Credential { username: "root".into(), password: Some("pw".into()), private_key_file: None, private_key: None,
                     passphrase: None, hosts: vec!["127.0.0.1".into()] }
    }

    #[tokio::test]
    async fn reads_the_host_key_without_logging_in() {
        let (port, seen, fp) = start().await;
        let key = fetch_host_key("127.0.0.1", port).await.unwrap();
        assert_eq!(key["fingerprint"], json!(fp));
        assert_eq!(key["key_type"], json!("ssh-ed25519"));
        assert!(key["public_key"].as_str().unwrap().starts_with("ssh-ed25519 "));
        assert_eq!(seen.lock().unwrap().auth_attempts, 0);
    }

    #[tokio::test]
    async fn a_different_host_key_is_refused_before_login() {
        let (port, seen, _fp) = start().await;
        let other = fingerprint(host_key(9).public_key());
        let err = connect("127.0.0.1", port, &root(), &other).await.err().expect("refused");
        assert!(format!("{err:#}").contains("changed"), "{err:#}");
        let seen = seen.lock().unwrap();
        assert_eq!(seen.auth_attempts, 0);
        assert!(seen.commands.is_empty());
    }

    #[tokio::test]
    async fn runs_fixed_commands_with_stdin() {
        let (port, seen, fp) = start().await;
        let s = connect("127.0.0.1", port, &root(), &fp).await.unwrap();
        let proc = Procedure::host(s.sudo);
        assert_eq!(s.must(&proc.arch(), b"", "arch").await.unwrap().trim(), "x86_64");
        let write = proc.write("/opt/nodeglow/config.json.new", "600");
        s.must(&write, b"{\"server\":\"s\"}", "config").await.unwrap();
        s.close().await;
        let seen = seen.lock().unwrap();
        assert_eq!(seen.commands, vec![proc.arch(), write.clone()]);
        assert_eq!(seen.stdin[&write], b"{\"server\":\"s\"}".to_vec());
    }

    #[tokio::test]
    async fn a_wrong_password_is_reported() {
        let (port, _seen, fp) = start().await;
        let cred = Credential { password: Some("nope".into()), ..root() };
        let err = connect("127.0.0.1", port, &cred, &fp).await.err().expect("refused");
        assert!(format!("{err:#}").contains("refused"), "{err:#}");
    }

    #[tokio::test]
    async fn non_root_goes_through_sudo_n() {
        let (port, seen, fp) = start().await;
        let cred = Credential { username: "deploy".into(), ..root() };
        let s = connect("127.0.0.1", port, &cred, &fp).await.unwrap();
        s.must(&Procedure::lxc(s.sudo, 105).arch(), b"", "arch").await.unwrap();
        s.close().await;
        assert_eq!(seen.lock().unwrap().commands, vec!["sudo -n pct exec 105 -- sh -c 'uname -m'"]);
    }
}

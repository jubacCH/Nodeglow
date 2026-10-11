//! Probe syslog relay.
//!
//! A probe sits inside the customer network, already enrolled into exactly
//! one tenant and site. With the relay switched on (heartbeat config
//! `syslog_relay: {"enabled": true, "port": 514}`), it listens for syslog on
//! UDP and TCP and forwards the raw lines over its own authenticated channel
//! (`POST /api/agent/logs`, one entry per message with the device's address
//! and the receive time). The backend parses them and files them under the
//! probe's tenant and site — devices that cannot authenticate never have to
//! be mapped to a customer by their address.
//!
//! Delivery is decoupled from reception: received lines go into a bounded
//! in-memory buffer; a forwarder sends batches every couple of seconds (or as
//! soon as a batch is full) and puts a batch back when the backend is
//! unreachable. When the buffer is full the oldest lines are discarded and
//! counted. The counters ride the heartbeat (`syslog_relay` in the report).
//!
//! An agent that is never told to relay never binds a port, never spawns a
//! task and sends exactly the heartbeat it always did.

use std::collections::VecDeque;
use std::net::IpAddr;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::io::AsyncReadExt;
use tokio::net::{TcpListener, TcpStream, UdpSocket};
use tokio::sync::Notify;
use tokio::task::JoinHandle;
use tracing::{debug, info, warn};

use crate::client::ApiClient;

/// Port the relay listens on when the server names none.
pub const DEFAULT_PORT: u16 = 514;
/// The backend refuses larger messages (`services/syslog.py`, 64 KiB).
pub const MAX_MESSAGE_BYTES: usize = 64 * 1024;
/// Entries per request; the backend takes at most 500 per call.
pub const BATCH_SIZE: usize = 500;
/// Approximate payload bytes per request, so a burst of large messages does
/// not become one huge body.
const BATCH_MAX_BYTES: usize = 1024 * 1024;
/// Buffer caps while the backend cannot be reached.
const MAX_BUFFERED_ENTRIES: usize = 20_000;
const MAX_BUFFERED_BYTES: usize = 16 * 1024 * 1024;
const FLUSH_INTERVAL: Duration = Duration::from_secs(2);
const RETRY_BACKOFF_MAX: Duration = Duration::from_secs(60);
/// A port that could not be bound is tried again after this long.
const BIND_RETRY: Duration = Duration::from_secs(60);
const MAX_TCP_CONNECTIONS: usize = 64;
const TCP_IDLE_TIMEOUT: Duration = Duration::from_secs(300);
/// How often blocked reads look at the stop flag.
const POLL: Duration = Duration::from_secs(1);

// ── Configuration ────────────────────────────────────────────────────────────

/// What the server asked for. `None` (absent, `null`, `enabled: false`, or
/// anything unparsable) means: no relay.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RelayConfig {
    pub port: u16,
}

impl RelayConfig {
    /// Read the `syslog_relay` value of the heartbeat config leniently: a
    /// malformed value must never fail the heartbeat, it just means "off"
    /// (or the default port).
    pub fn from_server(value: Option<&serde_json::Value>) -> Option<RelayConfig> {
        let v = value?;
        if !v.get("enabled").and_then(|e| e.as_bool()).unwrap_or(false) {
            return None;
        }
        let port = v
            .get("port")
            .and_then(|p| p.as_u64())
            .filter(|p| *p > 0 && *p <= u64::from(u16::MAX))
            .map(|p| p as u16)
            .unwrap_or(DEFAULT_PORT);
        Some(RelayConfig { port })
    }
}

// ── Entries and the bounded buffer ───────────────────────────────────────────

/// One received syslog line as the backend expects it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RelayEntry {
    /// The message as received (framing removed), lossily UTF-8.
    pub raw: String,
    /// Address of the device that sent it.
    pub source_ip: String,
    /// RFC3339 UTC, when this probe received it.
    pub received_at: String,
    /// "udp" or "tcp".
    pub transport: &'static str,
}

impl RelayEntry {
    fn size(&self) -> usize {
        // JSON overhead per entry is roughly constant; 64 is generous.
        self.raw.len() + self.source_ip.len() + self.received_at.len() + 64
    }
}

/// Bounded FIFO of entries waiting for delivery. Full means: the oldest go.
#[derive(Debug)]
pub struct RelayBuffer {
    entries: VecDeque<RelayEntry>,
    bytes: usize,
    max_entries: usize,
    max_bytes: usize,
}

impl RelayBuffer {
    pub fn new(max_entries: usize, max_bytes: usize) -> Self {
        Self {
            entries: VecDeque::new(),
            bytes: 0,
            max_entries: max_entries.max(1),
            max_bytes: max_bytes.max(1),
        }
    }

    fn fits(&self, size: usize) -> bool {
        self.entries.len() < self.max_entries && self.bytes + size <= self.max_bytes
    }

    /// Append an entry, discarding the oldest ones until it fits. Returns how
    /// many were discarded.
    pub fn push(&mut self, entry: RelayEntry) -> u64 {
        let size = entry.size();
        let mut dropped = 0u64;
        while !self.entries.is_empty() && !self.fits(size) {
            if let Some(old) = self.entries.pop_front() {
                self.bytes -= old.size();
                dropped += 1;
            }
        }
        self.bytes += size;
        self.entries.push_back(entry);
        dropped
    }

    /// Take the oldest entries for one request: at most `max_entries`, and no
    /// more than `max_bytes` — but always at least one if any are waiting.
    pub fn take_batch(&mut self, max_entries: usize, max_bytes: usize) -> Vec<RelayEntry> {
        let mut batch = Vec::new();
        let mut bytes = 0usize;
        while batch.len() < max_entries {
            let size = match self.entries.front() {
                Some(e) => e.size(),
                None => break,
            };
            if !batch.is_empty() && bytes + size > max_bytes {
                break;
            }
            if let Some(e) = self.entries.pop_front() {
                self.bytes -= size;
                bytes += size;
                batch.push(e);
            }
        }
        batch
    }

    /// Put an undelivered batch back in front, in its order. The batch is
    /// older than anything received meanwhile, so if not all of it fits, its
    /// oldest entries are the ones discarded. Returns how many were.
    pub fn requeue_front(&mut self, batch: Vec<RelayEntry>) -> u64 {
        let mut dropped = 0u64;
        for entry in batch.into_iter().rev() {
            let size = entry.size();
            if self.fits(size) {
                self.bytes += size;
                self.entries.push_front(entry);
            } else {
                dropped += 1;
            }
        }
        dropped
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }
}

// ── TCP framing (RFC 6587) ───────────────────────────────────────────────────

/// Splits a TCP byte stream into syslog messages. Understands both framings
/// devices use: octet counting (`"<len> <msg>"`, RFC 6587 3.4.1) and
/// non-transparent framing (messages ended by LF or NUL, 3.4.2).
#[derive(Debug, Default)]
pub struct Framer {
    buf: Vec<u8>,
}

/// The stream can no longer be framed (an announced length above the
/// message limit); the connection is closed.
#[derive(Debug, PartialEq, Eq)]
pub struct FramingError;

impl Framer {
    pub fn new() -> Self {
        Self { buf: Vec::new() }
    }

    /// Feed bytes; returns the complete messages they finished, and how many
    /// oversized messages were discarded on the way.
    pub fn push(&mut self, data: &[u8]) -> Result<(Vec<Vec<u8>>, u64), FramingError> {
        self.buf.extend_from_slice(data);
        let mut frames = Vec::new();
        let mut oversized = 0u64;
        loop {
            if self.buf.is_empty() {
                break;
            }
            match self.octet_counted()? {
                Octets::Frame(start, len) => {
                    frames.push(self.buf[start..start + len].to_vec());
                    self.buf.drain(..start + len);
                    continue;
                }
                Octets::NeedMore => break,
                Octets::NotOctetCounted => {}
            }
            match self.buf.iter().position(|b| *b == b'\n' || *b == 0) {
                Some(pos) => {
                    let mut frame: Vec<u8> = self.buf.drain(..=pos).collect();
                    frame.pop(); // the terminator
                    if frame.last() == Some(&b'\r') {
                        frame.pop();
                    }
                    if frame.len() > MAX_MESSAGE_BYTES {
                        oversized += 1;
                    } else if !frame.is_empty() {
                        frames.push(frame);
                    }
                }
                None => {
                    if self.buf.len() > MAX_MESSAGE_BYTES {
                        // No terminator in sight: discard what we have and
                        // skip to the next one.
                        self.buf.clear();
                        oversized += 1;
                    }
                    break;
                }
            }
        }
        Ok((frames, oversized))
    }

    /// The connection ended: whatever is left is the last message (many
    /// senders do not terminate it).
    pub fn finish(&mut self) -> Option<Vec<u8>> {
        let mut rest = std::mem::take(&mut self.buf);
        while matches!(rest.last().copied(), Some(b'\r' | b'\n' | 0)) {
            rest.pop();
        }
        if rest.is_empty() || rest.len() > MAX_MESSAGE_BYTES {
            None
        } else {
            Some(rest)
        }
    }

    fn octet_counted(&self) -> Result<Octets, FramingError> {
        let b = &self.buf;
        if !(b'1'..=b'9').contains(&b[0]) {
            return Ok(Octets::NotOctetCounted);
        }
        // At most 6 digits: MAX_MESSAGE_BYTES has 5.
        let digits = b.iter().take(7).take_while(|c| c.is_ascii_digit()).count();
        if digits == b.len() {
            return Ok(if digits < 7 { Octets::NeedMore } else { Octets::NotOctetCounted });
        }
        if b[digits] != b' ' || digits > 6 {
            return Ok(Octets::NotOctetCounted);
        }
        // "123 " must be followed by a PRI to be a length, not text.
        if b.len() == digits + 1 {
            return Ok(Octets::NeedMore);
        }
        if b[digits + 1] != b'<' {
            return Ok(Octets::NotOctetCounted);
        }
        let len: usize = std::str::from_utf8(&b[..digits])
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(usize::MAX);
        if len > MAX_MESSAGE_BYTES {
            return Err(FramingError);
        }
        if b.len() < digits + 1 + len {
            return Ok(Octets::NeedMore);
        }
        Ok(Octets::Frame(digits + 1, len))
    }
}

enum Octets {
    Frame(usize, usize),
    NeedMore,
    NotOctetCounted,
}

// ── Shared state of the listeners and the forwarder ──────────────────────────

/// Relay counters as reported in the heartbeat (`syslog_relay`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RelayStatus {
    pub listening: bool,
    pub port: u16,
    pub received: u64,
    pub forwarded: u64,
    pub dropped: u64,
    pub buffered: u64,
    pub send_failures: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

struct Shared {
    buffer: Mutex<RelayBuffer>,
    received: AtomicU64,
    forwarded: AtomicU64,
    dropped: AtomicU64,
    send_failures: AtomicU64,
    tcp_connections: AtomicUsize,
    notify: Notify,
}

impl Shared {
    fn new() -> Self {
        Self {
            buffer: Mutex::new(RelayBuffer::new(MAX_BUFFERED_ENTRIES, MAX_BUFFERED_BYTES)),
            received: AtomicU64::new(0),
            forwarded: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            send_failures: AtomicU64::new(0),
            tcp_connections: AtomicUsize::new(0),
            notify: Notify::new(),
        }
    }

    fn buffer(&self) -> MutexGuard<'_, RelayBuffer> {
        self.buffer.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// One message off the wire.
    fn accept(&self, frame: &[u8], ip: IpAddr, transport: &'static str) {
        if frame.len() > MAX_MESSAGE_BYTES {
            self.dropped.fetch_add(1, Ordering::Relaxed);
            return;
        }
        let text = String::from_utf8_lossy(frame);
        let text = text.trim_matches(|c: char| c == '\r' || c == '\n' || c == '\0');
        if text.trim().is_empty() {
            return;
        }
        self.received.fetch_add(1, Ordering::Relaxed);
        let entry = RelayEntry {
            raw: text.to_string(),
            source_ip: plain_ip(ip).to_string(),
            received_at: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            transport,
        };
        let (dropped, buffered) = {
            let mut buf = self.buffer();
            let dropped = buf.push(entry);
            (dropped, buf.len())
        };
        if dropped > 0 {
            self.dropped.fetch_add(dropped, Ordering::Relaxed);
        }
        if buffered >= BATCH_SIZE {
            self.notify.notify_one();
        }
    }
}

/// An IPv4 device seen through a dual-stack socket is still an IPv4 device.
fn plain_ip(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        v4 => v4,
    }
}

// ── Listeners ────────────────────────────────────────────────────────────────

struct Listeners {
    tasks: Vec<JoinHandle<()>>,
    stop: Arc<AtomicBool>,
}

impl Listeners {
    fn stop(self) {
        self.stop.store(true, Ordering::Relaxed);
        for t in self.tasks {
            t.abort();
        }
    }
}

/// Bind UDP and TCP on `addr:port` (port 0: each its own ephemeral port).
async fn bind(addr: &str, port: u16) -> std::io::Result<(UdpSocket, TcpListener)> {
    let udp = UdpSocket::bind((addr, port)).await?;
    let tcp = TcpListener::bind((addr, port)).await?;
    Ok((udp, tcp))
}

fn spawn_listeners(shared: &Arc<Shared>, udp: UdpSocket, tcp: TcpListener) -> Listeners {
    let stop = Arc::new(AtomicBool::new(false));
    let tasks = vec![
        tokio::spawn(udp_loop(udp, Arc::clone(shared), Arc::clone(&stop))),
        tokio::spawn(tcp_accept_loop(tcp, Arc::clone(shared), Arc::clone(&stop))),
    ];
    Listeners { tasks, stop }
}

async fn udp_loop(socket: UdpSocket, shared: Arc<Shared>, stop: Arc<AtomicBool>) {
    // The largest UDP payload; a datagram is one message (RFC 5426).
    let mut buf = vec![0u8; 65_536];
    while !stop.load(Ordering::Relaxed) {
        let received = tokio::time::timeout(POLL, socket.recv_from(&mut buf)).await;
        match received {
            Err(_) => continue, // nothing within POLL; look at the stop flag
            Ok(Ok((n, addr))) => shared.accept(&buf[..n], addr.ip(), "udp"),
            Ok(Err(e)) => {
                // e.g. an ICMP error surfaced on Windows; never spin on it.
                debug!("Syslog relay UDP receive error: {e}");
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
    }
}

async fn tcp_accept_loop(listener: TcpListener, shared: Arc<Shared>, stop: Arc<AtomicBool>) {
    while !stop.load(Ordering::Relaxed) {
        let (stream, addr) = match tokio::time::timeout(POLL, listener.accept()).await {
            Err(_) => continue,
            Ok(Ok(pair)) => pair,
            Ok(Err(e)) => {
                debug!("Syslog relay TCP accept error: {e}");
                tokio::time::sleep(Duration::from_millis(50)).await;
                continue;
            }
        };
        if shared.tcp_connections.fetch_add(1, Ordering::Relaxed) >= MAX_TCP_CONNECTIONS {
            shared.tcp_connections.fetch_sub(1, Ordering::Relaxed);
            shared.dropped.fetch_add(1, Ordering::Relaxed);
            warn!("Syslog relay: TCP connection limit ({MAX_TCP_CONNECTIONS}) reached, refusing {addr}");
            continue; // dropping the stream closes it
        }
        let shared = Arc::clone(&shared);
        let stop = Arc::clone(&stop);
        tokio::spawn(async move {
            tcp_connection(stream, addr.ip(), &shared, &stop).await;
            shared.tcp_connections.fetch_sub(1, Ordering::Relaxed);
        });
    }
}

async fn tcp_connection(mut stream: TcpStream, ip: IpAddr, shared: &Shared, stop: &AtomicBool) {
    let mut framer = Framer::new();
    let mut buf = vec![0u8; 16 * 1024];
    let mut idle = Duration::ZERO;
    loop {
        if stop.load(Ordering::Relaxed) {
            return; // relay switched off: drop the connection, and its rest
        }
        let read = tokio::time::timeout(POLL, stream.read(&mut buf)).await;
        let n = match read {
            Err(_) => {
                idle += POLL;
                if idle >= TCP_IDLE_TIMEOUT {
                    break;
                }
                continue;
            }
            Ok(Ok(0)) | Ok(Err(_)) => break,
            Ok(Ok(n)) => n,
        };
        idle = Duration::ZERO;
        match framer.push(&buf[..n]) {
            Ok((frames, oversized)) => {
                if oversized > 0 {
                    shared.dropped.fetch_add(oversized, Ordering::Relaxed);
                }
                for f in frames {
                    shared.accept(&f, ip, "tcp");
                }
            }
            Err(FramingError) => {
                shared.dropped.fetch_add(1, Ordering::Relaxed);
                debug!("Syslog relay: unframeable TCP stream from {ip}, closing");
                return;
            }
        }
    }
    if let Some(last) = framer.finish() {
        shared.accept(&last, ip, "tcp");
    }
}

// ── Forwarder ────────────────────────────────────────────────────────────────

async fn forward_loop(shared: Arc<Shared>, api: Arc<ApiClient>, hostname: String) {
    let mut failing = false;
    let mut backoff = FLUSH_INTERVAL;
    loop {
        if failing {
            // The backend is unreachable: wait out the backoff, whatever
            // arrives meanwhile (it is buffered).
            tokio::time::sleep(backoff).await;
        } else {
            let _ = tokio::time::timeout(FLUSH_INTERVAL, shared.notify.notified()).await;
        }
        loop {
            let batch = shared.buffer().take_batch(BATCH_SIZE, BATCH_MAX_BYTES);
            if batch.is_empty() {
                break;
            }
            let n = batch.len() as u64;
            match api.send_relay_logs(&hostname, &batch).await {
                Ok(()) => {
                    shared.forwarded.fetch_add(n, Ordering::Relaxed);
                    if failing {
                        info!("Syslog relay: backend reachable again, forwarding");
                    }
                    failing = false;
                    backoff = FLUSH_INTERVAL;
                }
                Err(e) => {
                    shared.send_failures.fetch_add(1, Ordering::Relaxed);
                    let dropped = shared.buffer().requeue_front(batch);
                    if dropped > 0 {
                        shared.dropped.fetch_add(dropped, Ordering::Relaxed);
                    }
                    if !failing {
                        warn!("Syslog relay: forwarding failed, buffering: {e}");
                    }
                    backoff = if failing { (backoff * 2).min(RETRY_BACKOFF_MAX) } else { FLUSH_INTERVAL };
                    failing = true;
                    break;
                }
            }
        }
    }
}

// ── The relay ────────────────────────────────────────────────────────────────

/// Owns the listeners and the forwarder; driven by the heartbeat loop.
pub struct SyslogRelay {
    shared: Arc<Shared>,
    api: Arc<ApiClient>,
    hostname: String,
    bind_addr: String,
    current: Option<RelayConfig>,
    listeners: Option<Listeners>,
    forwarder: Option<JoinHandle<()>>,
    error: Option<String>,
    retry_at: Option<Instant>,
    configured_once: bool,
}

impl SyslogRelay {
    pub fn new(api: Arc<ApiClient>, hostname: String) -> Self {
        Self {
            shared: Arc::new(Shared::new()),
            api,
            hostname,
            bind_addr: "0.0.0.0".to_string(),
            current: None,
            listeners: None,
            forwarder: None,
            error: None,
            retry_at: None,
            configured_once: false,
        }
    }

    /// Follow the config of the latest heartbeat: start, move to another
    /// port, retry a failed bind (once a minute), or stop.
    pub async fn apply(&mut self, cfg: Option<RelayConfig>) {
        if cfg == self.current {
            let bind_pending = cfg.is_some() && self.listeners.is_none();
            let retry_due = self.retry_at.is_none_or(|t| Instant::now() >= t);
            if !bind_pending || !retry_due {
                return; // unchanged, or a failed bind not yet due again
            }
        }
        if let Some(l) = self.listeners.take() {
            l.stop();
            info!("Syslog relay stopped");
        }
        self.current = cfg;
        self.retry_at = None;
        let Some(cfg) = cfg else {
            self.error = None;
            // The forwarder stays: it delivers what is still buffered.
            return;
        };
        self.configured_once = true;
        match bind(&self.bind_addr, cfg.port).await {
            Ok((udp, tcp)) => {
                self.listeners = Some(spawn_listeners(&self.shared, udp, tcp));
                self.error = None;
                if self.forwarder.is_none() {
                    self.forwarder = Some(tokio::spawn(forward_loop(
                        Arc::clone(&self.shared),
                        Arc::clone(&self.api),
                        self.hostname.clone(),
                    )));
                }
                info!("Syslog relay listening on UDP/TCP {}:{}", self.bind_addr, cfg.port);
            }
            Err(e) => {
                let msg = format!("cannot listen on port {}: {e}", cfg.port);
                warn!("Syslog relay {msg} (retrying every {}s)", BIND_RETRY.as_secs());
                self.error = Some(msg);
                self.retry_at = Some(Instant::now() + BIND_RETRY);
            }
        }
    }

    /// Counters for the heartbeat; `None` until the relay was ever switched
    /// on, so a plain agent's heartbeat stays byte-for-byte what it was.
    pub fn status(&self) -> Option<RelayStatus> {
        if !self.configured_once {
            return None;
        }
        Some(RelayStatus {
            listening: self.listeners.is_some(),
            port: self.current.map(|c| c.port).unwrap_or(0),
            received: self.shared.received.load(Ordering::Relaxed),
            forwarded: self.shared.forwarded.load(Ordering::Relaxed),
            dropped: self.shared.dropped.load(Ordering::Relaxed),
            buffered: self.shared.buffer().len() as u64,
            send_failures: self.shared.send_failures.load(Ordering::Relaxed),
            error: self.error.clone(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn entry(raw: &str) -> RelayEntry {
        RelayEntry {
            raw: raw.to_string(),
            source_ip: "10.0.0.5".to_string(),
            received_at: "2026-10-11T10:00:00.000Z".to_string(),
            transport: "udp",
        }
    }

    fn raws(entries: &[RelayEntry]) -> Vec<&str> {
        entries.iter().map(|e| e.raw.as_str()).collect()
    }

    // ── config ──────────────────────────────────────────────────────────────

    #[test]
    fn config_absent_or_off_means_no_relay() {
        assert_eq!(RelayConfig::from_server(None), None);
        assert_eq!(RelayConfig::from_server(Some(&json!(null))), None);
        assert_eq!(RelayConfig::from_server(Some(&json!({"enabled": false, "port": 514}))), None);
        assert_eq!(RelayConfig::from_server(Some(&json!({"port": 514}))), None);
        assert_eq!(RelayConfig::from_server(Some(&json!({"enabled": "yes"}))), None);
        assert_eq!(RelayConfig::from_server(Some(&json!([1, 2]))), None);
    }

    #[test]
    fn config_port_defaults_and_bad_ports_fall_back() {
        let on = |v| RelayConfig::from_server(Some(&v)).map(|c| c.port);
        assert_eq!(on(json!({"enabled": true})), Some(514));
        assert_eq!(on(json!({"enabled": true, "port": 1514})), Some(1514));
        assert_eq!(on(json!({"enabled": true, "port": 0})), Some(514));
        assert_eq!(on(json!({"enabled": true, "port": 70000})), Some(514));
        assert_eq!(on(json!({"enabled": true, "port": -1})), Some(514));
        assert_eq!(on(json!({"enabled": true, "port": "1514"})), Some(514));
    }

    // ── buffer ──────────────────────────────────────────────────────────────

    #[test]
    fn buffer_drops_the_oldest_when_full_and_counts() {
        let mut b = RelayBuffer::new(3, usize::MAX);
        for i in 0..3 {
            assert_eq!(b.push(entry(&format!("m{i}"))), 0);
        }
        assert_eq!(b.push(entry("m3")), 1);
        assert_eq!(b.push(entry("m4")), 1);
        assert_eq!(b.len(), 3);
        assert_eq!(raws(&b.take_batch(10, usize::MAX)), vec!["m2", "m3", "m4"]);
        assert_eq!(b.len(), 0);
    }

    #[test]
    fn buffer_is_bounded_by_bytes_too() {
        let one = entry("x").size();
        let mut b = RelayBuffer::new(1000, one * 2);
        b.push(entry("a"));
        b.push(entry("b"));
        assert_eq!(b.push(entry("c")), 1);
        assert_eq!(raws(&b.take_batch(10, usize::MAX)), vec!["b", "c"]);
        // A single entry larger than the byte cap still gets in, alone.
        let mut b = RelayBuffer::new(1000, 10);
        b.push(entry("a"));
        assert_eq!(b.push(entry("a much longer line")), 1);
        assert_eq!(b.len(), 1);
    }

    #[test]
    fn batches_respect_count_and_bytes_but_never_starve() {
        let mut b = RelayBuffer::new(100, usize::MAX);
        for i in 0..10 {
            b.push(entry(&format!("m{i}")));
        }
        assert_eq!(b.take_batch(4, usize::MAX).len(), 4);
        let one = entry("m0").size();
        assert_eq!(b.take_batch(100, one * 2).len(), 2);
        assert_eq!(b.take_batch(100, 1).len(), 1, "always at least one entry");
        assert_eq!(b.len(), 3);
    }

    #[test]
    fn an_undelivered_batch_goes_back_in_front_in_order() {
        let mut b = RelayBuffer::new(100, usize::MAX);
        for i in 0..5 {
            b.push(entry(&format!("m{i}")));
        }
        let batch = b.take_batch(3, usize::MAX);
        b.push(entry("m5"));
        assert_eq!(b.requeue_front(batch), 0);
        assert_eq!(raws(&b.take_batch(100, usize::MAX)), vec!["m0", "m1", "m2", "m3", "m4", "m5"]);
    }

    #[test]
    fn requeue_into_a_full_buffer_drops_the_oldest_of_the_batch() {
        let mut b = RelayBuffer::new(4, usize::MAX);
        for i in 0..4 {
            b.push(entry(&format!("m{i}")));
        }
        let batch = b.take_batch(3, usize::MAX); // m0..m2 out, m3 left
        b.push(entry("m4"));
        b.push(entry("m5")); // m3 m4 m5 — one slot free
        assert_eq!(b.requeue_front(batch), 2);
        assert_eq!(raws(&b.take_batch(100, usize::MAX)), vec!["m2", "m3", "m4", "m5"]);
    }

    // ── framing ─────────────────────────────────────────────────────────────

    fn texts(frames: Vec<Vec<u8>>) -> Vec<String> {
        frames.into_iter().map(|f| String::from_utf8(f).unwrap()).collect()
    }

    #[test]
    fn newline_framing_with_crlf_and_nul() {
        let mut f = Framer::new();
        let (frames, oversized) = f.push(b"<13>one\n<13>two\r\n<13>three\0<13>par").unwrap();
        assert_eq!(texts(frames), vec!["<13>one", "<13>two", "<13>three"]);
        assert_eq!(oversized, 0);
        let (frames, _) = f.push(b"tial\n\n").unwrap();
        assert_eq!(texts(frames), vec!["<13>partial"]);
        assert_eq!(f.finish(), None);
    }

    #[test]
    fn octet_counting_across_reads() {
        let mut f = Framer::new();
        // "<13>hello 1" and "<13>world!!" are 11 octets each.
        let (frames, _) = f.push(b"11 <13>hel").unwrap();
        assert!(frames.is_empty());
        let (frames, _) = f.push(b"lo 111 <13>wor").unwrap();
        assert_eq!(texts(frames), vec!["<13>hello 1"]);
        let (frames, _) = f.push(b"ld!!").unwrap();
        assert_eq!(texts(frames), vec!["<13>world!!"]);
        assert_eq!(f.finish(), None);
    }

    #[test]
    fn octet_counted_messages_may_contain_newlines() {
        let mut f = Framer::new();
        let (frames, _) = f.push(b"11 <13>a\nb\nc\nd8 <14>next").unwrap();
        assert_eq!(texts(frames), vec!["<13>a\nb\nc\nd", "<14>next"]);
    }

    #[test]
    fn digits_without_a_pri_are_text() {
        let mut f = Framer::new();
        let (frames, _) = f.push(b"2026 was a year\n42\n").unwrap();
        assert_eq!(texts(frames), vec!["2026 was a year", "42"]);
    }

    #[test]
    fn an_absurd_length_closes_the_stream() {
        let mut f = Framer::new();
        assert_eq!(f.push(b"999999 <13>x"), Err(FramingError));
    }

    #[test]
    fn an_unterminated_flood_is_discarded() {
        let mut f = Framer::new();
        let flood = vec![b'a'; MAX_MESSAGE_BYTES + 10];
        let (frames, oversized) = f.push(&flood).unwrap();
        assert!(frames.is_empty());
        assert_eq!(oversized, 1);
        let (frames, _) = f.push(b"<13>ok\n").unwrap();
        assert_eq!(texts(frames), vec!["<13>ok"]);
    }

    #[test]
    fn the_last_unterminated_message_is_kept_on_close() {
        let mut f = Framer::new();
        f.push(b"<13>first\n<13>last").unwrap();
        assert_eq!(f.finish(), Some(b"<13>last".to_vec()));
    }

    // ── wire format ─────────────────────────────────────────────────────────

    #[test]
    fn entries_serialise_as_the_backend_reads_them() {
        let v = serde_json::to_value(entry("<13>hi")).unwrap();
        assert_eq!(
            v,
            json!({"raw": "<13>hi", "source_ip": "10.0.0.5",
                   "received_at": "2026-10-11T10:00:00.000Z", "transport": "udp"})
        );
    }

    #[test]
    fn status_omits_a_missing_error() {
        let s = RelayStatus {
            listening: true,
            port: 514,
            received: 3,
            forwarded: 2,
            dropped: 1,
            buffered: 0,
            send_failures: 0,
            error: None,
        };
        let v = serde_json::to_value(&s).unwrap();
        assert!(v.get("error").is_none());
        assert_eq!(v["received"], 3);
    }

    #[test]
    fn mapped_ipv4_is_reported_as_ipv4() {
        let ip: IpAddr = "::ffff:10.0.0.7".parse().unwrap();
        assert_eq!(plain_ip(ip).to_string(), "10.0.0.7");
        let v6: IpAddr = "fd00::1".parse().unwrap();
        assert_eq!(plain_ip(v6).to_string(), "fd00::1");
    }

    #[test]
    fn accept_skips_empty_and_counts_oversized() {
        let shared = Shared::new();
        let ip: IpAddr = "10.0.0.9".parse().unwrap();
        shared.accept(b"\r\n", ip, "udp");
        shared.accept(&vec![b'x'; MAX_MESSAGE_BYTES + 1], ip, "udp");
        shared.accept(b"<13>ok\n", ip, "udp");
        assert_eq!(shared.received.load(Ordering::Relaxed), 1);
        assert_eq!(shared.dropped.load(Ordering::Relaxed), 1);
        let batch = shared.buffer().take_batch(10, usize::MAX);
        assert_eq!(raws(&batch), vec!["<13>ok"]);
        assert_eq!(batch[0].source_ip, "10.0.0.9");
        assert!(batch[0].received_at.ends_with('Z'));
    }

    // ── listeners end to end (loopback) ─────────────────────────────────────

    async fn wait_for(shared: &Shared, n: usize) -> Vec<RelayEntry> {
        let deadline = Instant::now() + Duration::from_secs(5);
        while shared.buffer().len() < n && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        shared.buffer().take_batch(100, usize::MAX)
    }

    #[tokio::test]
    async fn udp_and_tcp_messages_land_in_the_buffer_with_their_source() {
        use tokio::io::AsyncWriteExt;

        let shared = Arc::new(Shared::new());
        let (udp, tcp) = bind("127.0.0.1", 0).await.unwrap();
        let udp_addr = udp.local_addr().unwrap();
        let tcp_addr = tcp.local_addr().unwrap();
        let listeners = spawn_listeners(&shared, udp, tcp);

        let sender = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        sender.send_to(b"<14>Oct 11 10:00:00 sw1 link up", udp_addr).await.unwrap();
        let got = wait_for(&shared, 1).await;
        assert_eq!(raws(&got), vec!["<14>Oct 11 10:00:00 sw1 link up"]);
        assert_eq!((got[0].source_ip.as_str(), got[0].transport), ("127.0.0.1", "udp"));

        let mut stream = TcpStream::connect(tcp_addr).await.unwrap();
        stream.write_all(b"<13>tcp one\n9 <13>tcp 2<13>tcp three").await.unwrap();
        stream.shutdown().await.unwrap();
        drop(stream);
        let got = wait_for(&shared, 3).await;
        assert_eq!(raws(&got), vec!["<13>tcp one", "<13>tcp 2", "<13>tcp three"]);
        assert!(got.iter().all(|e| e.transport == "tcp" && e.source_ip == "127.0.0.1"));

        listeners.stop();
        assert_eq!(shared.received.load(Ordering::Relaxed), 4);
    }

    #[tokio::test]
    async fn a_port_in_use_is_reported_not_fatal() {
        let busy = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let port = busy.local_addr().unwrap().port();
        let api = Arc::new(ApiClient::with_token(&crate::config::Config::for_test(), "t"));
        let mut relay = SyslogRelay::new(api, "probe".to_string());
        relay.bind_addr = "127.0.0.1".to_string();
        assert_eq!(relay.status(), None, "never configured: nothing in the heartbeat");

        relay.apply(Some(RelayConfig { port })).await;
        let st = relay.status().unwrap();
        assert!(!st.listening);
        assert_eq!(st.port, port);
        assert!(st.error.unwrap().contains(&port.to_string()));
        assert!(relay.forwarder.is_none());

        // The same config again does not retry before BIND_RETRY.
        relay.apply(Some(RelayConfig { port })).await;
        assert!(relay.retry_at.is_some());

        relay.apply(None).await;
        let st = relay.status().unwrap();
        assert!(!st.listening);
        assert_eq!(st.error, None);
    }
}

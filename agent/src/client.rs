use reqwest::Client;
use serde::{Deserialize, Serialize};
use tracing::{debug, warn};

use crate::checks::{CheckAssignment, CheckResult};
use crate::collector::SystemMetrics;
use crate::config::{Config, ServerConfig};
use crate::services::ServiceStatus;
use crate::syslog_relay::{RelayEntry, RelayStatus};

/// Hard cap on update-binary download size to prevent OOM/DoS from a malicious
/// or compromised server returning an unbounded body. 200 MiB.
const MAX_UPDATE_BYTES: usize = 200 * 1024 * 1024;

pub struct ApiClient {
    client: Client,
    base_url: String,
    token: String,
}

/// Build the shared HTTP client. TLS certificate validation is enforced by
/// default; it is only disabled when `allow_insecure_tls` is explicitly set
/// (config field / NODEGLOW_ALLOW_INSECURE_TLS), which is intended for testing.
fn build_client(cfg: &Config) -> Client {
    let mut builder = Client::builder().timeout(std::time::Duration::from_secs(15));

    if cfg.allow_insecure_tls {
        warn!("TLS certificate validation is DISABLED (allow_insecure_tls=true) — insecure, testing only");
        builder = builder.danger_accept_invalid_certs(true);
    }

    builder.build().expect("Failed to create HTTP client")
}

/// Heartbeat request body: the metrics object exactly as before, with
/// `check_results` and `service_states` added next to it. Each field is
/// omitted entirely when there is nothing to deliver, so an agent that is
/// neither a probe nor watching services sends the same bytes it always did.
#[derive(Serialize)]
struct ReportPayload<'a> {
    #[serde(flatten)]
    metrics: &'a SystemMetrics,
    #[serde(skip_serializing_if = "is_empty")]
    check_results: &'a [CheckResult],
    #[serde(skip_serializing_if = "no_services")]
    service_states: &'a [ServiceStatus],
    /// Syslog relay counters; only once the relay was ever switched on.
    #[serde(skip_serializing_if = "Option::is_none")]
    syslog_relay: Option<&'a RelayStatus>,
}

fn is_empty(results: &&[CheckResult]) -> bool {
    results.is_empty()
}

fn no_services(states: &&[ServiceStatus]) -> bool {
    states.is_empty()
}

#[derive(Debug, Serialize)]
pub struct LogEntry {
    pub timestamp: String,
    pub severity: u8,
    pub app_name: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub facility: Option<u8>,
}

#[derive(Debug, Deserialize)]
pub struct ReportResponse {
    pub ok: bool,
    pub config: Option<ServerConfig>,
    pub command: Option<String>,
    /// Hosts this agent is responsible for checking until the next heartbeat.
    /// Absent or empty for every agent that is not a probe.
    #[serde(default)]
    pub checks: Option<Vec<CheckAssignment>>,
}

#[derive(Debug, Deserialize)]
pub struct EnrollResponse {
    pub ok: bool,
    pub token: String,
    pub agent_id: u64,
}

#[derive(Debug, Deserialize)]
pub struct VersionResponse {
    pub hash: String,
    /// Optional hex-encoded ed25519 detached signature over the binary bytes.
    /// Present only if the server has signing enabled (defense-in-depth on top
    /// of the authenticated TLS channel + SHA-256 hash check).
    #[serde(default)]
    pub signature: Option<String>,
}

impl ApiClient {
    pub fn new(cfg: &Config) -> Self {
        Self {
            client: build_client(cfg),
            base_url: cfg.server.trim_end_matches('/').to_string(),
            token: String::new(),
        }
    }

    pub fn with_token(cfg: &Config, token: &str) -> Self {
        Self {
            client: build_client(cfg),
            base_url: cfg.server.trim_end_matches('/').to_string(),
            token: token.to_string(),
        }
    }

    /// Enroll with the server using hostname and enrollment key.
    pub async fn enroll(&self, cfg: &Config) -> anyhow::Result<String> {
        let hostname = hostname::get()
            .map(|h| h.to_string_lossy().to_string())
            .unwrap_or_else(|_| "unknown".into());

        #[derive(Serialize)]
        struct EnrollPayload {
            enrollment_key: String,
            hostname: String,
            platform: String,
            arch: String,
        }

        let resp = self
            .client
            .post(format!("{}/api/agent/enroll", self.base_url))
            .json(&EnrollPayload {
                enrollment_key: cfg.enrollment_key.clone(),
                hostname,
                platform: std::env::consts::OS.into(),
                arch: std::env::consts::ARCH.into(),
            })
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Enrollment failed ({}): {}", status, body);
        }

        let data: EnrollResponse = resp.json().await?;
        if !data.ok {
            anyhow::bail!("Enrollment rejected by server");
        }

        Ok(data.token)
    }

    /// Send metrics + logs to the server, plus any probe check results that are
    /// waiting for delivery and the state of the watched services.
    pub async fn report(
        &self,
        metrics: &SystemMetrics,
        logs: &[LogEntry],
        check_results: &[CheckResult],
        service_states: &[ServiceStatus],
        syslog_relay: Option<&RelayStatus>,
    ) -> anyhow::Result<ReportResponse> {
        let resp = self
            .client
            .post(format!("{}/api/agent/report", self.base_url))
            .bearer_auth(&self.token)
            .json(&ReportPayload {
                metrics,
                check_results,
                service_states,
                syslog_relay,
            })
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Report failed ({}): {}", status, body);
        }

        let report_resp: ReportResponse = resp.json().await?;
        debug!("Report OK");

        // Send logs separately if any
        if !logs.is_empty() {
            self.send_logs(&metrics.hostname, logs).await;
        }

        Ok(report_resp)
    }

    /// Send collected logs to the server.
    async fn send_logs(&self, hostname: &str, logs: &[LogEntry]) {
        #[derive(Serialize)]
        struct LogPayload<'a> {
            hostname: &'a str,
            logs: &'a [LogEntry],
        }

        // Send in batches of 500
        for chunk in logs.chunks(500) {
            let result = self
                .client
                .post(format!("{}/api/agent/logs", self.base_url))
                .bearer_auth(&self.token)
                .json(&LogPayload {
                    hostname,
                    logs: chunk,
                })
                .send()
                .await;

            match result {
                Ok(resp) if resp.status().is_success() => {
                    debug!("Sent {} log entries", chunk.len());
                }
                Ok(resp) => {
                    warn!("Log submission failed: {}", resp.status());
                }
                Err(e) => {
                    warn!("Log submission error: {e}");
                }
            }
        }
    }

    /// Forward a batch of relayed syslog lines (probe syslog relay). Unlike
    /// the agent's own logs, a failure that may pass is returned as `Err`:
    /// the relay keeps the batch and retries it. What the backend took and
    /// what it refused (or refused outright) comes back as a `RelaySent`.
    pub async fn send_relay_logs(
        &self,
        hostname: &str,
        entries: &[RelayEntry],
    ) -> anyhow::Result<RelaySent> {
        #[derive(Serialize)]
        struct RelayPayload<'a> {
            hostname: &'a str,
            logs: &'a [RelayEntry],
        }

        let resp = self
            .client
            .post(format!("{}/api/agent/logs", self.base_url))
            .bearer_auth(&self.token)
            .json(&RelayPayload { hostname, logs: entries })
            .send()
            .await?;
        let status = resp.status();
        if status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Ok(RelaySent::from_response(entries.len(), &body));
        }
        if relay_status_is_permanent(status.as_u16()) {
            return Ok(RelaySent::Refused(status.as_u16()));
        }
        anyhow::bail!("relay submission failed: {}", status);
    }

    /// Check latest agent version info (hash + optional signature) from server.
    pub async fn get_version_info(&self, platform: &str) -> anyhow::Result<VersionResponse> {
        let resp = self
            .client
            .get(format!(
                "{}/api/agent/version/{}",
                self.base_url, platform
            ))
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Version check failed: {}", resp.status());
        }

        let data: VersionResponse = resp.json().await?;
        Ok(data)
    }

    /// Download agent binary from server, enforcing a maximum size to avoid
    /// an OOM/DoS from an unbounded response body.
    pub async fn download_agent(&self, platform: &str) -> anyhow::Result<Vec<u8>> {
        let url = format!("{}/agents/download/{}", self.base_url, platform);
        let mut resp = self
            .client
            .get(&url)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .await?;

        if !resp.status().is_success() {
            anyhow::bail!("Download failed: {}", resp.status());
        }

        // Reject early if the advertised length already exceeds the cap.
        if let Some(len) = resp.content_length() {
            if len > MAX_UPDATE_BYTES as u64 {
                anyhow::bail!(
                    "Update too large: Content-Length {len} exceeds limit {MAX_UPDATE_BYTES}"
                );
            }
        }

        // Stream the body with a running byte cap so a server that lies about
        // (or omits) Content-Length still cannot exhaust memory.
        let mut buf: Vec<u8> = Vec::new();
        while let Some(chunk) = resp.chunk().await? {
            if buf.len() + chunk.len() > MAX_UPDATE_BYTES {
                anyhow::bail!(
                    "Update exceeded maximum download size of {MAX_UPDATE_BYTES} bytes, aborting"
                );
            }
            buf.extend_from_slice(&chunk);
        }

        Ok(buf)
    }
}

/// What became of a relayed batch the backend answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RelaySent {
    /// The backend stored `accepted` entries and dropped `rejected` of them
    /// (not allowed, oversized, invalid, ...). Resending them cannot help.
    Done { accepted: u64, rejected: u64 },
    /// The backend refused the whole batch for good (a 4xx resending cannot
    /// fix, such as 400 or 413): it is discarded, not retried forever.
    Refused(u16),
}

impl RelaySent {
    /// Read `{"count": n, "dropped": m}`. The accepted count is what the
    /// backend stored; everything else of the batch counts as rejected
    /// (that also covers entries past the backend's per-request limit). A
    /// backend that does not say (an unreadable body) took the batch.
    fn from_response(sent: usize, body: &str) -> Self {
        let sent = sent as u64;
        let accepted = serde_json::from_str::<serde_json::Value>(body)
            .ok()
            .and_then(|v| v.get("count").and_then(|c| c.as_u64()))
            .map(|c| c.min(sent))
            .unwrap_or(sent);
        RelaySent::Done { accepted, rejected: sent - accepted }
    }
}

/// A 4xx resending cannot fix. Authentication (401/403: a token or probe
/// an admin may restore), 404 (a backend mid-upgrade), 408 and 429 pass;
/// 5xx and network errors are always retried.
fn relay_status_is_permanent(status: u16) -> bool {
    (400..500).contains(&status) && !matches!(status, 401 | 403 | 404 | 408 | 425 | 429)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collector::SystemMetrics;
    use std::collections::{BTreeMap, HashMap};

    #[test]
    fn relay_responses_count_only_what_the_backend_stored() {
        assert_eq!(
            RelaySent::from_response(5, r#"{"ok": true, "count": 3, "dropped": 2}"#),
            RelaySent::Done { accepted: 3, rejected: 2 }
        );
        // Entries the backend ignored without counting them are rejected too.
        assert_eq!(
            RelaySent::from_response(600, r#"{"ok": true, "count": 500, "dropped": 0}"#),
            RelaySent::Done { accepted: 500, rejected: 100 }
        );
        // A count above the batch is capped; a body that says nothing means taken.
        assert_eq!(
            RelaySent::from_response(2, r#"{"count": 9}"#),
            RelaySent::Done { accepted: 2, rejected: 0 }
        );
        assert_eq!(
            RelaySent::from_response(4, ""),
            RelaySent::Done { accepted: 4, rejected: 0 }
        );
    }

    #[test]
    fn only_unfixable_client_errors_discard_a_relay_batch() {
        for permanent in [400, 413, 422] {
            assert!(relay_status_is_permanent(permanent), "{permanent}");
        }
        for retry in [401, 403, 404, 408, 429, 500, 502, 503] {
            assert!(!relay_status_is_permanent(retry), "{retry}");
        }
    }

    fn sample_metrics() -> SystemMetrics {
        SystemMetrics {
            hostname: "probe01".into(),
            platform: "linux".into(),
            arch: "x86_64".into(),
            agent_version: "0.1.0".into(),
            cpu_pct: 1.0,
            mem_total_mb: 1024,
            mem_used_mb: 256,
            mem_pct: 25.0,
            swap_total_mb: 0,
            swap_used_mb: 0,
            swap_pct: 0.0,
            disk_pct: 10.0,
            disks: Vec::new(),
            load_1: 0.0,
            load_5: 0.0,
            load_15: 0.0,
            uptime_s: 60,
            rx_bytes: 0,
            tx_bytes: 0,
            network_interfaces: Vec::new(),
            cpu_temp: None,
            processes: Vec::new(),
            os_info: None,
            cpu_info: None,
            docker_containers: Vec::new(),
            extra: HashMap::new(),
        }
    }

    fn sample_result() -> CheckResult {
        let mut detail = BTreeMap::new();
        detail.insert("icmp".to_string(), true);
        CheckResult {
            host_id: 42,
            success: true,
            latency_ms: Some(12.4),
            detail,
            ts: "2026-08-28T09:01:13Z".to_string(),
        }
    }

    #[test]
    fn a_non_probe_payload_is_unchanged() {
        let metrics = sample_metrics();
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &[],
            service_states: &[],
            syslog_relay: None,
        };
        let with_probe_field = serde_json::to_value(&payload).unwrap();
        let plain = serde_json::to_value(&metrics).unwrap();
        assert_eq!(
            with_probe_field, plain,
            "an agent with no check results must send exactly what it always sent"
        );
    }

    #[test]
    fn check_results_ride_along_at_the_top_level() {
        let metrics = sample_metrics();
        let results = vec![sample_result()];
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &results,
            service_states: &[],
            syslog_relay: None,
        };
        let json = serde_json::to_value(&payload).unwrap();

        // The metrics stay where the backend already reads them.
        assert_eq!(json["hostname"], "probe01");
        assert_eq!(
            json["check_results"],
            serde_json::json!([{
                "host_id": 42,
                "success": true,
                "latency_ms": 12.4,
                "detail": {"icmp": true},
                "ts": "2026-08-28T09:01:13Z"
            }])
        );
    }

    #[test]
    fn service_states_ride_along_at_the_top_level() {
        use crate::services::{ServiceState, ServiceStatus};
        let metrics = sample_metrics();
        let states = vec![ServiceStatus {
            name: "nginx".into(),
            state: ServiceState::Stopped,
            start_type: Some("enabled".into()),
        }];
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &[],
            service_states: &states,
            syslog_relay: None,
        };
        let json = serde_json::to_value(&payload).unwrap();
        assert_eq!(json["hostname"], "probe01");
        assert!(json.get("check_results").is_none());
        assert_eq!(
            json["service_states"],
            serde_json::json!([{"name": "nginx", "state": "stopped", "start_type": "enabled"}])
        );
    }

    #[test]
    fn the_watched_service_list_arrives_in_the_config() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": {"log_levels": "1", "log_channels": "System",
                "log_file_paths": "", "agent_log_level": "errors",
                "watched_services": ["Spooler", "W32Time"]}}"#,
        )
        .unwrap();
        let sc = resp.config.unwrap();
        assert_eq!(
            sc.watched_services.unwrap_or_default(),
            vec!["Spooler".to_string(), "W32Time".to_string()]
        );
    }

    #[test]
    fn an_old_server_or_a_null_list_means_nothing_to_watch() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": {"log_levels": "1", "log_channels": "System",
                "log_file_paths": "", "agent_log_level": "errors"}}"#,
        )
        .unwrap();
        assert!(resp.config.unwrap().watched_services.unwrap_or_default().is_empty());
        let resp: ReportResponse =
            serde_json::from_str(r#"{"ok": true, "config": {"watched_services": null}}"#).unwrap();
        assert!(resp.config.unwrap().watched_services.unwrap_or_default().is_empty());
    }

    #[test]
    fn relay_counters_ride_along_once_the_relay_was_on() {
        let metrics = sample_metrics();
        let status = RelayStatus {
            listening: true,
            port: 514,
            received: 7,
            forwarded: 5,
            rejected: 1,
            dropped: 0,
            buffered: 2,
            send_failures: 1,
            error: None,
        };
        let payload = ReportPayload {
            metrics: &metrics,
            check_results: &[],
            service_states: &[],
            syslog_relay: Some(&status),
        };
        let json = serde_json::to_value(&payload).unwrap();
        assert_eq!(json["hostname"], "probe01");
        assert_eq!(
            json["syslog_relay"],
            serde_json::json!({"listening": true, "port": 514, "received": 7, "forwarded": 5,
                               "rejected": 1, "dropped": 0, "buffered": 2, "send_failures": 1})
        );
    }

    #[test]
    fn the_relay_config_arrives_and_a_bad_one_does_not_break_the_heartbeat() {
        use crate::syslog_relay::RelayConfig;
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": {"syslog_relay": {"enabled": true, "port": 1514}}}"#,
        )
        .unwrap();
        let sc = resp.config.unwrap();
        assert_eq!(
            RelayConfig::from_server(sc.syslog_relay.as_ref()),
            Some(RelayConfig { port: 1514 })
        );
        for bad in [
            r#"{"ok": true, "config": {"syslog_relay": {"enabled": true, "port": 99999}}}"#,
            r#"{"ok": true, "config": {"syslog_relay": "on"}}"#,
            r#"{"ok": true, "config": {"syslog_relay": null}}"#,
            r#"{"ok": true, "config": {"log_levels": "1"}}"#,
        ] {
            let resp: ReportResponse = serde_json::from_str(bad).unwrap();
            let sc = resp.config.unwrap();
            let cfg = RelayConfig::from_server(sc.syslog_relay.as_ref());
            assert!(cfg.is_none() || cfg == Some(RelayConfig { port: 514 }), "{bad}");
        }
    }

    #[test]
    fn a_response_without_checks_yields_no_assignments() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": {"log_levels": "1", "log_channels": "System",
                "log_file_paths": "", "agent_log_level": "errors"}}"#,
        )
        .unwrap();
        assert!(resp.checks.is_none());
        assert!(resp.checks.unwrap_or_default().is_empty());
    }

    #[test]
    fn a_response_with_checks_carries_the_assignments() {
        let resp: ReportResponse = serde_json::from_str(
            r#"{"ok": true, "config": null, "command": null, "checks": [
                {"host_id": 42, "hostname": "nas.kunde.ch", "ip": "10.0.0.5",
                 "check_type": "icmp", "port": null, "timeout_seconds": 5.0}]}"#,
        )
        .unwrap();
        let checks = resp.checks.unwrap();
        assert_eq!(checks.len(), 1);
        assert_eq!(checks[0].host_id, 42);
        assert_eq!(checks[0].check_type, "icmp");
    }
}

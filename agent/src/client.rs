use reqwest::Client;
use serde::{Deserialize, Serialize};
use tracing::{debug, warn};

use crate::collector::SystemMetrics;
use crate::config::{Config, ServerConfig};

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

    /// Send metrics + logs to the server.
    pub async fn report(
        &self,
        metrics: &SystemMetrics,
        logs: &[LogEntry],
    ) -> anyhow::Result<ReportResponse> {
        let resp = self
            .client
            .post(format!("{}/api/agent/report", self.base_url))
            .bearer_auth(&self.token)
            .json(metrics)
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

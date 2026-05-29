use crate::client::LogEntry;
use tokio::process::Command;
use tracing::{debug, warn};
use std::sync::Mutex;

/// Track last-read timestamps per channel.
static LAST_TIMESTAMPS: Mutex<Option<std::collections::HashMap<String, String>>> = Mutex::new(None);

/// Collect Windows Event Log entries via PowerShell.
pub async fn collect_event_logs(channels: &str, levels: &str) -> Vec<LogEntry> {
    let channels: Vec<&str> = if channels.is_empty() {
        vec!["System", "Application"]
    } else {
        channels.split(',').map(str::trim).filter(|s| !s.is_empty()).collect()
    };

    let level_filter: Vec<u8> = if levels.is_empty() {
        vec![1, 2, 3] // Error, Warning, Critical
    } else {
        levels.split(',').filter_map(|s| s.trim().parse().ok()).collect()
    };

    let mut all_entries = Vec::new();
    let mut timestamps = LAST_TIMESTAMPS
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
        .unwrap_or_default();

    for channel in &channels {
        let last_ts = timestamps.get(*channel).cloned();
        let entries = collect_channel(channel, &level_filter, last_ts.as_deref()).await;

        if let Some(newest) = entries.first() {
            timestamps.insert(channel.to_string(), newest.timestamp.clone());
        }

        all_entries.extend(entries);
    }

    *LAST_TIMESTAMPS.lock().unwrap_or_else(|e| e.into_inner()) = Some(timestamps);

    debug!("Collected {} event log entries", all_entries.len());
    all_entries
}

/// Validate a Windows event-log channel name. The channel list is influenced by
/// server-pushed config, so it must never be interpolated into a PowerShell
/// script unchecked. Allow only the characters used by real channel names:
/// alphanumerics and `/ - _` and spaces (e.g. "Microsoft-Windows-Sysmon/Operational").
fn is_valid_channel(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 256
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '-' | '_' | ' '))
}

/// Validate that the `after` cursor is a parseable RFC3339/ISO-8601 timestamp.
/// The cursor originates from a prior event's TimeCreated.ToString('o') output,
/// but we re-validate before use so it can never inject script.
fn is_valid_timestamp(ts: &str) -> bool {
    chrono::DateTime::parse_from_rfc3339(ts).is_ok()
}

async fn collect_channel(channel: &str, levels: &[u8], after: Option<&str>) -> Vec<LogEntry> {
    // Reject channel names that are not strictly whitelisted. This makes
    // PowerShell script injection via a crafted channel name impossible.
    if !is_valid_channel(channel) {
        warn!("Skipping invalid event-log channel name");
        return Vec::new();
    }

    // Level CSV is built from numeric u8 values, so it is injection-safe.
    let level_csv = levels.iter().map(|l| l.to_string()).collect::<Vec<_>>().join(",");

    // Validate the `after` cursor parses as a timestamp before using it. Passing
    // the value through the NODEGLOW_LOG_AFTER environment variable (read inside
    // the script) keeps it out of the script body entirely.
    let after_validated: Option<&str> = match after {
        Some(ts) if is_valid_timestamp(ts) => Some(ts),
        Some(_) => {
            warn!("Ignoring invalid 'after' timestamp for event-log query");
            None
        }
        None => None,
    };

    let time_filter = if after_validated.is_some() {
        " -and $_.TimeCreated -gt [DateTime]::Parse($env:NODEGLOW_LOG_AFTER)".to_string()
    } else {
        // First run: last 5 minutes
        " -and $_.TimeCreated -gt (Get-Date).AddMinutes(-5)".to_string()
    };

    // The channel name is passed via the NODEGLOW_LOG_CHANNEL environment
    // variable rather than interpolated into the script body. Combined with the
    // whitelist validation above, untrusted data never lands in code position.
    let ps_script = format!(
        r#"
        try {{
            Get-WinEvent -FilterHashtable @{{LogName=$env:NODEGLOW_LOG_CHANNEL; Level={level_csv}}} -MaxEvents 100 -ErrorAction SilentlyContinue |
            Where-Object {{ $_ -ne $null{time_filter} }} |
            Select-Object TimeCreated, Level, ProviderName, Message |
            ForEach-Object {{
                $ts = $_.TimeCreated.ToUniversalTime().ToString('o')
                $msg = if ($_.Message) {{ $_.Message.Replace("`r`n"," ").Replace("`n"," ").Substring(0, [Math]::Min($_.Message.Length, 500)) }} else {{ "" }}
                "$ts|$($_.Level)|$($_.ProviderName)|$msg"
            }}
        }} catch {{}}
        "#
    );

    let mut cmd = Command::new("powershell");
    cmd.args(["-NoProfile", "-NoLogo", "-Command", &ps_script])
        .env("NODEGLOW_LOG_CHANNEL", channel);
    if let Some(ts) = after_validated {
        cmd.env("NODEGLOW_LOG_AFTER", ts);
    }
    let output = cmd.output().await;

    let Ok(output) = output else {
        warn!("PowerShell event log query failed for {channel}");
        return Vec::new();
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut entries: Vec<LogEntry> = stdout
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(4, '|').collect();
            if parts.len() < 4 {
                return None;
            }

            let timestamp = parts[0].to_string();
            let level: u8 = parts[1].parse().unwrap_or(4);
            let app_name = parts[2].to_string();
            let message = parts[3].to_string();

            if message.is_empty() {
                return None;
            }

            // Map Windows Event Level to syslog severity
            // Windows: 1=Critical, 2=Error, 3=Warning, 4=Info, 5=Verbose
            // Syslog:  2=Critical, 3=Error, 4=Warning, 6=Info, 7=Debug
            let severity = match level {
                1 => 2, // Critical
                2 => 3, // Error
                3 => 4, // Warning
                4 => 6, // Info
                5 => 7, // Debug
                _ => 6,
            };

            Some(LogEntry {
                timestamp,
                severity,
                app_name,
                message,
                facility: None,
            })
        })
        .collect();

    // Sort newest first
    entries.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));

    entries
}

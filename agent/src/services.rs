//! Watched services — report the state of the services the backend asked for.
//!
//! The list arrives on the heartbeat response (`config.watched_services`), is
//! checked once per heartbeat, and the result rides along on the next report
//! (`service_states`). An agent with nothing to watch sends no field at all, so
//! its payload stays byte-for-byte what it was before this feature existed.
//!
//! Wire format of one reported entry:
//!
//! ```json
//! {"name": "Spooler", "state": "running", "start_type": "auto"}
//! ```
//!
//! `state` is one of `running`, `stopped`, `not_found`, `unknown`. `unknown`
//! means the agent could not tell (the query tool is missing, timed out or
//! printed something unexpected) — it is never a guess in either direction.
//!
//! Windows services are queried with `sc query` / `sc qc`, Linux systemd units
//! with `systemctl show`. Both run without a shell; the name is passed as a
//! single argument and validated beforehand, because it comes from the server.

use serde::Serialize;
#[cfg(any(target_os = "windows", target_os = "linux"))]
use std::process::Stdio;
#[cfg(any(target_os = "windows", target_os = "linux"))]
use std::time::Duration;
#[cfg(any(target_os = "windows", target_os = "linux"))]
use tokio::process::Command;
#[cfg(any(target_os = "windows", target_os = "linux"))]
use tracing::debug;

/// Upper bound on services checked per heartbeat. Mirrors the backend limit;
/// anything beyond it is ignored rather than letting a bad list fork hundreds
/// of processes every interval.
pub const MAX_WATCHED_SERVICES: usize = 50;

/// Longest accepted service / unit name.
const MAX_NAME_LEN: usize = 256;

/// Wall-clock cap for a single query process.
#[cfg(any(target_os = "windows", target_os = "linux"))]
const QUERY_TIMEOUT: Duration = Duration::from_secs(5);

/// How long a Windows start type is cached. It changes rarely, and querying it
/// costs a second `sc` process per service.
#[cfg(target_os = "windows")]
const START_TYPE_TTL: Duration = Duration::from_secs(600);

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceState {
    Running,
    Stopped,
    NotFound,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ServiceStatus {
    pub name: String,
    pub state: ServiceState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub start_type: Option<String>,
}

/// Whether `name` is safe to hand to `sc` / `systemctl` as an argument.
///
/// Windows service names and systemd unit names use letters, digits and a few
/// punctuation characters (`MSSQL$SQLEXPRESS`, `getty@tty1.service`,
/// `systemd-fsck@dev-disk-by\x2duuid.service`). A leading `-` is rejected so a
/// name can never be read as an option.
pub fn is_valid_service_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= MAX_NAME_LEN
        && !name.starts_with('-')
        && name.chars().all(|c| {
            c.is_ascii_alphanumeric()
                || matches!(c, '-' | '_' | '.' | '@' | ':' | '$' | ' ' | '\\')
        })
}

/// Normalise the server-provided list: trim, drop empties, de-duplicate
/// (keeping the first occurrence) and cap the length.
pub fn normalize_watch_list(names: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for raw in names {
        let name = raw.trim();
        if name.is_empty() || out.iter().any(|n| n == name) {
            continue;
        }
        out.push(name.to_string());
        if out.len() >= MAX_WATCHED_SERVICES {
            break;
        }
    }
    out
}

/// Check every watched service once. Returns one entry per (normalised) name,
/// in list order. An empty list does no work at all.
pub async fn check_services(names: &[String]) -> Vec<ServiceStatus> {
    let names = normalize_watch_list(names);
    let mut out = Vec::with_capacity(names.len());
    for name in names {
        let status = if is_valid_service_name(&name) {
            check_one(&name).await
        } else {
            ServiceStatus {
                name: name.clone(),
                state: ServiceState::Unknown,
                start_type: None,
            }
        };
        out.push(status);
    }
    out
}

// ── Platform runners ─────────────────────────────────────────────────────────

#[cfg(any(target_os = "windows", target_os = "linux"))]
async fn run_query(program: &str, args: &[&str]) -> Option<(Option<i32>, String)> {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let output = match tokio::time::timeout(QUERY_TIMEOUT, cmd.output()).await {
        Ok(Ok(out)) => out,
        Ok(Err(e)) => {
            debug!("could not run {program}: {e}");
            return None;
        }
        Err(_) => {
            debug!("{program} timed out after {QUERY_TIMEOUT:?}");
            return None;
        }
    };
    // `sc` prints its error text on stdout; keep stderr too so a
    // "service does not exist" message is seen wherever it lands.
    let mut text = String::from_utf8_lossy(&output.stdout).into_owned();
    if !output.stderr.is_empty() {
        text.push('\n');
        text.push_str(&String::from_utf8_lossy(&output.stderr));
    }
    Some((output.status.code(), text))
}

#[cfg(target_os = "windows")]
async fn check_one(name: &str) -> ServiceStatus {
    let state = match run_query("sc", &["query", name]).await {
        Some((code, text)) => parse_sc_query(&text, code),
        None => ServiceState::Unknown,
    };
    let start_type = if state == ServiceState::NotFound {
        None
    } else {
        windows_start_type(name).await
    };
    ServiceStatus {
        name: name.to_string(),
        state,
        start_type,
    }
}

#[cfg(target_os = "windows")]
async fn windows_start_type(name: &str) -> Option<String> {
    use std::collections::HashMap;
    use std::sync::Mutex;
    use std::time::Instant;

    static CACHE: Mutex<Option<HashMap<String, (Instant, Option<String>)>>> = Mutex::new(None);

    let key = name.to_ascii_lowercase();
    {
        let guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((at, value)) = guard.as_ref().and_then(|m| m.get(&key)) {
            if at.elapsed() < START_TYPE_TTL {
                return value.clone();
            }
        }
    }

    let value = match run_query("sc", &["qc", name]).await {
        Some((_, text)) => parse_sc_start_type(&text),
        None => None,
    };
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let map = guard.get_or_insert_with(HashMap::new);
    // Bounded by the watch list; drop entries for names no longer asked about
    // only when the map grows well past it.
    if map.len() > MAX_WATCHED_SERVICES * 4 {
        map.clear();
    }
    map.insert(key, (Instant::now(), value.clone()));
    value
}

#[cfg(target_os = "linux")]
async fn check_one(name: &str) -> ServiceStatus {
    let (state, start_type) = match run_query(
        "systemctl",
        &[
            "show",
            "--no-pager",
            "--property=LoadState,ActiveState,UnitFileState",
            "--",
            name,
        ],
    )
    .await
    {
        Some((_, text)) => parse_systemctl_show(&text),
        None => (ServiceState::Unknown, None),
    };
    ServiceStatus {
        name: name.to_string(),
        state,
        start_type,
    }
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
async fn check_one(name: &str) -> ServiceStatus {
    ServiceStatus {
        name: name.to_string(),
        state: ServiceState::Unknown,
        start_type: None,
    }
}

// ── Parsers (platform independent, so both CI targets test them) ─────────────

/// Windows error code for "The specified service does not exist as an
/// installed service." Printed by `sc` and used as its exit code.
const ERROR_SERVICE_DOES_NOT_EXIST: i32 = 1060;

/// Map the numeric `SERVICE_STATUS.dwCurrentState` that `sc query` prints.
///
/// The number is what we rely on: the word next to it is English on most
/// systems, but the number is the same everywhere.
fn sc_state_code(code: u32) -> ServiceState {
    match code {
        4 => ServiceState::Running,                 // RUNNING
        1 | 3 | 6 | 7 => ServiceState::Stopped,     // STOPPED, STOP_PENDING, PAUSE_PENDING, PAUSED
        // START_PENDING / CONTINUE_PENDING: on its way up, not yet a verdict.
        _ => ServiceState::Unknown,
    }
}

/// Parse `sc query <name>` output.
///
/// ```text
/// SERVICE_NAME: Spooler
///         TYPE               : 110  WIN32_OWN_PROCESS  (interactive)
///         STATE              : 4  RUNNING
/// ```
///
/// A missing service yields `[SC] EnumQueryServicesStatus:OpenService FAILED
/// 1060:` and exit code 1060, in any language.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn parse_sc_query(text: &str, exit_code: Option<i32>) -> ServiceState {
    if exit_code == Some(ERROR_SERVICE_DOES_NOT_EXIST) || mentions_error_1060(text) {
        return ServiceState::NotFound;
    }

    // Preferred: the line keyed STATE.
    for line in text.lines() {
        if let Some((key, value)) = line.split_once(':') {
            if key.trim().eq_ignore_ascii_case("STATE") {
                if let Some(code) = leading_number(value) {
                    return sc_state_code(code);
                }
            }
        }
    }

    // Fallback for a localised key: a "<n>  <STATE_WORD>" value, where the word
    // is one of the service-state constants (those are never translated).
    const WORDS: [&str; 7] = [
        "STOPPED",
        "START_PENDING",
        "STOP_PENDING",
        "RUNNING",
        "CONTINUE_PENDING",
        "PAUSE_PENDING",
        "PAUSED",
    ];
    for line in text.lines() {
        if let Some((_, value)) = line.split_once(':') {
            let mut parts = value.split_whitespace();
            if let (Some(num), Some(word)) = (parts.next(), parts.next()) {
                if WORDS.contains(&word) {
                    if let Ok(code) = num.parse::<u32>() {
                        return sc_state_code(code);
                    }
                }
            }
        }
    }

    ServiceState::Unknown
}

/// Parse the start type out of `sc qc <name>` output:
/// `START_TYPE         : 2   AUTO_START  (DELAYED)`.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn parse_sc_start_type(text: &str) -> Option<String> {
    if mentions_error_1060(text) {
        return None;
    }
    for line in text.lines() {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        if !key.trim().eq_ignore_ascii_case("START_TYPE") {
            continue;
        }
        let delayed = value.to_ascii_uppercase().contains("DELAYED");
        let label = match leading_number(value)? {
            0 => "boot",
            1 => "system",
            2 if delayed => "auto_delayed",
            2 => "auto",
            3 => "manual",
            4 => "disabled",
            _ => return None,
        };
        return Some(label.to_string());
    }
    None
}

fn mentions_error_1060(text: &str) -> bool {
    text.lines().any(|l| {
        let l = l.trim();
        l.starts_with("[SC]") && l.split(|c: char| !c.is_ascii_digit()).any(|t| t == "1060")
    })
}

fn leading_number(value: &str) -> Option<u32> {
    value.split_whitespace().next()?.parse().ok()
}

/// Map a systemd `ActiveState` — the same word `systemctl is-active` prints.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub fn systemd_active_state(word: &str) -> ServiceState {
    match word.trim() {
        "active" | "reloading" | "refreshing" => ServiceState::Running,
        "inactive" | "failed" | "deactivating" | "maintenance" => ServiceState::Stopped,
        // "activating" is on its way up; anything else we do not recognise.
        _ => ServiceState::Unknown,
    }
}

/// Parse `systemctl show --property=LoadState,ActiveState,UnitFileState`.
///
/// ```text
/// LoadState=loaded
/// ActiveState=active
/// UnitFileState=enabled
/// ```
///
/// `systemctl is-active` alone cannot tell a stopped unit from a missing one
/// (both print `inactive`), which is why `LoadState` is read as well.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub fn parse_systemctl_show(text: &str) -> (ServiceState, Option<String>) {
    let mut load = None;
    let mut active = None;
    let mut unit_file = None;
    for line in text.lines() {
        if let Some((k, v)) = line.trim().split_once('=') {
            match k {
                "LoadState" => load = Some(v.trim()),
                "ActiveState" => active = Some(v.trim()),
                "UnitFileState" => unit_file = Some(v.trim()),
                _ => {}
            }
        }
    }

    let start_type = unit_file
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());

    let state = match (load, active) {
        (Some("not-found"), _) => ServiceState::NotFound,
        (_, Some(a)) => systemd_active_state(a),
        _ => ServiceState::Unknown,
    };
    let start_type = if state == ServiceState::NotFound {
        None
    } else {
        start_type
    };
    (state, start_type)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SC_RUNNING: &str = "\r\nSERVICE_NAME: Spooler \r\n        TYPE               : 110  WIN32_OWN_PROCESS  (interactive)\r\n        STATE              : 4  RUNNING \r\n                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)\r\n        WIN32_EXIT_CODE    : 0  (0x0)\r\n        SERVICE_EXIT_CODE  : 0  (0x0)\r\n        CHECKPOINT         : 0x0\r\n        WAIT_HINT          : 0x0\r\n";

    const SC_STOPPED: &str = "\nSERVICE_NAME: wuauserv\n        TYPE               : 20  WIN32_SHARE_PROCESS\n        STATE              : 1  STOPPED\n        WIN32_EXIT_CODE    : 1077  (0x435)\n        SERVICE_EXIT_CODE  : 0  (0x0)\n        CHECKPOINT         : 0x0\n        WAIT_HINT          : 0x0\n";

    const SC_MISSING_EN: &str = "[SC] EnumQueryServicesStatus:OpenService FAILED 1060:\n\nThe specified service does not exist as an installed service.\n\n";

    const SC_MISSING_DE: &str = "[SC] EnumQueryServicesStatus:OpenService FEHLER 1060:\n\nDer angegebene Dienst ist kein installierter Dienst.\n\n";

    #[test]
    fn sc_query_running() {
        assert_eq!(parse_sc_query(SC_RUNNING, Some(0)), ServiceState::Running);
    }

    #[test]
    fn sc_query_stopped() {
        assert_eq!(parse_sc_query(SC_STOPPED, Some(0)), ServiceState::Stopped);
    }

    #[test]
    fn sc_query_missing_by_exit_code_or_text_in_any_language() {
        assert_eq!(parse_sc_query(SC_MISSING_EN, Some(1060)), ServiceState::NotFound);
        assert_eq!(parse_sc_query(SC_MISSING_EN, None), ServiceState::NotFound);
        assert_eq!(parse_sc_query(SC_MISSING_DE, Some(1)), ServiceState::NotFound);
        assert_eq!(parse_sc_query("", Some(1060)), ServiceState::NotFound);
    }

    #[test]
    fn sc_query_uses_the_numeric_code_not_the_word() {
        // A (hypothetical) localised word next to the number must not matter.
        let text = "SERVICE_NAME: Spooler\n        STATE              : 4  WIRD_AUSGEFUEHRT\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Running);
        let text = "SERVICE_NAME: Spooler\n        STATE              : 1  ANGEHALTEN\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Stopped);
    }

    #[test]
    fn sc_query_falls_back_when_the_key_is_localised() {
        let text = "DIENSTNAME: Spooler\n        ZUSTAND            : 4  RUNNING\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Running);
        let text = "DIENSTNAME: Spooler\n        ZUSTAND            : 1  STOPPED\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Stopped);
    }

    #[test]
    fn sc_query_pending_and_garbage() {
        let text = "SERVICE_NAME: x\n        STATE              : 2  START_PENDING\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Unknown);
        let text = "SERVICE_NAME: x\n        STATE              : 3  STOP_PENDING\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Stopped);
        let text = "SERVICE_NAME: x\n        STATE              : 7  PAUSED\n";
        assert_eq!(parse_sc_query(text, Some(0)), ServiceState::Stopped);
        assert_eq!(parse_sc_query("", Some(0)), ServiceState::Unknown);
        assert_eq!(parse_sc_query("Access is denied.", Some(5)), ServiceState::Unknown);
    }

    #[test]
    fn sc_qc_start_types() {
        let text = "[SC] QueryServiceConfig SUCCESS\n\nSERVICE_NAME: Spooler\n        TYPE               : 110  WIN32_OWN_PROCESS  (interactive)\n        START_TYPE         : 2   AUTO_START\n        ERROR_CONTROL      : 1   NORMAL\n        BINARY_PATH_NAME   : C:\\Windows\\System32\\spoolsv.exe\n";
        assert_eq!(parse_sc_start_type(text).as_deref(), Some("auto"));
        let text = "        START_TYPE         : 2   AUTO_START  (DELAYED)\n";
        assert_eq!(parse_sc_start_type(text).as_deref(), Some("auto_delayed"));
        let text = "        START_TYPE         : 3   DEMAND_START\n";
        assert_eq!(parse_sc_start_type(text).as_deref(), Some("manual"));
        let text = "        START_TYPE         : 4   DISABLED\n";
        assert_eq!(parse_sc_start_type(text).as_deref(), Some("disabled"));
        assert_eq!(parse_sc_start_type(SC_MISSING_EN), None);
        assert_eq!(parse_sc_start_type(""), None);
    }

    #[test]
    fn systemctl_is_active_words() {
        // The outputs `systemctl is-active <unit>` prints.
        assert_eq!(systemd_active_state("active\n"), ServiceState::Running);
        assert_eq!(systemd_active_state("reloading"), ServiceState::Running);
        assert_eq!(systemd_active_state("inactive\n"), ServiceState::Stopped);
        assert_eq!(systemd_active_state("failed"), ServiceState::Stopped);
        assert_eq!(systemd_active_state("deactivating"), ServiceState::Stopped);
        assert_eq!(systemd_active_state("activating"), ServiceState::Unknown);
        assert_eq!(systemd_active_state(""), ServiceState::Unknown);
    }

    #[test]
    fn systemctl_show_running_unit() {
        let (state, start) =
            parse_systemctl_show("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n");
        assert_eq!(state, ServiceState::Running);
        assert_eq!(start.as_deref(), Some("enabled"));
    }

    #[test]
    fn systemctl_show_failed_unit() {
        let (state, start) =
            parse_systemctl_show("LoadState=loaded\nActiveState=failed\nUnitFileState=disabled\n");
        assert_eq!(state, ServiceState::Stopped);
        assert_eq!(start.as_deref(), Some("disabled"));
    }

    #[test]
    fn systemctl_show_missing_unit() {
        // is-active would print "inactive" here; LoadState tells the truth.
        let (state, start) =
            parse_systemctl_show("LoadState=not-found\nActiveState=inactive\nUnitFileState=\n");
        assert_eq!(state, ServiceState::NotFound);
        assert_eq!(start, None);
    }

    #[test]
    fn systemctl_show_without_systemd() {
        let (state, start) = parse_systemctl_show(
            "System has not been booted with systemd as init system (PID 1). Can't operate.\n",
        );
        assert_eq!(state, ServiceState::Unknown);
        assert_eq!(start, None);
    }

    #[test]
    fn service_name_validation() {
        assert!(is_valid_service_name("Spooler"));
        assert!(is_valid_service_name("MSSQL$SQLEXPRESS"));
        assert!(is_valid_service_name("getty@tty1.service"));
        assert!(is_valid_service_name("nginx"));
        assert!(!is_valid_service_name(""));
        assert!(!is_valid_service_name("--help"));
        assert!(!is_valid_service_name("a;rm -rf /"));
        assert!(!is_valid_service_name("a|b"));
        assert!(!is_valid_service_name(&"x".repeat(MAX_NAME_LEN + 1)));
    }

    #[test]
    fn watch_list_is_trimmed_deduplicated_and_capped() {
        let list: Vec<String> = vec![" nginx ".into(), "".into(), "nginx".into(), "sshd".into()];
        assert_eq!(normalize_watch_list(&list), vec!["nginx".to_string(), "sshd".to_string()]);
        let many: Vec<String> = (0..200).map(|i| format!("svc{i}")).collect();
        assert_eq!(normalize_watch_list(&many).len(), MAX_WATCHED_SERVICES);
    }

    #[tokio::test]
    async fn nothing_watched_means_no_work() {
        assert!(check_services(&[]).await.is_empty());
    }

    #[tokio::test]
    async fn an_invalid_name_is_reported_unknown_without_running_anything() {
        let out = check_services(&["bad;name".to_string()]).await;
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].name, "bad;name");
        assert_eq!(out[0].state, ServiceState::Unknown);
    }

    #[test]
    fn serialises_to_the_documented_shape() {
        let s = ServiceStatus {
            name: "Spooler".into(),
            state: ServiceState::NotFound,
            start_type: None,
        };
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            serde_json::json!({"name": "Spooler", "state": "not_found"})
        );
        let s = ServiceStatus {
            name: "nginx".into(),
            state: ServiceState::Running,
            start_type: Some("enabled".into()),
        };
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            serde_json::json!({"name": "nginx", "state": "running", "start_type": "enabled"})
        );
    }
}

//! The `WorkspaceRunManager` singleton, its daemon configuration cell, and the
//! caller-keyed lifecycle backstops (cleanup, sweep, startup recovery) that
//! transport timers and the cancel coordinator drive.

use std::sync::{OnceLock, RwLock};
#[cfg(target_os = "linux")]
use std::time::Instant;

#[cfg(target_os = "linux")]
use eos_command_session::CommandSessionConfig as RuntimeCommandSessionConfig;
#[cfg(target_os = "linux")]
use eos_command_session::{CommandResponse, CommandSessionCompletion};
#[cfg(target_os = "linux")]
use eos_workspace_runtime::run::WorkspaceRunManager;
#[cfg(target_os = "linux")]
use serde_json::Value;

use crate::config::CommandSessionConfig;

#[cfg(target_os = "linux")]
pub(super) fn workspace_run_manager() -> &'static WorkspaceRunManager {
    static MANAGER: OnceLock<WorkspaceRunManager> = OnceLock::new();
    MANAGER.get_or_init(|| {
        WorkspaceRunManager::new(
            runtime_command_session_config(),
            std::sync::Arc::new(super::host_ports::DaemonRunHostPorts),
        )
    })
}

pub(crate) fn configure_command_sessions(config: &CommandSessionConfig) {
    let mut guard = command_session_config_cell()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *guard = config.clone();
}

#[cfg(target_os = "linux")]
pub(super) fn command_session_config() -> CommandSessionConfig {
    command_session_config_cell()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

#[cfg(target_os = "linux")]
fn runtime_command_session_config() -> RuntimeCommandSessionConfig {
    command_session_config()
}

#[cfg(target_os = "linux")]
pub(super) fn command_session_scratch_root() -> std::path::PathBuf {
    command_session_config().scratch_root
}

fn command_session_config_cell() -> &'static RwLock<CommandSessionConfig> {
    static CONFIG: OnceLock<RwLock<CommandSessionConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| RwLock::new(default_command_session_config()))
}

fn default_command_session_config() -> CommandSessionConfig {
    CommandSessionConfig {
        scratch_root: std::path::PathBuf::from("/eos/scratch/command-sessions"),
        default_yield_time_ms: 1000,
        default_timeout_s: 600,
        quiet_ms: 50,
        cancel_wait_ms: 500,
        output_drain_grace_ms: 500,
        max_session_s: 6 * 60 * 60,
        transcript_timestamp_timezone: "UTC".to_owned(),
    }
}

#[cfg(target_os = "linux")]
#[must_use]
pub(crate) fn active_command_sessions_for_caller(caller_id: &str) -> usize {
    let caller_id = caller_id.trim();
    if caller_id.is_empty() {
        return 0;
    }
    workspace_run_manager().count_by_caller(Some(caller_id))
}

#[cfg(not(target_os = "linux"))]
pub(crate) const fn active_command_sessions_for_caller(_caller_id: &str) -> usize {
    0
}

#[cfg(target_os = "linux")]
/// Best-effort lifecycle backstop for callers that bypass the model-facing
/// `RequireNoBackgroundSessions` hook.
pub(crate) fn cleanup_command_sessions_for_caller(caller_id: &str, grace_s: Option<f64>) -> usize {
    workspace_run_manager().cleanup_caller(caller_id, grace_s)
}

#[cfg(not(target_os = "linux"))]
pub(crate) const fn cleanup_command_sessions_for_caller(
    _caller_id: &str,
    _grace_s: Option<f64>,
) -> usize {
    0
}

/// Cancel and discard every live command session across all callers (the
/// whole-sandbox cancel sweep). Returns the number cancelled.
#[cfg(target_os = "linux")]
pub(crate) fn cancel_all_command_sessions(grace_s: Option<f64>) -> usize {
    workspace_run_manager().cancel_all(grace_s)
}

#[cfg(not(target_os = "linux"))]
pub(crate) const fn cancel_all_command_sessions(_grace_s: Option<f64>) -> usize {
    0
}

/// Periodic reaper (sense-2 §2.4, §3): enforce the per-session timeout backstop
/// and finalize any session whose child has exited without a live poller,
/// parking the completion for the heartbeat. The runner enforces the per-call
/// timeout internally (primary); this is the backstop for a wedged or
/// no-timeout runner and the only finalizer for fire-and-forget sessions. A
/// session started without an explicit `timeout` falls back to the configured
/// wall-clock cap so it can never run forever.
#[cfg(target_os = "linux")]
pub(crate) fn command_session_reaper_sweep() {
    workspace_run_manager().sweep_expired(Instant::now());
}

/// Startup recovery (sense-2 §2.4): a previous daemon may have left ephemeral
/// command-session metadata behind. Park an `orphan_reaped` completion for each
/// so a recovering agent learns the session is dead, then remove the stale dir.
///
/// We deliberately do **not** `killpg` the old children: their pgids are not
/// persisted, so a restarted daemon could otherwise signal a reused PID. Their
/// own runner timeout reclaims them; lease cleanup is left to LayerStack GC.
#[cfg(target_os = "linux")]
pub(crate) fn recover_orphaned_command_sessions() {
    let dir = command_session_scratch_root();
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if let Ok(bytes) = std::fs::read(path.join("metadata.json")) {
            if let Ok(meta) = serde_json::from_slice::<Value>(&bytes) {
                let id = meta
                    .get("command_session_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if !id.is_empty() {
                    let caller_id = meta
                        .get("caller_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let command = meta
                        .get("command")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let result = CommandResponse {
                        status: "error".to_owned(),
                        exit_code: Some(1),
                        stdout: String::new(),
                        stderr: "orphan_reaped: daemon restarted".to_owned(),
                        command_session_id: Some(id.to_owned()),
                        workspace_mode: None,
                        metadata: Value::Null,
                    };
                    workspace_run_manager().push_completed(CommandSessionCompletion {
                        command_session_id: id.to_owned(),
                        caller_id: caller_id.to_owned(),
                        command: command.to_owned(),
                        result,
                    });
                }
            }
        }
        let _ = std::fs::remove_dir_all(&path);
    }
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn command_session_reaper_sweep() {}

#[cfg(not(target_os = "linux"))]
pub(crate) fn recover_orphaned_command_sessions() {}

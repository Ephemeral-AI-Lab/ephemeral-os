//! Command-session operations for the daemon dispatcher.

#[cfg(target_os = "linux")]
mod finalize;
#[cfg(target_os = "linux")]
mod lifecycle;
#[cfg(any(target_os = "linux", test))]
mod output;
#[cfg(target_os = "linux")]
mod pty;
#[cfg(any(target_os = "linux", test))]
mod session;

#[cfg(target_os = "linux")]
use std::sync::Arc;
use std::sync::{OnceLock, RwLock};
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

// Test-support imports: the `tests` child module pulls these through `use
// super::*`. They exist only when that linux-gated test code is compiled.
#[cfg(all(test, target_os = "linux"))]
use std::collections::HashMap;
#[cfg(all(test, target_os = "linux"))]
use std::fs::OpenOptions;
#[cfg(all(test, target_os = "linux"))]
use std::path::PathBuf;
#[cfg(all(test, target_os = "linux"))]
use std::sync::Mutex;

use serde_json::{json, Value};

#[cfg(all(test, target_os = "linux"))]
use output::{CommandSessionOutput, CommandSessionOutputCursor};
#[cfg(target_os = "linux")]
use session::{command_session_registry, lock_command_session_state, CommandSession};

use crate::config::CommandSessionConfig;
use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::response_timings::u64_to_f64_saturating;

#[cfg(target_os = "linux")]
use finalize::*;
#[cfg(target_os = "linux")]
pub(crate) use lifecycle::*;

pub(crate) fn configure_command_sessions(config: &CommandSessionConfig) {
    let mut guard = command_session_config_cell()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *guard = config.clone();
}

#[cfg(any(target_os = "linux", test))]
#[cfg(any(target_os = "linux", test))]
// Non-Linux test builds compile command output helpers without the Linux
// lifecycle call graph that normally reads this config.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(super) fn command_session_config() -> CommandSessionConfig {
    command_session_config_cell()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

fn command_session_config_cell() -> &'static RwLock<CommandSessionConfig> {
    static CONFIG: OnceLock<RwLock<CommandSessionConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| RwLock::new(default_command_session_config()))
}

fn default_command_session_config() -> CommandSessionConfig {
    CommandSessionConfig {
        scratch_root: std::path::PathBuf::from("/eos/scratch/command-sessions"),
        default_yield_time_ms: 1000,
        quiet_ms: 50,
        cancel_wait_ms: 500,
        output_drain_grace_ms: 500,
        max_session_s: 6 * 60 * 60,
        output_ring_max_bytes: 1024 * 1024,
        output_spool_max_bytes: 32 * 1024 * 1024,
    }
}

/// `api.v1.exec_command` — command-session start contract.
pub fn op_exec_command(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let cmd = require_command_string(args, "cmd")?;
    #[cfg(target_os = "linux")]
    let command_config = command_session_config();
    #[cfg(not(target_os = "linux"))]
    let _ = &cmd;
    let timeout_seconds = optional_u64(args, "timeout")
        .or_else(|| optional_u64(args, "timeout_seconds"))
        .map(u64_to_f64_saturating);
    #[cfg(not(target_os = "linux"))]
    if crate::services::isolated_workspace::agent_has_active_handle(agent_id_arg(args)) {
        return Ok(command_result(
            "error",
            None,
            "",
            "isolated exec_command is only supported on linux",
            None,
        ));
    }
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::services::isolated_workspace::command_handle_for_args(args) {
        let yield_time_ms =
            optional_u64(args, "yield_time_ms").unwrap_or(command_config.default_yield_time_ms);
        return start_isolated_command_session(args, &cmd, timeout_seconds, yield_time_ms, handle);
    }

    #[cfg(target_os = "linux")]
    {
        let yield_time_ms =
            optional_u64(args, "yield_time_ms").unwrap_or(command_config.default_yield_time_ms);
        start_command_session(args, &cmd, timeout_seconds, yield_time_ms)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = timeout_seconds;
        Ok(command_result(
            "error",
            None,
            "",
            "command sessions are only supported on linux",
            None,
        ))
    }
}

// Dispatcher op handlers share the `Result<Value, DaemonError>` ABI even when
// a specific op encodes all domain failures in its JSON response.
#[cfg_attr(
    not(target_os = "linux"),
    expect(
        clippy::unnecessary_wraps,
        reason = "dispatcher handlers share a fallible ABI"
    )
)]
pub fn op_command_write_stdin(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        command_session_write_stdin(args)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(command_session_not_found())
    }
}

#[cfg_attr(
    not(target_os = "linux"),
    expect(
        clippy::unnecessary_wraps,
        reason = "dispatcher handlers share a fallible ABI"
    )
)]
pub fn op_command_cancel(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        command_session_cancel(args)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(command_session_not_found())
    }
}

#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_command_collect_completed(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        Ok(command_session_registry().collect_completed(args))
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(json!({"success": true, "completions": []}))
    }
}

#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_command_session_count(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    #[cfg(target_os = "linux")]
    {
        let count = command_session_registry().count_by_agent(&agent_id);
        Ok(json!({"success": true, "agent_id": agent_id, "count": count}))
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(json!({"success": true, "agent_id": agent_id, "count": 0}))
    }
}

fn require_command_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| DaemonError::InvalidEnvelope(format!("{key} is required")))?;
    if value.trim().is_empty() {
        return Err(DaemonError::InvalidEnvelope(format!(
            "{key} must be non-empty"
        )));
    }
    Ok(value.to_owned())
}

#[cfg(not(target_os = "linux"))]
fn agent_id_arg(args: &Value) -> &str {
    args.get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
}

fn optional_u64(args: &Value, key: &str) -> Option<u64> {
    args.get(key).and_then(|value| {
        value
            .as_u64()
            .or_else(|| value.as_i64().and_then(|value| u64::try_from(value).ok()))
    })
}

fn command_result(
    status: &str,
    exit_code: Option<i64>,
    stdout: &str,
    stderr: &str,
    command_session_id: Option<String>,
) -> Value {
    let mut response = json!({
        "status": status,
        "exit_code": exit_code,
        "output": {
            "stdout": stdout,
            "stderr": stderr,
        },
    });
    if let Some(command_session_id) = command_session_id {
        response["command_session_id"] = json!(command_session_id);
    }
    response
}

fn command_session_not_found() -> Value {
    command_result("error", None, "", "command_session_not_found", None)
}

#[cfg(target_os = "linux")]
/// Best-effort lifecycle backstop for callers that bypass the model-facing
/// `RequireNoBackgroundSessions` hook.
pub fn cleanup_command_sessions_for_agent(agent_id: &str, grace_s: Option<f64>) -> usize {
    let agent_id = agent_id.trim();
    if agent_id.is_empty() {
        return 0;
    }
    let sessions: Vec<Arc<CommandSession>> = command_session_registry()
        .live()
        .into_iter()
        .filter(|session| session.agent_id == agent_id)
        .collect();
    if sessions.is_empty() {
        return 0;
    }
    for session in &sessions {
        *lock_command_session_state(&session.cancelled) = true;
        terminate_command_process_group(session.pgid);
    }

    let cancel_wait_s = command_session_config().cancel_wait_ms as f64 / 1000.0;
    let wait_s = grace_s.unwrap_or(cancel_wait_s).max(cancel_wait_s);
    let deadline = Instant::now() + Duration::from_secs_f64(wait_s);
    let mut pending = sessions.clone();
    loop {
        pending.retain(|session| session.try_finalize(true).is_none());
        if pending.is_empty() || Instant::now() >= deadline {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    for session in &pending {
        let _ = session.try_finalize(true);
    }
    sessions.len()
}

#[cfg(not(target_os = "linux"))]
pub const fn cleanup_command_sessions_for_agent(_agent_id: &str, _grace_s: Option<f64>) -> usize {
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
pub fn command_session_reaper_sweep() {
    let now = Instant::now();
    for session in command_session_registry().live() {
        let deadline = session.timeout_deadline.unwrap_or_else(|| {
            session.started_at + Duration::from_secs(command_session_config().max_session_s)
        });
        if now > deadline {
            terminate_command_process_group(session.pgid);
        }
        let _ = session.try_finalize(true);
    }
}

/// Startup recovery (sense-2 §2.4): a previous daemon may have left ephemeral
/// command-session metadata behind. Park an `orphan_reaped` completion for each
/// so a recovering agent learns the session is dead, then remove the stale dir.
///
/// We deliberately do **not** `killpg` the old children: their pgids are not
/// persisted, so a restarted daemon could otherwise signal a reused PID. Their
/// own runner timeout reclaims them; lease cleanup is left to LayerStack GC.
#[cfg(target_os = "linux")]
pub fn recover_orphaned_command_sessions() {
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
                    let agent_id = meta
                        .get("agent_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let command = meta
                        .get("command")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let result = command_result(
                        "error",
                        Some(1),
                        "",
                        "orphan_reaped: daemon restarted",
                        Some(id.to_owned()),
                    );
                    command_session_registry().push_completed(json!({
                        "command_session_id": id,
                        "agent_id": agent_id,
                        "command": command,
                        "result": result.clone(),
                        "notification_result": result,
                    }));
                }
            }
        }
        let _ = std::fs::remove_dir_all(&path);
    }
}

#[cfg(not(target_os = "linux"))]
pub fn command_session_reaper_sweep() {}

#[cfg(not(target_os = "linux"))]
pub fn recover_orphaned_command_sessions() {}

#[cfg(test)]
#[path = "../../../tests/command/mod.rs"]
mod tests;

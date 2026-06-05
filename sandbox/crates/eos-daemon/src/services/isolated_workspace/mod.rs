//! Daemon-owned isolated-workspace lifecycle state.
//!
//! This module is the first Rust lifecycle slice behind
//! `api.isolated_workspace.*`: it owns one daemon-local `eos-isolated-workspace`
//! session, keeps the public routing key as `agent_id`, and exposes cloned
//! command handles to the command-session dispatcher. The session holds only the
//! snapshot/lease hinge and scratch upperdir; no OCC publish path is linked
//! through `eos-isolated-workspace`.

mod runtime;

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, PoisonError};

use eos_isolated_workspace::{
    config::{IsolatedWorkspaceConfig, Rfc1918Egress as ConfigRfc1918Egress},
    AgentId, IsolatedError, IsolatedSession, JsonlAuditSink, ResourceCaps,
    Rfc1918Egress as RuntimeRfc1918Egress,
};
use eos_layerstack::{read_workspace_binding, LayerStack};
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::services::command_session;
#[cfg(target_os = "linux")]
use runtime::command_handle_from;
#[cfg(target_os = "linux")]
pub use runtime::CommandHandle;
use runtime::{DaemonLayerStackPort, DaemonNamespaceRuntime};

const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";

type DaemonSession = IsolatedSession<DaemonLayerStackPort, DaemonNamespaceRuntime, JsonlAuditSink>;

struct DaemonIsolatedState {
    #[cfg(target_os = "linux")]
    layer_stack_root: PathBuf,
    session: DaemonSession,
    active_command_sessions: HashMap<String, String>,
}

pub(crate) fn configure_isolated_workspace(config: &IsolatedWorkspaceConfig) {
    let mut guard = isolated_workspace_config_cell()
        .write()
        .unwrap_or_else(PoisonError::into_inner);
    *guard = config.clone();
}

// Dispatcher op handlers share the `Result<Value, DaemonError>` ABI even when
// isolated-workspace failures are represented as structured JSON responses.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_enter(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = match require_arg(args, "agent_id") {
        Ok(agent_id) => agent_id,
        Err(error) => return Ok(error),
    };
    let root = match require_arg(args, "layer_stack_root") {
        Ok(root) => PathBuf::from(root),
        Err(error) => return Ok(error),
    };
    command_session::cleanup_command_sessions_for_agent(&agent_id, None);
    match ensure_state(&root)
        .and_then(|()| with_state(|state| state.session.enter(&AgentId(agent_id))))
    {
        Ok(handle) => Ok(json!({
            "success": true,
            "manifest_version": handle.manifest_version,
            "manifest_root_hash": handle.manifest_root_hash,
            "workspace_handle_id": handle.workspace_handle_id.0,
            "workspace_root": handle.workspace_root,
        })),
        Err(error) => Ok(error_payload(&error)),
    }
}

pub fn op_exit(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = match require_arg(args, "agent_id") {
        Ok(agent_id) => agent_id,
        Err(error) => return Ok(error),
    };
    let grace_s = args.get("grace_s").and_then(Value::as_f64);
    command_session::cleanup_command_sessions_for_agent(&agent_id, grace_s);
    with_state(|state| {
        let response = state.session.exit(&AgentId(agent_id.clone()), grace_s)?;
        state
            .active_command_sessions
            .retain(|_, owner| owner != &agent_id);
        Ok(response)
    })
    .map_or_else(|error| Ok(error_payload(&error)), Ok)
}

// Dispatcher op handlers share the fallible ABI even though status misses are
// represented as `{success: true, open: false}`.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_status(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = match require_arg(args, "agent_id") {
        Ok(agent_id) => agent_id,
        Err(error) => return Ok(error),
    };
    match with_state(|state| Ok(state.session.get_handle(&AgentId(agent_id)))) {
        Ok(Some(handle)) => Ok(json!({
            "success": true,
            "open": true,
            "manifest_version": handle.manifest_version,
            "manifest_root_hash": handle.manifest_root_hash,
            "workspace_root": handle.workspace_root,
            "created_at": handle.created_at,
            "last_activity": handle.last_activity,
        })),
        Ok(None) => Ok(json!({"success": true, "open": false})),
        Err(error) => Ok(error_payload(&error)),
    }
}

// Dispatcher op handlers share the fallible ABI even though disabled state is
// represented as an empty open-agent list.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_list_open(_args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    match with_state(|state| Ok(state.session.list_open_agents())) {
        Ok(open_agent_ids) => Ok(json!({"success": true, "open_agent_ids": open_agent_ids})),
        Err(IsolatedError::FeatureDisabled) => Ok(json!({"success": true, "open_agent_ids": []})),
        Err(error) => Ok(error_payload(&error)),
    }
}

// Dispatcher op handlers share the fallible ABI even though harness gating is
// represented as a structured JSON error.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub fn op_test_reset(_args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    if !env_true(TEST_HARNESS_ENV) {
        return Ok(error_json(
            "forbidden",
            "api.isolated_workspace.test_reset requires EOS_ISOLATED_WORKSPACE_TEST_HARNESS=true",
            json!({}),
        ));
    }
    let exited_agents = {
        let mut guard = lock_state_cell();
        let exited_agents = if let Some(state) = guard.as_mut() {
            let agents = state.session.list_open_agents();
            state.active_command_sessions.clear();
            for agent_id in &agents {
                let _ = state.session.exit(&AgentId(agent_id.clone()), Some(0.0));
            }
            state.session.reap_orphan_resources();
            agents
        } else {
            Vec::new()
        };
        *guard = None;
        exited_agents
    };
    reset_test_manager_file();
    Ok(json!({"success": true, "reset": true, "exited_agents": exited_agents}))
}

#[cfg(target_os = "linux")]
pub fn command_handle_for_args(args: &Value) -> Option<CommandHandle> {
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .trim()
        .to_owned();
    if agent_id.is_empty() {
        return None;
    }
    let (layer_stack_root, handle) = {
        let guard = lock_state_cell();
        guard.as_ref().and_then(|state| {
            state
                .session
                .get_handle(&AgentId(agent_id))
                .map(|handle| (state.layer_stack_root.clone(), handle))
        })
    }?;
    Some(command_handle_from(&layer_stack_root, handle))
}

pub fn agent_has_active_handle(agent_id: &str) -> bool {
    let agent_id = agent_id.trim();
    if agent_id.is_empty() {
        return false;
    }
    let guard = lock_state_cell();
    guard
        .as_ref()
        .and_then(|state| state.session.get_handle(&AgentId(agent_id.to_owned())))
        .is_some()
}

pub fn ttl_sweep() -> usize {
    let mut guard = lock_state_cell();
    let Some(state) = guard.as_mut() else {
        return 0;
    };
    let active_agents = state
        .active_command_sessions
        .values()
        .cloned()
        .collect::<HashSet<_>>();
    state.session.ttl_sweep(&active_agents)
}

#[cfg(any(target_os = "linux", test))]
pub fn register_command_session(agent_id: &str, command_session_id: &str) {
    let mut guard = lock_state_cell();
    if let Some(state) = guard.as_mut() {
        state
            .active_command_sessions
            .insert(command_session_id.to_owned(), agent_id.to_owned());
    }
}

#[cfg(target_os = "linux")]
pub fn unregister_command_session(agent_id: &str, command_session_id: &str) {
    let mut guard = lock_state_cell();
    if let Some(state) = guard.as_mut() {
        if state
            .active_command_sessions
            .get(command_session_id)
            .is_some_and(|owner| owner == agent_id)
        {
            state.active_command_sessions.remove(command_session_id);
        }
    }
}

#[cfg(target_os = "linux")]
pub fn record_tool_call(agent_id: &str, payload: Value) {
    let mut guard = lock_state_cell();
    if let Some(state) = guard.as_mut() {
        state
            .session
            .record_tool_call(&AgentId(agent_id.to_owned()), payload);
    }
}

fn ensure_state(root: &Path) -> Result<(), IsolatedError> {
    {
        let mut guard = lock_state_cell();
        if guard.is_none() {
            let config = isolated_workspace_config();
            let mut caps = resource_caps_from_config(&config);
            if !caps.enabled {
                return Err(IsolatedError::FeatureDisabled);
            }
            if let Some(binding) = read_workspace_binding(root).map_err(setup_error)? {
                caps.eos_workspace_root = binding.workspace_root;
            }
            let stack = LayerStack::open(root.to_path_buf()).map_err(setup_error)?;
            let mut session = IsolatedSession::with_scratch_root(
                caps,
                DaemonLayerStackPort {
                    stack: Arc::new(Mutex::new(stack)),
                },
                DaemonNamespaceRuntime,
                JsonlAuditSink::new(&config.audit_jsonl_path),
                config.scratch_root,
            );
            session.initialize()?;
            *guard = Some(DaemonIsolatedState {
                #[cfg(target_os = "linux")]
                layer_stack_root: root.to_path_buf(),
                session,
                active_command_sessions: HashMap::new(),
            });
        }
    }
    Ok(())
}

fn isolated_workspace_config() -> IsolatedWorkspaceConfig {
    isolated_workspace_config_cell()
        .read()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
}

fn isolated_workspace_config_cell() -> &'static std::sync::RwLock<IsolatedWorkspaceConfig> {
    static CONFIG: OnceLock<std::sync::RwLock<IsolatedWorkspaceConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| std::sync::RwLock::new(default_isolated_workspace_config()))
}

fn default_isolated_workspace_config() -> IsolatedWorkspaceConfig {
    IsolatedWorkspaceConfig {
        enabled: false,
        scratch_root: PathBuf::from("/eos/scratch/isolated"),
        audit_jsonl_path: PathBuf::from("/eos/scratch/isolated/audit.jsonl"),
        ttl_s: 1800.0,
        total_cap: 5,
        upperdir_bytes: 1_073_741_824,
        memavail_fraction: 0.5,
        setup_timeout_s: 30.0,
        exit_grace_s: 0.25,
        rfc1918_egress: ConfigRfc1918Egress::Allow,
        fallback_dns: "1.1.1.1".to_owned(),
        workspace_root: PathBuf::from("/testbed"),
        sample_interval_s: 0.5,
    }
}

fn resource_caps_from_config(config: &IsolatedWorkspaceConfig) -> ResourceCaps {
    ResourceCaps {
        enabled: config.enabled,
        ttl_s: config.ttl_s,
        total_cap: config.total_cap,
        upperdir_bytes: config.upperdir_bytes,
        memavail_fraction: config.memavail_fraction,
        setup_timeout_s: config.setup_timeout_s,
        exit_grace_s: config.exit_grace_s,
        rfc1918_egress: match config.rfc1918_egress {
            ConfigRfc1918Egress::Allow => RuntimeRfc1918Egress::Allow,
            ConfigRfc1918Egress::Deny => RuntimeRfc1918Egress::Deny,
        },
        fallback_dns: config.fallback_dns.clone(),
        eos_workspace_root: config.workspace_root.to_string_lossy().into_owned(),
        sample_interval_s: config.sample_interval_s,
    }
}

fn with_state<T>(
    f: impl FnOnce(&mut DaemonIsolatedState) -> Result<T, IsolatedError>,
) -> Result<T, IsolatedError> {
    lock_state_cell()
        .as_mut()
        .ok_or(IsolatedError::FeatureDisabled)
        .and_then(f)
}

fn state_cell() -> &'static Mutex<Option<DaemonIsolatedState>> {
    static STATE: OnceLock<Mutex<Option<DaemonIsolatedState>>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(None))
}

fn lock_state_cell() -> MutexGuard<'static, Option<DaemonIsolatedState>> {
    state_cell().lock().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
pub(crate) fn lock_isolated_test_state() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
}

fn require_arg(args: &Value, key: &str) -> Result<String, Value> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(error_json(
            "invalid_argument",
            format!("{key} is required"),
            json!({"key": key}),
        ));
    }
    Ok(value)
}

fn setup_error(error: impl std::fmt::Display) -> IsolatedError {
    IsolatedError::SetupFailed {
        step: error.to_string(),
    }
}

fn error_payload(error: &IsolatedError) -> Value {
    let details = match error {
        IsolatedError::AlreadyOpen {
            created_at,
            last_activity,
        } => json!({
            "created_at": created_at,
            "last_activity": last_activity,
        }),
        IsolatedError::QuotaExceeded { total_cap } => json!({
            "total_cap": total_cap,
        }),
        IsolatedError::HostRamPressure {
            required_bytes,
            budget_bytes,
        } => json!({
            "required_bytes": required_bytes,
            "budget_bytes": budget_bytes,
        }),
        IsolatedError::SetupFailed { step } | IsolatedError::SetupTimeout { step } => json!({
            "failed_step": step,
        }),
        _ => json!({}),
    };
    error_json(error.kind(), error.to_string(), details)
}

fn error_json(kind: &str, message: impl Into<String>, details: Value) -> Value {
    json!({
        "success": false,
        "error": {
            "kind": kind,
            "message": message.into(),
            "details": if details.is_null() { json!({}) } else { details },
        },
    })
}

fn env_true(key: &str) -> bool {
    std::env::var(key)
        .unwrap_or_default()
        .trim()
        .eq_ignore_ascii_case("true")
}

fn test_runtime_stub_enabled() -> bool {
    env_true(TEST_HARNESS_ENV)
}

fn reset_test_manager_file() {
    let session_root = isolated_workspace_config().scratch_root;
    let _ = std::fs::remove_dir_all(&session_root);
    if std::fs::create_dir_all(&session_root).is_err() {
        return;
    }
    let _ = std::fs::write(
        session_root.join("manager.json"),
        br#"{"schema_version":1,"handles":[]}"#,
    );
}

#[cfg(test)]
#[path = "../../../tests/isolated_workspace/service.rs"]
mod tests;

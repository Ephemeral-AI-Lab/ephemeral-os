//! Daemon-owned isolated-workspace lifecycle state.
//!
//! This module is the first Rust lifecycle slice behind
//! `api.isolated_workspace.*`: it owns one daemon-local `eos-isolated`
//! session, keeps the public routing key as `agent_id`, and exposes cloned
//! command handles to the command/PTY dispatcher. The session holds only the
//! snapshot/lease hinge and scratch upperdir; no OCC publish path is linked
//! through `eos-isolated`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use eos_isolated::{
    AgentId, IsolatedError, IsolatedSession, JsonlAuditSink, LayerStackSnapshotPort,
    NamespaceRuntimePort, ResourceCaps, SnapshotLease, WorkspaceHandle,
};
use eos_layerstack::LayerStack;
use serde_json::{json, Value};

use crate::command;
use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

pub(crate) const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";
pub(crate) const TEST_SCRATCH_ROOT_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_SCRATCH_ROOT";

type DaemonSession = IsolatedSession<DaemonLayerStackPort, DaemonNamespaceRuntime, JsonlAuditSink>;

#[derive(Debug, Clone)]
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(crate) struct CommandHandle {
    pub agent_id: String,
    pub workspace_handle_id: String,
    pub layer_stack_root: PathBuf,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: PathBuf,
    pub scratch_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub cgroup_path: Option<PathBuf>,
}

struct DaemonIsolatedState {
    layer_stack_root: PathBuf,
    session: DaemonSession,
    active_ptys: HashMap<String, String>,
}

#[derive(Clone)]
struct DaemonLayerStackPort {
    stack: Arc<Mutex<LayerStack>>,
}

impl LayerStackSnapshotPort for DaemonLayerStackPort {
    fn acquire_snapshot(&self, request_id: &str) -> Result<SnapshotLease, IsolatedError> {
        let mut stack = self.stack.lock().expect("layer stack poisoned");
        let lease = stack.acquire_snapshot(request_id).map_err(setup_error)?;
        Ok(SnapshotLease {
            lease_id: lease.lease_id,
            manifest_version: lease.manifest_version,
            root_hash: lease.root_hash,
            layer_paths: lease.layer_paths,
        })
    }

    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError> {
        let mut stack = self.stack.lock().expect("layer stack poisoned");
        stack.release_lease(lease_id).map_err(setup_error)
    }
}

#[derive(Default)]
struct DaemonNamespaceRuntime;

impl NamespaceRuntimePort for DaemonNamespaceRuntime {
    fn spawn_ns_holder(
        &self,
        _handle: &mut WorkspaceHandle,
        _setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError> {
        Ok(0)
    }

    fn open_ns_fds(&self, _holder_pid: i32) -> Result<HashMap<String, i32>, IsolatedError> {
        Ok(HashMap::new())
    }

    fn mount_overlay(
        &self,
        _handle: &WorkspaceHandle,
        _layer_paths: &[String],
    ) -> Result<(), IsolatedError> {
        Ok(())
    }

    fn configure_dns(
        &self,
        _handle: &WorkspaceHandle,
        _fallback_dns: &str,
    ) -> Result<bool, IsolatedError> {
        Ok(false)
    }

    fn signal_net_ready(
        &self,
        _handle: &WorkspaceHandle,
        _setup_timeout_s: f64,
    ) -> Result<(), IsolatedError> {
        Ok(())
    }

    fn create_cgroup(&self, _handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError> {
        Ok(PathBuf::new())
    }

    fn kill_holder(&self, _holder_pid: i32, _grace_s: f64) -> Result<(), IsolatedError> {
        Ok(())
    }
}

pub(crate) fn op_enter(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = match require_arg(args, "agent_id") {
        Ok(agent_id) => agent_id,
        Err(error) => return Ok(error),
    };
    let root = match require_arg(args, "layer_stack_root") {
        Ok(root) => PathBuf::from(root),
        Err(error) => return Ok(error),
    };
    match ensure_state(root)
        .and_then(|()| with_state(|state| state.session.enter(&AgentId(agent_id))))
    {
        Ok(handle) => Ok(json!({
            "success": true,
            "manifest_version": handle.manifest_version,
            "manifest_root_hash": handle.manifest_root_hash,
            "workspace_handle_id": handle.workspace_handle_id.0,
        })),
        Err(error) => Ok(error_payload(error)),
    }
}

pub(crate) fn op_exit(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = match require_arg(args, "agent_id") {
        Ok(agent_id) => agent_id,
        Err(error) => return Ok(error),
    };
    let force_cancel = args
        .get("force_cancel")
        .or_else(|| args.get("force_cancel_pty"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let grace_s = args.get("grace_s").and_then(Value::as_f64);
    let active_ptys = active_pty_ids(&agent_id);
    if !active_ptys.is_empty() {
        if !force_cancel {
            return Ok(active_pty_error(&agent_id, active_ptys));
        }
        for pty_session_id in &active_ptys {
            let cancelled = command::cancel_pty_session_for_exit(pty_session_id)?;
            if !cancelled {
                unregister_pty_id(pty_session_id);
            }
        }
        let deadline = Instant::now() + Duration::from_secs_f64(grace_s.unwrap_or(0.25).max(0.0));
        while !active_pty_ids(&agent_id).is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let still_active = active_pty_ids(&agent_id);
        if !still_active.is_empty() {
            return Ok(active_pty_error(&agent_id, still_active));
        }
    }

    with_state(|state| state.session.exit(&AgentId(agent_id), grace_s))
        .map_or_else(|error| Ok(error_payload(error)), Ok)
}

pub(crate) fn op_status(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
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
            "created_at": handle.created_at,
            "last_activity": handle.last_activity,
        })),
        Ok(None) => Ok(json!({"success": true, "open": false})),
        Err(error) => Ok(error_payload(error)),
    }
}

pub(crate) fn op_list_open(
    _args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    match with_state(|state| Ok(state.session.list_open_agents())) {
        Ok(open_agent_ids) => Ok(json!({"success": true, "open_agent_ids": open_agent_ids})),
        Err(IsolatedError::FeatureDisabled) => Ok(json!({"success": true, "open_agent_ids": []})),
        Err(error) => Ok(error_payload(error)),
    }
}

pub(crate) fn op_test_reset(
    _args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    if !env_true(TEST_HARNESS_ENV) {
        return Ok(error_json(
            "forbidden",
            "api.isolated_workspace.test_reset requires EOS_ISOLATED_WORKSPACE_TEST_HARNESS=true",
            json!({}),
        ));
    }
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    let exited_agents = if let Some(state) = guard.as_mut() {
        let agents = state.session.list_open_agents();
        state.active_ptys.clear();
        for agent_id in &agents {
            let _ = state.session.exit(&AgentId(agent_id.clone()), Some(0.0));
        }
        agents
    } else {
        Vec::new()
    };
    *guard = None;
    Ok(json!({"success": true, "reset": true, "exited_agents": exited_agents}))
}

pub(crate) fn command_handle_for_args(args: &Value) -> Option<CommandHandle> {
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .trim()
        .to_owned();
    if agent_id.is_empty() {
        return None;
    }
    let guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    let state = guard.as_ref()?;
    let handle = state.session.get_handle(&AgentId(agent_id.clone()))?;
    Some(command_handle_from(&state.layer_stack_root, handle))
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(crate) fn register_pty(agent_id: &str, pty_session_id: &str) {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    if let Some(state) = guard.as_mut() {
        state
            .active_ptys
            .insert(pty_session_id.to_owned(), agent_id.to_owned());
    }
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(crate) fn unregister_pty(agent_id: &str, pty_session_id: &str) {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    if let Some(state) = guard.as_mut() {
        if state
            .active_ptys
            .get(pty_session_id)
            .is_some_and(|owner| owner == agent_id)
        {
            state.active_ptys.remove(pty_session_id);
        }
    }
}

pub(crate) fn unregister_pty_id(pty_session_id: &str) {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    if let Some(state) = guard.as_mut() {
        state.active_ptys.remove(pty_session_id);
    }
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(crate) fn record_tool_call(agent_id: &str, payload: Value) {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    if let Some(state) = guard.as_mut() {
        state
            .session
            .record_tool_call(&AgentId(agent_id.to_owned()), payload);
    }
}

fn ensure_state(root: PathBuf) -> Result<(), IsolatedError> {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    if guard.is_none() {
        let caps = ResourceCaps::from_env();
        if !caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        let scratch_root = scratch_root();
        let stack = LayerStack::open(root.clone()).map_err(setup_error)?;
        let mut session = IsolatedSession::with_scratch_root(
            caps,
            DaemonLayerStackPort {
                stack: Arc::new(Mutex::new(stack)),
            },
            DaemonNamespaceRuntime,
            JsonlAuditSink::from_env(),
            scratch_root,
        );
        session.initialize()?;
        *guard = Some(DaemonIsolatedState {
            layer_stack_root: root,
            session,
            active_ptys: HashMap::new(),
        });
    }
    Ok(())
}

fn with_state<T>(
    f: impl FnOnce(&mut DaemonIsolatedState) -> Result<T, IsolatedError>,
) -> Result<T, IsolatedError> {
    let mut guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    let Some(state) = guard.as_mut() else {
        return Err(IsolatedError::FeatureDisabled);
    };
    f(state)
}

fn active_pty_ids(agent_id: &str) -> Vec<String> {
    let guard = state_cell()
        .lock()
        .expect("isolated workspace state poisoned");
    guard
        .as_ref()
        .map(|state| {
            state
                .active_ptys
                .iter()
                .filter(|(_, owner)| owner.as_str() == agent_id)
                .map(|(id, _)| id.clone())
                .collect()
        })
        .unwrap_or_default()
}

fn command_handle_from(
    layer_stack_root: &std::path::Path,
    handle: WorkspaceHandle,
) -> CommandHandle {
    CommandHandle {
        agent_id: handle.agent_id.0,
        workspace_handle_id: handle.workspace_handle_id.0,
        layer_stack_root: layer_stack_root.to_path_buf(),
        manifest_version: handle.manifest_version,
        manifest_root_hash: handle.manifest_root_hash,
        workspace_root: PathBuf::from(handle.workspace_root),
        scratch_dir: handle.scratch_dir,
        upperdir: handle.upperdir,
        workdir: handle.workdir,
        layer_paths: handle.layer_paths.into_iter().map(PathBuf::from).collect(),
        cgroup_path: handle.cgroup_path,
    }
}

fn state_cell() -> &'static Mutex<Option<DaemonIsolatedState>> {
    static STATE: OnceLock<Mutex<Option<DaemonIsolatedState>>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(None))
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

fn error_payload(error: IsolatedError) -> Value {
    error_json(error.kind(), error.to_string(), json!({}))
}

fn active_pty_error(agent_id: &str, pty_session_ids: Vec<String>) -> Value {
    error_json(
        "active_pty_sessions",
        "exit_isolated_workspace refused while PTY sessions are active",
        json!({
            "agent_id": agent_id,
            "pty_session_ids": pty_session_ids,
        }),
    )
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

fn scratch_root() -> PathBuf {
    if env_true(TEST_HARNESS_ENV) {
        let root = std::env::var(TEST_SCRATCH_ROOT_ENV)
            .unwrap_or_default()
            .trim()
            .to_owned();
        if !root.is_empty() {
            return PathBuf::from(root);
        }
    }
    PathBuf::from(eos_overlay::OVERLAY_WRITABLE_ROOT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn active_pty_records_block_exit_until_cleared() {
        let _guard = TEST_LOCK.lock().expect("test lock poisoned");
        let _ = op_test_reset(&json!({}), DispatchContext::empty());
        let root =
            std::env::temp_dir().join(format!("eos-daemon-iws-pty-block-{}", std::process::id()));
        let scratch = root.join("scratch");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("layers")).expect("layers");
        std::fs::create_dir_all(root.join("staging")).expect("staging");
        std::fs::write(
            root.join("manifest.json"),
            r#"{"schema_version":1,"version":1,"layers":[]}"#,
        )
        .expect("manifest");
        set_env("EOS_ISOLATED_WORKSPACE_ENABLED", "true");
        set_env(TEST_HARNESS_ENV, "true");
        set_env(TEST_SCRATCH_ROOT_ENV, &scratch.to_string_lossy());

        let entered = op_enter(
            &json!({"agent_id": "agent-pty", "layer_stack_root": root}),
            DispatchContext::empty(),
        )
        .expect("enter response");
        assert_eq!(entered["success"], true);
        register_pty("agent-pty", "pty-block");

        let blocked = op_exit(&json!({"agent_id": "agent-pty"}), DispatchContext::empty())
            .expect("exit response");
        assert_eq!(blocked["success"], false);
        assert_eq!(blocked["error"]["kind"], "active_pty_sessions");

        unregister_pty("agent-pty", "pty-block");
        let exited = op_exit(&json!({"agent_id": "agent-pty"}), DispatchContext::empty())
            .expect("exit response");
        assert_eq!(exited["success"], true);
        let _ = op_test_reset(&json!({}), DispatchContext::empty());
        clear_env("EOS_ISOLATED_WORKSPACE_ENABLED");
        clear_env(TEST_HARNESS_ENV);
        clear_env(TEST_SCRATCH_ROOT_ENV);
        let _ = std::fs::remove_dir_all(&root);
    }

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn set_env(key: &str, value: &str) {
        std::env::set_var(key, value);
    }

    fn clear_env(key: &str) {
        std::env::remove_var(key);
    }
}

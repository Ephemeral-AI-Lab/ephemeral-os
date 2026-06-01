//! Daemon-owned isolated-workspace lifecycle state.
//!
//! This module is the first Rust lifecycle slice behind
//! `api.isolated_workspace.*`: it owns one daemon-local `eos-isolated`
//! session, keeps the public routing key as `agent_id`, and exposes cloned
//! command handles to the command/PTY dispatcher. The session holds only the
//! snapshot/lease hinge and scratch upperdir; no OCC publish path is linked
//! through `eos-isolated`.

use std::collections::HashMap;
#[cfg(target_os = "linux")]
use std::fs::{File, OpenOptions};
#[cfg(target_os = "linux")]
use std::io::Write;
#[cfg(target_os = "linux")]
use std::os::fd::{AsRawFd, IntoRawFd, RawFd};
use std::path::{Path, PathBuf};
#[cfg(target_os = "linux")]
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

use eos_isolated::{
    AgentId, IsolatedError, IsolatedSession, JsonlAuditSink, LayerStackSnapshotPort,
    NamespaceRuntimePort, ResourceCaps, SnapshotLease, WorkspaceHandle,
};
use eos_layerstack::LayerStack;
#[cfg(target_os = "linux")]
use eos_protocol::Intent;
#[cfg(target_os = "linux")]
use eos_runner::{Fd, NsFds, RunMode, RunRequest, ToolCall, WorkspaceRoot};
#[cfg(target_os = "linux")]
use nix::errno::Errno;
#[cfg(target_os = "linux")]
use nix::fcntl::{fcntl, FcntlArg, FdFlag, OFlag};
#[cfg(target_os = "linux")]
use nix::sys::signal::{kill, Signal};
#[cfg(target_os = "linux")]
use nix::unistd::{close, pipe2, read, Pid};
use serde_json::{json, Value};

use crate::command;
use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";
const TEST_SCRATCH_ROOT_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_SCRATCH_ROOT";

type DaemonSession = IsolatedSession<DaemonLayerStackPort, DaemonNamespaceRuntime, JsonlAuditSink>;

#[cfg(target_os = "linux")]
#[derive(Debug, Clone)]
pub struct CommandHandle {
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
    pub ns_fds: HashMap<String, i32>,
    pub cgroup_path: Option<PathBuf>,
}

struct DaemonIsolatedState {
    #[cfg(target_os = "linux")]
    layer_stack_root: PathBuf,
    session: DaemonSession,
    active_ptys: HashMap<String, String>,
}

#[cfg(target_os = "linux")]
fn holder_children() -> &'static Mutex<HashMap<i32, Child>> {
    static CHILDREN: OnceLock<Mutex<HashMap<i32, Child>>> = OnceLock::new();
    CHILDREN.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(target_os = "linux")]
fn lock_holder_children() -> Result<MutexGuard<'static, HashMap<i32, Child>>, IsolatedError> {
    holder_children()
        .lock()
        .map_err(|_| setup_error("ns-holder child registry lock poisoned"))
}

#[derive(Clone)]
struct DaemonLayerStackPort {
    stack: Arc<Mutex<LayerStack>>,
}

impl LayerStackSnapshotPort for DaemonLayerStackPort {
    fn acquire_snapshot(&self, request_id: &str) -> Result<SnapshotLease, IsolatedError> {
        let lease = {
            let mut stack = self
                .stack
                .lock()
                .map_err(|_| setup_error("layer stack lock poisoned"))?;
            stack.acquire_snapshot(request_id).map_err(setup_error)?
        };
        Ok(SnapshotLease {
            lease_id: lease.lease_id,
            manifest_version: lease.manifest_version,
            root_hash: lease.root_hash,
            layer_paths: lease.layer_paths,
        })
    }

    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError> {
        let mut stack = self
            .stack
            .lock()
            .map_err(|_| setup_error("layer stack lock poisoned"))?;
        stack.release_lease(lease_id).map_err(setup_error)
    }

    fn active_lease_count(&self) -> Result<Option<usize>, IsolatedError> {
        let stack = self
            .stack
            .lock()
            .map_err(|_| setup_error("layer stack lock poisoned"))?;
        Ok(Some(stack.active_lease_count()))
    }
}

#[derive(Default)]
struct DaemonNamespaceRuntime;

impl NamespaceRuntimePort for DaemonNamespaceRuntime {
    fn spawn_ns_holder(
        &self,
        handle: &mut WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError> {
        if env_true(TEST_HARNESS_ENV) {
            return Ok(0);
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
            Ok(0)
        }
        #[cfg(target_os = "linux")]
        {
            let (readiness_read, readiness_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let (control_read, control_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let readiness_child_fd = readiness_write.as_raw_fd();
            let control_child_fd = control_read.as_raw_fd();
            clear_cloexec(readiness_child_fd)?;
            clear_cloexec(control_child_fd)?;
            let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
                .arg("ns-holder")
                .arg(readiness_child_fd.to_string())
                .arg(control_child_fd.to_string())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(setup_error)?;
            drop(readiness_write);
            drop(control_read);
            let readiness_fd = readiness_read.into_raw_fd();
            let control_fd = control_write.into_raw_fd();
            handle.readiness_fd = readiness_fd;
            handle.control_fd = control_fd;
            if let Err(error) = set_nonblocking(readiness_fd)
                .and_then(|()| expect_line(readiness_fd, b"ns-up", setup_timeout_s))
            {
                let _ = child.kill();
                let _ = child.wait();
                let _ = close(readiness_fd);
                let _ = close(control_fd);
                return Err(error);
            }
            let Ok(holder_pid) = i32::try_from(child.id()) else {
                let _ = child.kill();
                let _ = child.wait();
                let _ = close(readiness_fd);
                let _ = close(control_fd);
                return Err(setup_error(format!(
                    "ns-holder pid does not fit i32: {}",
                    child.id()
                )));
            };
            lock_holder_children()?.insert(holder_pid, child);
            Ok(holder_pid)
        }
    }

    fn open_ns_fds(&self, holder_pid: i32) -> Result<HashMap<String, i32>, IsolatedError> {
        if env_true(TEST_HARNESS_ENV) || holder_pid <= 0 {
            return Ok(HashMap::new());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = holder_pid;
            Ok(HashMap::new())
        }
        #[cfg(target_os = "linux")]
        {
            let paths = [
                ("user", format!("/proc/{holder_pid}/ns/user")),
                ("mnt", format!("/proc/{holder_pid}/ns/mnt")),
                ("pid", format!("/proc/{holder_pid}/ns/pid_for_children")),
                ("net", format!("/proc/{holder_pid}/ns/net")),
            ];
            paths
                .into_iter()
                .map(|(name, path)| Ok((name.to_owned(), open_inheritable_fd(path)?)))
                .collect()
        }
    }

    fn mount_overlay(
        &self,
        handle: &WorkspaceHandle,
        layer_paths: &[String],
    ) -> Result<(), IsolatedError> {
        if env_true(TEST_HARNESS_ENV) || handle.holder_pid <= 0 {
            return Ok(());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, layer_paths);
        }
        #[cfg(target_os = "linux")]
        {
            let request = RunRequest {
                mode: RunMode::SetNs,
                tool_call: ToolCall {
                    invocation_id: format!("isolated-mount-{}", handle.workspace_handle_id.0),
                    agent_id: handle.agent_id.0.clone(),
                    verb: "setns_overlay_mount".to_owned(),
                    intent: Intent::WriteAllowed,
                    args: json!({}),
                    background: false,
                },
                workspace_root: WorkspaceRoot(PathBuf::from(&handle.workspace_root)),
                layer_paths: layer_paths.iter().map(PathBuf::from).collect(),
                upperdir: Some(handle.upperdir.clone()),
                workdir: Some(handle.workdir.clone()),
                ns_fds: ns_fds_from_map(&handle.ns_fds),
                cgroup_path: handle.cgroup_path.clone(),
                timeout_seconds: None,
            };
            run_ns_runner_mount_overlay_child(&request)?;
        }
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
        handle: &WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedError> {
        if env_true(TEST_HARNESS_ENV) || handle.holder_pid <= 0 {
            return Ok(());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let payload = handle.veth.as_ref().map_or_else(
                || "net-ready\n".to_owned(),
                |veth| {
                    format!(
                        "net-ready {} {} {} {}\n",
                        veth.ns_name,
                        veth.ns_ip,
                        eos_isolated::BRIDGE_PREFIX_LEN,
                        eos_isolated::GATEWAY
                    )
                },
            );
            write_all_fd(handle.control_fd, payload.as_bytes())?;
            expect_line(handle.readiness_fd, b"ready", setup_timeout_s)?;
        }
        Ok(())
    }

    fn create_cgroup(&self, handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError> {
        if env_true(TEST_HARNESS_ENV) {
            return Ok(PathBuf::new());
        }
        let path = PathBuf::from(eos_isolated::CGROUP_ROOT).join(format!(
            "{}{}",
            eos_isolated::HANDLE_PREFIX,
            handle.workspace_handle_id.0
        ));
        std::fs::create_dir_all(&path).map_err(setup_error)?;
        Ok(path)
    }

    fn kill_holder(&self, holder_pid: i32, grace_s: f64) -> Result<(), IsolatedError> {
        if env_true(TEST_HARNESS_ENV) || holder_pid <= 0 {
            return Ok(());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = grace_s;
        }
        #[cfg(target_os = "linux")]
        {
            let _ = kill(Pid::from_raw(holder_pid), Signal::SIGTERM);
            let child = lock_holder_children()?.remove(&holder_pid);
            if let Some(mut child) = child {
                let deadline = Instant::now() + Duration::from_secs_f64(grace_s.max(0.0));
                while Instant::now() < deadline {
                    if child.try_wait().map_err(setup_error)?.is_some() {
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                let _ = kill(Pid::from_raw(holder_pid), Signal::SIGKILL);
                let _ = child.wait();
            } else {
                thread::sleep(Duration::from_secs_f64(grace_s.max(0.0)));
                let _ = kill(Pid::from_raw(holder_pid), Signal::SIGKILL);
            }
        }
        Ok(())
    }
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
    match ensure_state(&root)
        .and_then(|()| with_state(|state| state.session.enter(&AgentId(agent_id))))
    {
        Ok(handle) => Ok(json!({
            "success": true,
            "manifest_version": handle.manifest_version,
            "manifest_root_hash": handle.manifest_root_hash,
            "workspace_handle_id": handle.workspace_handle_id.0,
        })),
        Err(error) => Ok(error_payload(&error)),
    }
}

pub fn op_exit(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
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
    let mut cancelled_pty_session_ids = Vec::new();
    let mut stale_pty_session_ids = Vec::new();
    if !active_ptys.is_empty() {
        if !force_cancel {
            return Ok(active_pty_error(&agent_id, &active_ptys));
        }
        for pty_session_id in &active_ptys {
            let cancelled = command::cancel_pty_session_for_exit(pty_session_id)?;
            if cancelled {
                cancelled_pty_session_ids.push(pty_session_id.clone());
            } else {
                unregister_pty_id(pty_session_id);
                stale_pty_session_ids.push(pty_session_id.clone());
            }
        }
        let deadline = Instant::now() + Duration::from_secs_f64(grace_s.unwrap_or(0.25).max(0.0));
        while !active_pty_ids(&agent_id).is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let still_active = active_pty_ids(&agent_id);
        if !still_active.is_empty() {
            return Ok(active_pty_error(&agent_id, &still_active));
        }
    }

    with_state(|state| state.session.exit(&AgentId(agent_id.clone()), grace_s)).map_or_else(
        |error| Ok(error_payload(&error)),
        |mut response| {
            annotate_pty_force_cancel(
                &mut response,
                force_cancel,
                &cancelled_pty_session_ids,
                &stale_pty_session_ids,
                &active_pty_ids(&agent_id),
            );
            Ok(response)
        },
    )
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
            state.active_ptys.clear();
            for agent_id in &agents {
                let _ = state.session.exit(&AgentId(agent_id.clone()), Some(0.0));
            }
            agents
        } else {
            Vec::new()
        };
        *guard = None;
        exited_agents
    };
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

#[cfg(any(target_os = "linux", test))]
pub fn register_pty(agent_id: &str, pty_session_id: &str) {
    let mut guard = lock_state_cell();
    if let Some(state) = guard.as_mut() {
        state
            .active_ptys
            .insert(pty_session_id.to_owned(), agent_id.to_owned());
    }
}

#[cfg(target_os = "linux")]
pub fn unregister_pty(agent_id: &str, pty_session_id: &str) {
    let mut guard = lock_state_cell();
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

pub fn unregister_pty_id(pty_session_id: &str) {
    let mut guard = lock_state_cell();
    if let Some(state) = guard.as_mut() {
        state.active_ptys.remove(pty_session_id);
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
            let caps = ResourceCaps::from_env();
            if !caps.enabled {
                return Err(IsolatedError::FeatureDisabled);
            }
            let scratch_root = scratch_root();
            let stack = LayerStack::open(root.to_path_buf()).map_err(setup_error)?;
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
                #[cfg(target_os = "linux")]
                layer_stack_root: root.to_path_buf(),
                session,
                active_ptys: HashMap::new(),
            });
        }
    }
    Ok(())
}

// Keep the state guard alive for exactly the caller closure; tightening around
// the generic closure would make the lock lifetime less explicit.
#[expect(
    clippy::significant_drop_tightening,
    reason = "state guard lifetime intentionally spans the caller closure"
)]
fn with_state<T>(
    f: impl FnOnce(&mut DaemonIsolatedState) -> Result<T, IsolatedError>,
) -> Result<T, IsolatedError> {
    let mut guard = lock_state_cell();
    let Some(state) = guard.as_mut() else {
        return Err(IsolatedError::FeatureDisabled);
    };
    f(state)
}

fn active_pty_ids(agent_id: &str) -> Vec<String> {
    let guard = lock_state_cell();
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

fn annotate_pty_force_cancel(
    response: &mut Value,
    force_cancel: bool,
    cancelled_pty_session_ids: &[String],
    stale_pty_session_ids: &[String],
    active_pty_session_ids_after: &[String],
) {
    let Some(object) = response.as_object_mut() else {
        return;
    };
    object.insert("force_cancel_requested".to_owned(), json!(force_cancel));
    object.insert(
        "force_cancelled_pty_session_ids".to_owned(),
        json!(cancelled_pty_session_ids),
    );
    object.insert(
        "stale_pty_session_ids".to_owned(),
        json!(stale_pty_session_ids),
    );
    object.insert(
        "active_pty_session_ids_after".to_owned(),
        json!(active_pty_session_ids_after),
    );
}

#[cfg(target_os = "linux")]
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
        ns_fds: handle.ns_fds,
        cgroup_path: handle.cgroup_path,
    }
}

#[cfg(target_os = "linux")]
fn open_inheritable_fd(path: impl AsRef<std::path::Path>) -> Result<RawFd, IsolatedError> {
    let file = File::open(path.as_ref()).map_err(setup_error)?;
    clear_cloexec(file.as_raw_fd())?;
    Ok(file.into_raw_fd())
}

#[cfg(target_os = "linux")]
fn clear_cloexec(fd: RawFd) -> Result<(), IsolatedError> {
    fcntl(fd, FcntlArg::F_SETFD(FdFlag::empty())).map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn set_nonblocking(fd: RawFd) -> Result<(), IsolatedError> {
    let flags = fcntl(fd, FcntlArg::F_GETFL).map_err(setup_error)?;
    let flags = OFlag::from_bits_truncate(flags);
    fcntl(fd, FcntlArg::F_SETFL(flags | OFlag::O_NONBLOCK)).map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn expect_line(fd: RawFd, prefix: &[u8], timeout_s: f64) -> Result<(), IsolatedError> {
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_s.max(0.0));
    let mut buf = Vec::new();
    loop {
        if Instant::now() >= deadline {
            return Err(IsolatedError::SetupFailed {
                step: format!(
                    "ns_holder did not signal {}",
                    String::from_utf8_lossy(prefix)
                ),
            });
        }
        let mut chunk = [0_u8; 64];
        match read(fd, &mut chunk) {
            Ok(0) => {
                return Err(IsolatedError::SetupFailed {
                    step: "ns_holder closed pipe before signaling".to_owned(),
                })
            }
            Ok(read) => {
                buf.extend_from_slice(&chunk[..read]);
                if buf.contains(&b'\n') {
                    if buf.starts_with(prefix) {
                        return Ok(());
                    }
                    return Err(IsolatedError::SetupFailed {
                        step: format!("unexpected ns_holder signal: {buf:?}"),
                    });
                }
            }
            Err(Errno::EAGAIN) => thread::sleep(Duration::from_millis(10)),
            Err(Errno::EINTR) => {}
            Err(error) => return Err(setup_error(error)),
        }
    }
}

#[cfg(target_os = "linux")]
fn write_all_fd(fd: RawFd, bytes: &[u8]) -> Result<(), IsolatedError> {
    let mut file = OpenOptions::new()
        .write(true)
        .open(format!("/proc/self/fd/{fd}"))
        .map_err(setup_error)?;
    file.write_all(bytes).map_err(setup_error)
}

#[cfg(target_os = "linux")]
fn ns_fds_from_map(map: &HashMap<String, i32>) -> Option<NsFds> {
    if map.is_empty() {
        return None;
    }
    Some(NsFds {
        user: map.get("user").copied().map(Fd),
        mnt: map.get("mnt").copied().map(Fd),
        pid: map.get("pid").copied().map(Fd),
        net: map.get("net").copied().map(Fd),
    })
}

#[cfg(target_os = "linux")]
fn run_ns_runner_mount_overlay_child(request: &RunRequest) -> Result<(), IsolatedError> {
    let payload = serde_json::to_vec(request).map_err(setup_error)?;
    let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
        .arg("ns-runner")
        .arg("--mount-overlay")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(setup_error)?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| IsolatedError::SetupFailed {
            step: "ns-runner stdin unavailable".to_owned(),
        })?
        .write_all(&payload)
        .map_err(setup_error)?;
    let output = child.wait_with_output().map_err(setup_error)?;
    if output.status.success() {
        return Ok(());
    }
    Err(IsolatedError::SetupFailed {
        step: format!(
            "ns-runner mount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

fn state_cell() -> &'static Mutex<Option<DaemonIsolatedState>> {
    static STATE: OnceLock<Mutex<Option<DaemonIsolatedState>>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(None))
}

fn lock_state_cell() -> MutexGuard<'static, Option<DaemonIsolatedState>> {
    state_cell().lock().unwrap_or_else(PoisonError::into_inner)
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
        IsolatedError::HostRamPressure {
            required_bytes,
            budget_bytes,
        } => json!({
            "required_bytes": required_bytes,
            "budget_bytes": budget_bytes,
        }),
        _ => json!({}),
    };
    error_json(error.kind(), error.to_string(), details)
}

fn active_pty_error(agent_id: &str, pty_session_ids: &[String]) -> Value {
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

    type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn active_pty_records_block_exit_until_cleared() -> TestResult {
        let _guard = TEST_LOCK.lock().map_err(|_| "test lock poisoned")?;
        let _ = op_test_reset(&json!({}), DispatchContext::empty());
        let root =
            std::env::temp_dir().join(format!("eos-daemon-iws-pty-block-{}", std::process::id()));
        let scratch = root.join("scratch");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("layers"))?;
        std::fs::create_dir_all(root.join("staging"))?;
        std::fs::write(
            root.join("manifest.json"),
            r#"{"schema_version":1,"version":1,"layers":[]}"#,
        )?;
        set_env("EOS_ISOLATED_WORKSPACE_ENABLED", "true");
        set_env(TEST_HARNESS_ENV, "true");
        set_env(TEST_SCRATCH_ROOT_ENV, &scratch.to_string_lossy());

        let entered = op_enter(
            &json!({"agent_id": "agent-pty", "layer_stack_root": root}),
            DispatchContext::empty(),
        )?;
        assert_eq!(entered["success"], true);
        register_pty("agent-pty", "pty-block");

        let blocked = op_exit(&json!({"agent_id": "agent-pty"}), DispatchContext::empty())?;
        assert_eq!(blocked["success"], false);
        assert_eq!(blocked["error"]["kind"], "active_pty_sessions");

        let exited = op_exit(
            &json!({"agent_id": "agent-pty", "force_cancel": true}),
            DispatchContext::empty(),
        )?;
        assert_eq!(exited["success"], true);
        assert_eq!(exited["force_cancel_requested"], true);
        assert_eq!(exited["force_cancelled_pty_session_ids"], json!([]));
        assert_eq!(exited["stale_pty_session_ids"], json!(["pty-block"]));
        assert_eq!(exited["active_pty_session_ids_after"], json!([]));
        assert_eq!(
            exited["inspection"]["handle_registered_after"],
            json!(false)
        );
        let _ = op_test_reset(&json!({}), DispatchContext::empty());
        clear_env("EOS_ISOLATED_WORKSPACE_ENABLED");
        clear_env(TEST_HARNESS_ENV);
        clear_env(TEST_SCRATCH_ROOT_ENV);
        let _ = std::fs::remove_dir_all(&root);
        Ok(())
    }

    #[test]
    fn host_ram_pressure_error_keeps_capacity_details() {
        let response = error_payload(&IsolatedError::HostRamPressure {
            required_bytes: 30,
            budget_bytes: 29,
        });
        assert_eq!(response["success"], false);
        assert_eq!(response["error"]["kind"], "host_ram_pressure");
        assert_eq!(response["error"]["details"]["required_bytes"], 30);
        assert_eq!(response["error"]["details"]["budget_bytes"], 29);
    }

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn set_env(key: &str, value: &str) {
        std::env::set_var(key, value);
    }

    fn clear_env(key: &str) {
        std::env::remove_var(key);
    }
}

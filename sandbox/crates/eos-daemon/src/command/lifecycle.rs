//! Linux command-session build & spawn lifecycle.

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{mpsc as std_mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use eos_layerstack::{require_workspace_binding, LayerStack, Lease, WorkspaceBinding};
use eos_protocol::Intent;
use eos_runner::{Fd, NsFds, RunMode, RunRequest, ToolCall, WorkspaceRoot};

use super::finalize::strip_session_id;
use super::output;
use super::output::{CommandSessionOutput, CommandSessionOutputCursor};
use super::pty::open_pty_pair;
use super::session::{command_session_registry, wait_for_yield, CommandSession, WaitOutcome};
use super::{command_result, command_session_config, optional_u64};
use crate::error::DaemonError;
use crate::overlay_runner::{overlay_run_dirs, RunDirCleanup};

pub(crate) struct EphemeralCommandWorkspace {
    pub(crate) root: PathBuf,
    pub(crate) lease_id: String,
    pub(crate) manifest: eos_layerstack::Manifest,
    pub(crate) manifest_version: i64,
    pub(crate) manifest_root_hash: String,
    pub(crate) layer_paths: Vec<PathBuf>,
    pub(crate) workspace_root: PathBuf,
    pub(crate) dirs: eos_ephemeral_workspace::EphemeralRunDirs,
}

pub(crate) struct IsolatedCommandWorkspace {
    pub(crate) handle: crate::isolated::CommandHandle,
    pub(crate) output_path: PathBuf,
    pub(crate) final_path: PathBuf,
}

/// Which workspace a command session finalizes into (sense-2 §4). The notify
/// `publish` flag is orthogonal to this — both kinds can be parked.
pub(crate) enum CommandWorkspaceKind {
    /// Shared ephemeral overlay: finalize publishes via OCC and releases the
    /// per-session lease + run dir.
    Ephemeral(EphemeralCommandWorkspace),
    /// Isolated private workspace: finalize captures record-only; lease/scratch
    /// teardown is deferred to `exit_isolated_workspace`.
    Isolated(IsolatedCommandWorkspace),
}

impl CommandWorkspaceKind {
    /// The runner `--output` result file path (used by `try_finalize`).
    pub(crate) fn output_path(&self) -> &Path {
        match self {
            Self::Ephemeral(workspace) => &workspace.dirs.output_path,
            Self::Isolated(workspace) => &workspace.output_path,
        }
    }
}

struct CommandSessionStartSpec {
    id: String,
    invocation_id: String,
    agent_id: String,
    command: String,
    timeout_seconds: Option<f64>,
}

fn runner_ns_fds(map: &HashMap<String, i32>) -> Option<NsFds> {
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

const fn runner_mode(ns_fds: Option<&NsFds>) -> RunMode {
    if ns_fds.is_some() {
        RunMode::SetNs
    } else {
        RunMode::FreshNs
    }
}

pub(crate) fn require_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(DaemonError::InvalidEnvelope(format!("{key} is required")));
    }
    Ok(value)
}

pub(crate) fn start_isolated_command_session(
    args: &Value,
    cmd: &str,
    timeout_seconds: Option<f64>,
    yield_time_ms: u64,
    handle: crate::isolated::CommandHandle,
) -> Result<Value, DaemonError> {
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or("exec_command")
        .to_owned();
    let spec = CommandSessionStartSpec {
        id: command_session_registry().next_id(),
        invocation_id,
        agent_id: handle.agent_id.clone(),
        command: cmd.to_owned(),
        timeout_seconds,
    };
    let id = spec.id.clone();
    let session = prepare_isolated_command_session(&spec, handle)?;
    command_session_registry().insert(Arc::clone(&session));
    crate::isolated::register_command_session(&session.agent_id, &session.id);
    match wait_for_yield(
        &session,
        yield_time_ms,
        optional_u64(args, "max_output_tokens"),
    ) {
        WaitOutcome::Completed(response) => Ok(strip_session_id(response)),
        WaitOutcome::Running(stdout) => Ok(command_result("running", None, &stdout, "", Some(id))),
    }
}

pub(crate) fn start_command_session(
    args: &Value,
    cmd: &str,
    timeout_seconds: Option<f64>,
    yield_time_ms: u64,
) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or("exec_command")
        .to_owned();
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let binding = require_workspace_binding(&root)?;
    let mut stack = LayerStack::open(root.clone())?;
    let lease = stack.acquire_snapshot(&format!("command_session:{agent_id}:{invocation_id}"))?;
    let spec = CommandSessionStartSpec {
        id: command_session_registry().next_id(),
        invocation_id,
        agent_id,
        command: cmd.to_owned(),
        timeout_seconds,
    };
    let id = spec.id.clone();
    match prepare_command_session(&root, &binding, &lease, &spec) {
        Ok(session) => {
            command_session_registry().insert(Arc::clone(&session));
            match wait_for_yield(
                &session,
                yield_time_ms,
                optional_u64(args, "max_output_tokens"),
            ) {
                WaitOutcome::Completed(response) => Ok(strip_session_id(response)),
                WaitOutcome::Running(stdout) => {
                    Ok(command_result("running", None, &stdout, "", Some(id)))
                }
            }
        }
        Err(err) => {
            // prepare failed before the session owns the lease — release it here
            // (else the per-session lease leaks; sense-2 §12 guarantee).
            let _ = stack.release_lease(&lease.lease_id);
            Err(err)
        }
    }
}

fn prepare_isolated_command_session(
    spec: &CommandSessionStartSpec,
    handle: crate::isolated::CommandHandle,
) -> Result<Arc<CommandSession>, DaemonError> {
    let session_dir = handle.scratch_dir.join("command-sessions").join(&spec.id);
    std::fs::create_dir_all(&session_dir)?;
    let transcript_path = session_dir.join("transcript.log");
    let final_path = session_dir.join("final.json");
    let output_path = session_dir.join("runner-result.json");
    let request_path = session_dir.join("runner-request.json");
    std::fs::write(
        session_dir.join("metadata.json"),
        serde_json::to_vec_pretty(&json!({
            "command_session_id": spec.id,
            "agent_id": handle.agent_id,
            "invocation_id": spec.invocation_id,
            "workspace": "isolated",
            "workspace_handle_id": handle.workspace_handle_id,
            "command": spec.command,
            "status": "running",
        }))
        .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    let ns_fds = runner_ns_fds(&handle.ns_fds);
    let request = RunRequest {
        mode: runner_mode(ns_fds.as_ref()),
        tool_call: ToolCall {
            invocation_id: spec.invocation_id.clone(),
            agent_id: handle.agent_id.clone(),
            verb: "exec_command".into(),
            intent: Intent::WriteAllowed,
            args: json!({
                "command": spec.command,
                "cwd": ".",
            }),
            background: false,
        },
        workspace_root: WorkspaceRoot(handle.workspace_root.clone()),
        layer_paths: handle.layer_paths.clone(),
        upperdir: Some(handle.upperdir.clone()),
        workdir: Some(handle.workdir.clone()),
        ns_fds,
        cgroup_path: handle.cgroup_path.clone(),
        timeout_seconds: spec.timeout_seconds,
    };
    write_run_request(&request_path, &request)?;
    let workspace = CommandWorkspaceKind::Isolated(IsolatedCommandWorkspace {
        handle,
        output_path,
        final_path,
    });
    spawn_command_runner_session(spec, &request_path, transcript_path, workspace)
}

fn prepare_command_session(
    root: &Path,
    binding: &WorkspaceBinding,
    lease: &Lease,
    spec: &CommandSessionStartSpec,
) -> Result<Arc<CommandSession>, DaemonError> {
    let session_root = command_session_scratch_root();
    let mut dirs = overlay_run_dirs("sandbox-overlay", &spec.invocation_id)?;
    let mut run_dir_cleanup = RunDirCleanup::new(dirs.run_dir.clone());
    let session_dir = session_root.join(&spec.id);
    std::fs::create_dir_all(&session_dir)?;
    let transcript_path = session_dir.join("transcript.log");
    let final_path = session_dir.join("final.json");
    std::fs::write(
        session_dir.join("metadata.json"),
        serde_json::to_vec_pretty(&json!({
            "command_session_id": spec.id,
            "agent_id": spec.agent_id,
            "invocation_id": spec.invocation_id,
            "command": spec.command,
            "status": "running",
        }))
        .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    dirs.output_path = dirs.run_dir.join("command-runner-result.json");
    let request_path = dirs.run_dir.join("command-runner-request.json");
    dirs.request_path = Some(request_path.clone());
    dirs.final_path = final_path.clone();
    let request = RunRequest {
        mode: RunMode::FreshNs,
        tool_call: ToolCall {
            invocation_id: spec.invocation_id.clone(),
            agent_id: spec.agent_id.clone(),
            verb: "exec_command".into(),
            intent: Intent::WriteAllowed,
            args: json!({
                "command": spec.command,
                "cwd": ".",
            }),
            background: false,
        },
        workspace_root: WorkspaceRoot(PathBuf::from(&binding.workspace_root)),
        layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
        upperdir: Some(dirs.upperdir.clone()),
        workdir: Some(dirs.workdir.clone()),
        ns_fds: None,
        cgroup_path: None,
        timeout_seconds: spec.timeout_seconds,
    };
    write_run_request(&request_path, &request)?;
    let workspace = CommandWorkspaceKind::Ephemeral(EphemeralCommandWorkspace {
        root: root.to_path_buf(),
        lease_id: lease.lease_id.clone(),
        manifest: lease.manifest.clone(),
        manifest_version: lease.manifest_version,
        manifest_root_hash: lease.root_hash.clone(),
        layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
        workspace_root: PathBuf::from(&binding.workspace_root),
        dirs,
    });
    let session = spawn_command_runner_session(spec, &request_path, transcript_path, workspace);
    if session.is_ok() {
        run_dir_cleanup.disarm();
    }
    session
}

fn write_run_request(path: &Path, request: &RunRequest) -> Result<(), DaemonError> {
    std::fs::write(
        path,
        serde_json::to_vec(request).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    Ok(())
}

fn spawn_command_runner_session(
    spec: &CommandSessionStartSpec,
    request_path: &Path,
    transcript_path: PathBuf,
    workspace: CommandWorkspaceKind,
) -> Result<Arc<CommandSession>, DaemonError> {
    let (master, slave) = open_pty_pair()
        .map_err(|err| DaemonError::OverlayPipeline(format!("open pty pair: {err}")))?;
    let mut child_command = Command::new(std::env::current_exe()?);
    child_command
        .arg("ns-runner")
        .arg("--request")
        .arg(request_path)
        .arg("--output")
        .arg(workspace.output_path())
        .stdin(Stdio::from(slave.try_clone()?))
        .stdout(Stdio::from(slave.try_clone()?))
        .stderr(Stdio::from(slave))
        .process_group(0);
    let child = child_command.spawn()?;
    let pgid = i32::try_from(child.id()).map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("child pid does not fit i32: {}", child.id()),
        )
    })?;
    let output = Arc::new(CommandSessionOutput::new());
    let writer = master.try_clone()?;
    let reader_done = spawn_command_output_reader(master, Arc::clone(&output), transcript_path);
    let started_at = Instant::now();
    let timeout_deadline = spec
        .timeout_seconds
        .map(|seconds| started_at + Duration::from_secs_f64(seconds));
    let session = Arc::new(CommandSession {
        id: spec.id.clone(),
        agent_id: spec.agent_id.clone(),
        command: spec.command.clone(),
        started_at,
        pgid,
        writer: Mutex::new(writer),
        output: Arc::clone(&output),
        reader_done: Mutex::new(Some(reader_done)),
        cancelled: Mutex::new(false),
        interrupted: Mutex::new(false),
        model_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        notification_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        child: Mutex::new(Some(child)),
        workspace,
        finalized: Mutex::new(None),
        timeout_deadline,
    });
    Ok(session)
}

fn spawn_command_output_reader(
    mut master: File,
    output: Arc<CommandSessionOutput>,
    transcript_path: PathBuf,
) -> std_mpsc::Receiver<()> {
    let (done_tx, done_rx) = std_mpsc::channel();
    thread::spawn(move || {
        let mut transcript = OpenOptions::new()
            .create(true)
            .append(true)
            .open(transcript_path)
            .ok();
        let mut buf = [0_u8; 8192];
        // Carry-over buffer: holds an incomplete trailing multibyte sequence
        // until the next read completes it (§2.6).
        let mut carry: Vec<u8> = Vec::new();
        loop {
            match master.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    // Transcript: byte-exact raw stream (decode-independent).
                    if output.note_spooled(u64::try_from(n).unwrap_or(u64::MAX)) {
                        if let Some(file) = transcript.as_mut() {
                            let _ = file.write_all(&buf[..n]);
                        }
                    }
                    // Model output: decode the consumable prefix, retain only an
                    // incomplete trailing multibyte tail.
                    carry.extend_from_slice(&buf[..n]);
                    let consume = output::utf8_consumable_prefix_len(&carry);
                    if consume > 0 {
                        output.append(String::from_utf8_lossy(&carry[..consume]).into_owned());
                        carry.drain(..consume);
                    }
                }
                Err(err) if err.kind() == std::io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
        // EOF: flush any remaining (truly incomplete) bytes lossily.
        if !carry.is_empty() {
            output.append(String::from_utf8_lossy(&carry).into_owned());
        }
        let _ = done_tx.send(());
    });
    done_rx
}

pub(crate) fn command_session_scratch_root() -> PathBuf {
    command_session_config().scratch_root
}

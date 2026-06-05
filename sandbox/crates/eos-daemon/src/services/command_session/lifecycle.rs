//! Linux command-session build & spawn lifecycle.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use eos_command_session::{
    process::spawn_current_exe_ns_runner, CommandSessionOutput, CommandSessionOutputCursor,
    DynCommandWorkspacePolicy,
};
use serde_json::Value;

use eos_layerstack::require_workspace_binding;
use eos_workspace_api::{PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError};

use super::finalize::strip_session_id;
use super::policy::ephemeral::EphemeralCommandPolicy;
use super::policy::isolated::IsolatedCommandPolicy;
use super::session::{command_session_registry, wait_for_yield, CommandSession, WaitOutcome};
use super::{command_result, command_session_config, optional_u64, runtime_command_session_config};
use crate::error::DaemonError;

struct CommandSessionStartSpec {
    id: String,
    invocation_id: String,
    caller_id: String,
    command: String,
    timeout_seconds: Option<f64>,
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
    handle: crate::services::isolated_workspace::CommandHandle,
) -> Result<Value, DaemonError> {
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or("exec_command")
        .to_owned();
    let spec = CommandSessionStartSpec {
        id: command_session_registry().next_id(),
        invocation_id,
        caller_id: handle.caller_id.clone(),
        command: cmd.to_owned(),
        timeout_seconds,
    };
    let id = spec.id.clone();
    let session = prepare_isolated_command_session(&spec, handle)?;
    command_session_registry().insert(Arc::clone(&session));
    crate::services::isolated_workspace::register_command_session(&session.caller_id, &session.id);
    match wait_for_yield(
        &session,
        yield_time_ms,
        optional_u64(args, "max_output_tokens"),
    ) {
        WaitOutcome::Completed(response) => Ok(strip_session_id(response)),
        WaitOutcome::Running(stdout) => Ok(command_result("running", None, &stdout, "", Some(id))),
    }
}

fn prepare_request(spec: &CommandSessionStartSpec) -> PrepareCommandRequest {
    PrepareCommandRequest {
        caller_id: spec.caller_id.clone(),
        command_session_id: spec.id.clone(),
        invocation_id: spec.invocation_id.clone(),
        cmd: spec.command.clone(),
        timeout_seconds: spec.timeout_seconds,
    }
}

fn command_workspace_error(error: WorkspaceApiError) -> DaemonError {
    DaemonError::InvalidEnvelope(error.to_string())
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
    let caller_id = args
        .get("caller_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let binding = require_workspace_binding(&root)?;
    let spec = CommandSessionStartSpec {
        id: command_session_registry().next_id(),
        invocation_id,
        caller_id,
        command: cmd.to_owned(),
        timeout_seconds,
    };
    let id = spec.id.clone();
    match prepare_command_session(&root, PathBuf::from(&binding.workspace_root), &spec) {
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
        Err(err) => Err(err),
    }
}

fn prepare_isolated_command_session(
    spec: &CommandSessionStartSpec,
    handle: crate::services::isolated_workspace::CommandHandle,
) -> Result<Arc<CommandSession>, DaemonError> {
    prepare_policy_command_session(spec, Box::new(IsolatedCommandPolicy::new(handle)))
}

fn prepare_command_session(
    root: &Path,
    workspace_root: PathBuf,
    spec: &CommandSessionStartSpec,
) -> Result<Arc<CommandSession>, DaemonError> {
    prepare_policy_command_session(
        spec,
        Box::new(EphemeralCommandPolicy::new(
            root.to_path_buf(),
            workspace_root,
            command_session_scratch_root(),
        )),
    )
}

fn prepare_policy_command_session(
    spec: &CommandSessionStartSpec,
    policy: DynCommandWorkspacePolicy,
) -> Result<Arc<CommandSession>, DaemonError> {
    let prepared = policy
        .prepare_command_workspace(prepare_request(spec))
        .map_err(command_workspace_error)?;
    spawn_command_runner_session(spec, prepared, policy)
}

fn spawn_command_runner_session(
    spec: &CommandSessionStartSpec,
    prepared: PreparedCommandWorkspace,
    policy: DynCommandWorkspacePolicy,
) -> Result<Arc<CommandSession>, DaemonError> {
    let output = Arc::new(CommandSessionOutput::new(&runtime_command_session_config()));
    let process = spawn_current_exe_ns_runner(
        &prepared.request_path,
        &prepared.run_request,
        &prepared.output_path,
        prepared.transcript_path.clone(),
        Arc::clone(&output),
    )
    .map_err(|err| DaemonError::OverlayPipeline(format!("spawn command session process: {err}")))?;
    let started_at = Instant::now();
    let timeout_deadline = spec
        .timeout_seconds
        .map(|seconds| started_at + Duration::from_secs_f64(seconds));
    let session = Arc::new(CommandSession {
        id: spec.id.clone(),
        caller_id: spec.caller_id.clone(),
        command: spec.command.clone(),
        started_at,
        process,
        output: Arc::clone(&output),
        cancelled: Mutex::new(false),
        interrupted: Mutex::new(false),
        model_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        notification_cursor: Mutex::new(CommandSessionOutputCursor::default()),
        workspace_mode: prepared.mode,
        output_path: prepared.output_path,
        final_path: prepared.final_path,
        workspace_policy: Mutex::new(Some(policy)),
        finalize_context: prepared.finalize_context,
        finalized: Mutex::new(None),
        timeout_deadline,
    });
    Ok(session)
}

pub(crate) fn command_session_scratch_root() -> PathBuf {
    command_session_config().scratch_root
}

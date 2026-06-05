//! Linux command-session finalize, teardown, and stdin/cancel handling.

use std::io::Write;
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde_json::{json, Value};

use eos_ephemeral_workspace::command_session::types::EphemeralCommandSessionPort;
use eos_ephemeral_workspace::{
    finalize_publishable_workspace, EphemeralSnapshot, EphemeralWorkspace, EphemeralWorkspaceOps,
    FinalizeRequest, WorkspaceRoot as EphemeralWorkspaceRoot,
};
use eos_isolated_workspace::command_session::types::IsolatedCommandSessionPort;
use eos_isolated_workspace::IsolatedWorkspaceOps;
use eos_layerstack::LayerStack;
use eos_occ::{ChangesetResult, FileResult, OccStatus};
use eos_overlay::capture_upperdir;
use eos_runner::RunResult;
use eos_workspace_api::{
    ChangedPathKinds, CommandWorkspaceOps, FinalizeCommandRequest, PrepareCommandRequest,
    PreparedCommandWorkspace, WorkspaceApiError, WorkspaceCommandOutcome, WorkspaceConflict,
    WorkspaceMode, WorkspaceTimings,
};

use super::lifecycle::{require_string, EphemeralCommandWorkspace, IsolatedCommandWorkspace};
use super::session::{
    command_session_registry, lock_command_session_state, wait_for_yield, CommandSession,
    WaitOutcome,
};
use super::{command_result, command_session_config, command_session_not_found, optional_u64};
use crate::error::DaemonError;
use crate::overlay_runner::{
    changeset_from_publish_outcome, path_changes_to_wire, DaemonPublisherPort,
};
use crate::response_timings::{
    insert_tree_resource_timings, layer_change_kind, merge_runner_timings, resource_timings,
    TreeResourceStats,
};

fn command_workspace_error(error: WorkspaceApiError) -> DaemonError {
    DaemonError::InvalidEnvelope(error.to_string())
}

fn workspace_api_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("daemon_command_workspace_error", error.to_string())
}

fn timing_map(timings: serde_json::Map<String, Value>) -> WorkspaceTimings {
    timings.into_iter().collect()
}

fn finalize_request(
    session: &CommandSession,
    runner: Option<&RunResult>,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<FinalizeCommandRequest, DaemonError> {
    Ok(FinalizeCommandRequest {
        finalize_context: json!({}),
        runner_result: runner
            .map(serde_json::to_value)
            .transpose()
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
        status: status.to_owned(),
        exit_code: Some(exit_code),
        stdout: stdout.to_owned(),
        stderr: String::new(),
        command_session_id: include_session_id.then(|| session.id.clone()),
    })
}

fn write_final_response(path: &std::path::Path, response: &Value) -> Result<(), DaemonError> {
    std::fs::write(
        path,
        serde_json::to_vec_pretty(response)
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    Ok(())
}

fn command_outcome_response(outcome: WorkspaceCommandOutcome) -> Value {
    let mode = outcome.mode.as_str();
    let mut response = json!({
        "success": outcome.success,
        "workspace": mode,
        "workspace_mode": mode,
        "status": outcome.status,
        "exit_code": outcome.exit_code,
        "output": {
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
        },
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "conflict": outcome.conflict,
        "conflict_reason": outcome.conflict_reason,
        "changed_paths": outcome.changed_paths,
        "changed_path_kinds": outcome.changed_path_kinds,
        "mutation_source": outcome.mutation_source,
        "error": null,
        "timings": outcome.timings,
    });
    if let Some(command_session_id) = outcome.command_session_id {
        response["command_session_id"] = json!(command_session_id);
    }
    if let Some(metadata) = outcome.metadata.as_object() {
        for (key, value) in metadata {
            response[key] = value.clone();
        }
    }
    response
}

struct IsolatedCommandFinalizePort<'a> {
    session: &'a CommandSession,
    workspace: &'a IsolatedCommandWorkspace,
}

impl IsolatedCommandSessionPort for IsolatedCommandFinalizePort<'_> {
    fn prepare_isolated_command_workspace(
        &self,
        _request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
        Err(WorkspaceApiError::new(
            "unsupported_command_workspace_adapter",
            "isolated finalize adapter cannot prepare command workspaces",
        ))
    }

    fn finalize_isolated_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        let total_s = self.session.started_at.elapsed().as_secs_f64();
        let capture_start = Instant::now();
        let changes = capture_upperdir(&self.workspace.handle.upperdir)
            .map_err(|err| workspace_api_error(format!("capture isolated upperdir: {err}")))?;
        let capture_s = capture_start.elapsed().as_secs_f64();
        let path_kinds: Vec<(String, String)> = changes
            .iter()
            .map(|change| {
                (
                    change.path().as_str().to_owned(),
                    layer_change_kind(change).to_owned(),
                )
            })
            .collect();
        let manifest = LayerStack::open(self.workspace.handle.layer_stack_root.clone())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        let runner = request
            .runner_result
            .as_ref()
            .map(|value| serde_json::from_value::<RunResult>(value.clone()))
            .transpose()
            .map_err(workspace_api_error)?;
        let mut timings = resource_timings(&manifest, path_kinds.len());
        if let Some(runner) = &runner {
            merge_runner_timings(&mut timings, runner);
        }
        timings.insert(
            "command_exec.capture_upperdir_s".to_owned(),
            json!(capture_s),
        );
        timings.insert("command_exec.occ_apply_s".to_owned(), json!(0.0));
        timings.insert("command_exec.total_s".to_owned(), json!(total_s));
        timings.insert("api.exec_command.total_s".to_owned(), json!(total_s));
        timings.insert(
            "api.exec_command.dispatch_total_s".to_owned(),
            json!(total_s),
        );
        let changed_paths: Vec<String> = path_kinds.iter().map(|(path, _)| path.clone()).collect();
        let changed_path_kinds = path_kinds.into_iter().collect::<ChangedPathKinds>();
        let exit_code = request.exit_code.unwrap_or(1);
        let duration_s = self.session.started_at.elapsed().as_secs_f64();
        let duration_ms = duration_s * 1000.0;
        Ok(WorkspaceCommandOutcome {
            mode: WorkspaceMode::Isolated,
            success: true,
            status: request.status,
            exit_code: Some(exit_code),
            stdout: request.stdout,
            stderr: request.stderr,
            command_session_id: request.command_session_id,
            changed_paths,
            changed_path_kinds,
            mutation_source: "isolated_workspace".to_owned(),
            conflict: None,
            conflict_reason: None,
            timings: timing_map(timings),
            metadata: json!({
                "isolated_workspace": {
                    "agent_id": self.workspace.handle.agent_id.clone(),
                    "workspace_handle_id": self.workspace.handle.workspace_handle_id.clone(),
                    "manifest_version": self.workspace.handle.manifest_version,
                    "manifest_root_hash": self.workspace.handle.manifest_root_hash.clone(),
                    "published": false,
                },
                "warnings": [],
                "spool_truncated": self.session.output.spool_truncated(),
                "audit": {
                    "workspace_handle_id": self.workspace.handle.workspace_handle_id.clone(),
                    "exit_code": exit_code,
                    "argv0": "bash",
                    "status": request.status,
                    "published": false,
                    "command_session_id": self.session.id.clone(),
                    "duration_s": duration_s,
                    "total_ms": duration_ms,
                    "phases_ms": {
                        "exec": duration_ms,
                    },
                },
            }),
        })
    }
}

struct EphemeralCommandFinalizePort<'a> {
    session: &'a CommandSession,
    workspace: &'a EphemeralCommandWorkspace,
}

impl EphemeralCommandSessionPort for EphemeralCommandFinalizePort<'_> {
    fn prepare_ephemeral_command_workspace(
        &self,
        _request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
        Err(WorkspaceApiError::new(
            "unsupported_command_workspace_adapter",
            "ephemeral finalize adapter cannot prepare command workspaces",
        ))
    }

    fn finalize_ephemeral_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        let total_s = self.session.started_at.elapsed().as_secs_f64();
        let finalize = finalize_publishable_workspace(
            &DaemonPublisherPort::new(&self.workspace.root, &self.workspace.manifest),
            FinalizeRequest {
                workspace: EphemeralWorkspace {
                    layer_stack_root: EphemeralWorkspaceRoot(self.workspace.root.clone()),
                    workspace_root: self.workspace.workspace_root.clone(),
                    agent_id: eos_ephemeral_workspace::AgentId(self.session.agent_id.clone()),
                    invocation_id: eos_ephemeral_workspace::InvocationId(self.session.id.clone()),
                    snapshot: EphemeralSnapshot {
                        lease_id: self.workspace.lease_id.clone(),
                        manifest_version: self.workspace.manifest_version,
                        manifest_root_hash: self.workspace.manifest_root_hash.clone(),
                        layer_paths: self.workspace.layer_paths.clone(),
                    },
                    dirs: self.workspace.dirs.clone(),
                },
                command_started_at: Some(self.session.started_at),
            },
        )
        .map_err(workspace_api_error)?;
        let changeset = changeset_from_publish_outcome(&finalize.publish)
            .map_err(|err| workspace_api_error(err.to_string()))?;
        let upperdir_stats = TreeResourceStats::from_ephemeral(&finalize.capture.stats);
        let capture_s = finalize.capture.capture_s;
        let path_kinds = path_changes_to_wire(&finalize.capture.path_kinds);
        let occ_s = changeset
            .timings
            .get("occ.commit.total_s")
            .copied()
            .or(finalize.timings.publish_s)
            .unwrap_or_default();
        let manifest = LayerStack::open(self.workspace.root.clone())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        let mut timings = resource_timings(&manifest, path_kinds.len());
        insert_tree_resource_timings(
            &mut timings,
            "resource.command_exec.upperdir",
            &upperdir_stats,
        );
        for (key, value) in &changeset.timings {
            timings.insert(key.clone(), json!(value));
        }
        timings.insert(
            "command_exec.capture_upperdir_s".to_owned(),
            json!(capture_s),
        );
        timings.insert("command_exec.occ_apply_s".to_owned(), json!(occ_s));
        timings.insert("command_exec.total_s".to_owned(), json!(total_s));
        timings.insert(
            "api.exec_command.dispatch_total_s".to_owned(),
            json!(total_s),
        );
        let changed_path_kinds = path_kinds.into_iter().collect::<ChangedPathKinds>();
        Ok(WorkspaceCommandOutcome {
            mode: WorkspaceMode::Ephemeral,
            success: changeset.success(),
            status: request.status,
            exit_code: request.exit_code,
            stdout: request.stdout,
            stderr: request.stderr,
            command_session_id: request.command_session_id,
            changed_paths: published_paths(&changeset),
            changed_path_kinds,
            mutation_source: "overlay_capture".to_owned(),
            conflict: first_conflict(&changeset).map(conflict_from_file),
            conflict_reason: first_conflict(&changeset)
                .map(|file| conflict_message(file, occ_status_wire(file.status)).to_owned()),
            timings: timing_map(timings),
            metadata: json!({
                "spool_truncated": self.session.output.spool_truncated(),
            }),
        })
    }
}

fn published_paths(result: &ChangesetResult) -> Vec<String> {
    result
        .files
        .iter()
        .filter(|file| file.status.is_published())
        .map(|file| file.path.as_str().to_owned())
        .collect()
}

fn first_conflict(result: &ChangesetResult) -> Option<&FileResult> {
    result.files.iter().find(|file| !file.status.is_success())
}

fn conflict_from_file(file: &FileResult) -> WorkspaceConflict {
    let reason = occ_status_wire(file.status);
    WorkspaceConflict::path(reason, file.path.as_str(), conflict_message(file, reason))
}

fn conflict_message<'a>(file: &'a FileResult, fallback: &'a str) -> &'a str {
    if file.message.is_empty() {
        fallback
    } else {
        file.message.as_str()
    }
}

const fn occ_status_wire(status: OccStatus) -> &'static str {
    match status {
        OccStatus::Accepted => "accepted",
        OccStatus::Committed => "committed",
        OccStatus::AbortedVersion => "aborted_version",
        OccStatus::AbortedOverlap => "aborted_overlap",
        OccStatus::Dropped => "dropped",
        OccStatus::Rejected => "rejected",
        _ => "failed",
    }
}

pub(super) fn finalize_isolated_command_workspace(
    session: &CommandSession,
    workspace: &IsolatedCommandWorkspace,
    runner: Option<&RunResult>,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<Value, DaemonError> {
    let mut outcome = IsolatedWorkspaceOps::new(IsolatedCommandFinalizePort { session, workspace })
        .finalize_command_workspace(finalize_request(
            session,
            runner,
            status,
            exit_code,
            stdout,
            include_session_id,
        )?)
        .map_err(command_workspace_error)?;
    let audit = outcome
        .metadata
        .get("audit")
        .cloned()
        .unwrap_or_else(|| json!({}));
    if let Some(metadata) = outcome.metadata.as_object_mut() {
        metadata.remove("audit");
    }
    let response = command_outcome_response(outcome);
    write_final_response(&workspace.final_path, &response)?;
    crate::isolated::record_tool_call(
        &workspace.handle.agent_id,
        merge_audit_changed_paths(audit, response["changed_paths"].clone()),
    );
    Ok(response)
}

pub(super) fn finalize_command_workspace(
    session: &CommandSession,
    workspace: &EphemeralCommandWorkspace,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<Value, DaemonError> {
    let outcome = EphemeralWorkspaceOps::new(EphemeralCommandFinalizePort { session, workspace })
        .finalize_command_workspace(finalize_request(
            session,
            None,
            status,
            exit_code,
            stdout,
            include_session_id,
        )?)
        .map_err(command_workspace_error)?;
    let response = command_outcome_response(outcome);
    write_final_response(&workspace.dirs.final_path, &response)?;
    Ok(response)
}

fn merge_audit_changed_paths(mut audit: Value, changed_paths: Value) -> Value {
    if let Some(object) = audit.as_object_mut() {
        object.insert("changed_paths".to_owned(), changed_paths);
    }
    audit
}

pub(crate) fn strip_session_id(mut response: Value) -> Value {
    if let Some(object) = response.as_object_mut() {
        object.remove("command_session_id");
    }
    response
}

pub(crate) fn response_with_stdout(mut response: Value, stdout: String) -> Value {
    response["output"]["stdout"] = json!(stdout);
    response["stdout"] = response["output"]["stdout"].clone();
    response
}

pub(crate) fn terminate_command_process_group(pgid: i32) {
    if killpg(Pid::from_raw(pgid), Signal::SIGTERM).is_ok() {
        thread::sleep(Duration::from_millis(50));
        let _ = killpg(Pid::from_raw(pgid), Signal::SIGKILL);
    }
}

pub(crate) fn command_session_write_stdin(args: &Value) -> Result<Value, DaemonError> {
    let id = require_string(args, "command_session_id")?;
    let chars = args
        .get("chars")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let yield_time_ms = optional_u64(args, "yield_time_ms")
        .unwrap_or(command_session_config().default_yield_time_ms);
    let max_tokens = optional_u64(args, "max_output_tokens");
    // sense-2 D7: `terminate` is the explicit teardown channel, decoupled from
    // `\x03` (which is SIGINT/interrupt only).
    let terminate = args
        .get("terminate")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let registry = command_session_registry();
    let Some(session) = registry.get(&id) else {
        // The live session is gone; a reaper-parked completion may remain.
        if let Some(result) = registry.take_completed_result(&id) {
            return Ok(result);
        }
        return Ok(command_session_not_found());
    };
    {
        let mut writer = lock_command_session_state(&session.writer);
        writer.write_all(chars.as_bytes())?;
    }
    // `\x03` interrupts the foreground program (SIGINT) only — teardown is a
    // separate concern (sense-2 D7).
    if chars.contains('\u{3}') {
        *lock_command_session_state(&session.interrupted) = true;
        let _ = killpg(Pid::from_raw(session.pgid), Signal::SIGINT);
    }
    // `terminate: true` tears the session down (SIGTERM→SIGKILL); `wait_for_yield`
    // then finalizes it inline with a `cancelled` status.
    if terminate {
        *lock_command_session_state(&session.cancelled) = true;
        terminate_command_process_group(session.pgid);
    }
    // Unified wait: early-return on completion (inline finalize) or
    // quiet-after-output, capped at `yield_time_ms` (sense-2 §2.3).
    match wait_for_yield(&session, yield_time_ms, max_tokens) {
        WaitOutcome::Completed(result) => Ok(result),
        WaitOutcome::Running(stdout) => Ok(command_result("running", None, &stdout, "", Some(id))),
    }
}

pub(crate) fn command_session_cancel(args: &Value) -> Result<Value, DaemonError> {
    let id = require_string(args, "command_session_id")?;
    let registry = command_session_registry();
    let Some(session) = registry.get(&id) else {
        if let Some(result) = registry.take_completed_result(&id) {
            return Ok(result);
        }
        return Ok(command_session_not_found());
    };
    *lock_command_session_state(&session.cancelled) = true;
    terminate_command_process_group(session.pgid);
    // Finalize inline so the lease/scratch is reclaimed and the cancelled status
    // is stamped; if the child is somehow still alive, the reaper finalizes it.
    match wait_for_yield(
        &session,
        command_session_config().cancel_wait_ms,
        optional_u64(args, "max_output_tokens"),
    ) {
        WaitOutcome::Completed(result) => Ok(result),
        WaitOutcome::Running(stdout) => Ok(command_result("cancelled", None, &stdout, "", None)),
    }
}

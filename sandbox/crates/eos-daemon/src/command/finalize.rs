//! Linux command-session finalize, teardown, and stdin/cancel handling.

use std::io::Write;
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde_json::{json, Value};

use eos_ephemeral_workspace::{
    finalize_publishable_workspace, EphemeralSnapshot, EphemeralWorkspace, FinalizeRequest,
    WorkspaceRoot as EphemeralWorkspaceRoot,
};
use eos_layerstack::LayerStack;
use eos_overlay::capture_upperdir;
use eos_runner::RunResult;

use super::lifecycle::{require_string, EphemeralCommandWorkspace, IsolatedCommandWorkspace};
use super::session::{
    command_session_registry, lock_command_session_state, wait_for_yield, CommandSession,
    WaitOutcome,
};
use super::{command_result, command_session_config, command_session_not_found, optional_u64};
use crate::error::DaemonError;
use crate::overlay_runner::{
    changeset_from_publish_outcome, ephemeral_daemon_error, overlay_daemon_error,
    path_changes_to_wire, DaemonPublisherPort,
};
use crate::response_timings::{
    guarded_changeset_response, insert_tree_resource_timings, layer_change_kind,
    merge_runner_timings, resource_timings, TreeResourceStats,
};

pub(super) fn finalize_isolated_command_workspace(
    session: &CommandSession,
    workspace: &IsolatedCommandWorkspace,
    runner: Option<&RunResult>,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<Value, DaemonError> {
    let total_s = session.started_at.elapsed().as_secs_f64();
    let capture_start = Instant::now();
    let changes = capture_upperdir(&workspace.handle.upperdir)
        .map_err(|err| overlay_daemon_error("capture isolated upperdir", &err))?;
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
    let manifest =
        LayerStack::open(workspace.handle.layer_stack_root.clone())?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, path_kinds.len());
    if let Some(runner) = runner {
        merge_runner_timings(&mut timings, runner);
    }
    timings.insert(
        "command_exec.capture_upperdir_s".to_owned(),
        json!(capture_s),
    );
    timings.insert("command_exec.occ_apply_s".to_owned(), json!(0.0));
    timings.insert("command_exec.total_s".to_owned(), json!(total_s));
    timings.insert(
        "api.exec_command.dispatch_total_s".to_owned(),
        json!(total_s),
    );
    let changed_paths: Vec<String> = path_kinds.iter().map(|(path, _)| path.clone()).collect();
    let changed_path_kinds = Value::Object(
        path_kinds
            .into_iter()
            .map(|(path, kind)| (path, json!(kind)))
            .collect(),
    );
    let mut response = json!({
        "success": true,
        "workspace": "isolated",
        "workspace_mode": "isolated",
        "status": status,
        "exit_code": exit_code,
        "output": {
            "stdout": stdout,
            "stderr": "",
        },
        "stdout": stdout,
        "stderr": "",
        "conflict": null,
        "conflict_reason": null,
        "changed_paths": changed_paths,
        "changed_path_kinds": changed_path_kinds,
        "mutation_source": "isolated_workspace",
        "isolated_workspace": {
            "agent_id": workspace.handle.agent_id.clone(),
            "workspace_handle_id": workspace.handle.workspace_handle_id.clone(),
            "manifest_version": workspace.handle.manifest_version,
            "manifest_root_hash": workspace.handle.manifest_root_hash.clone(),
            "published": false,
        },
        "timings": Value::Object(timings),
        "warnings": [],
        "spool_truncated": session.output.spool_truncated(),
    });
    if include_session_id {
        response["command_session_id"] = json!(session.id.clone());
    }
    std::fs::write(
        &workspace.final_path,
        serde_json::to_vec_pretty(&response)
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    let duration_s = session.started_at.elapsed().as_secs_f64();
    let duration_ms = duration_s * 1000.0;
    crate::isolated::record_tool_call(
        &workspace.handle.agent_id,
        json!({
            "workspace_handle_id": workspace.handle.workspace_handle_id.clone(),
            "exit_code": exit_code,
            "argv0": "bash",
            "status": status,
            "changed_paths": response["changed_paths"].clone(),
            "published": false,
            "command_session_id": session.id.clone(),
            "duration_s": duration_s,
            "total_ms": duration_ms,
            "phases_ms": {
                "exec": duration_ms,
            },
        }),
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
    let total_s = session.started_at.elapsed().as_secs_f64();
    let finalize = finalize_publishable_workspace(
        &DaemonPublisherPort::new(&workspace.root, &workspace.manifest),
        FinalizeRequest {
            workspace: EphemeralWorkspace {
                layer_stack_root: EphemeralWorkspaceRoot(workspace.root.clone()),
                workspace_root: workspace.workspace_root.clone(),
                agent_id: eos_ephemeral_workspace::AgentId(session.agent_id.clone()),
                invocation_id: eos_ephemeral_workspace::InvocationId(session.id.clone()),
                snapshot: EphemeralSnapshot {
                    lease_id: workspace.lease_id.clone(),
                    manifest_version: workspace.manifest_version,
                    manifest_root_hash: workspace.manifest_root_hash.clone(),
                    layer_paths: workspace.layer_paths.clone(),
                },
                dirs: workspace.dirs.clone(),
            },
            command_started_at: Some(session.started_at),
        },
    )
    .map_err(ephemeral_daemon_error)?;
    let changeset = changeset_from_publish_outcome(&finalize.publish)?;
    let upperdir_stats = TreeResourceStats::from_ephemeral(&finalize.capture.stats);
    let capture_s = finalize.capture.capture_s;
    let path_kinds = path_changes_to_wire(&finalize.capture.path_kinds);
    let occ_s = changeset
        .timings
        .get("occ.commit.total_s")
        .copied()
        .or(finalize.timings.publish_s)
        .unwrap_or_default();
    let manifest = LayerStack::open(workspace.root.clone())?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, path_kinds.len());
    insert_tree_resource_timings(
        &mut timings,
        "resource.command_exec.upperdir",
        &upperdir_stats,
    );
    let mut response =
        guarded_changeset_response("exec_command", &changeset, timings, Instant::now(), None);
    response["status"] = json!(status);
    response["exit_code"] = json!(exit_code);
    response["output"] = json!({"stdout": stdout, "stderr": ""});
    response["stdout"] = response["output"]["stdout"].clone();
    response["stderr"] = json!("");
    response["changed_path_kinds"] = Value::Object(
        path_kinds
            .into_iter()
            .map(|(path, kind)| (path, json!(kind)))
            .collect(),
    );
    response["timings"]["command_exec.capture_upperdir_s"] = json!(capture_s);
    response["timings"]["command_exec.occ_apply_s"] = json!(occ_s);
    response["timings"]["command_exec.total_s"] = json!(total_s);
    response["timings"]["api.exec_command.dispatch_total_s"] = json!(total_s);
    response["spool_truncated"] = json!(session.output.spool_truncated());
    if include_session_id {
        response["command_session_id"] = json!(session.id);
    }
    std::fs::write(
        &workspace.dirs.final_path,
        serde_json::to_vec_pretty(&response)
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?,
    )?;
    Ok(response)
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

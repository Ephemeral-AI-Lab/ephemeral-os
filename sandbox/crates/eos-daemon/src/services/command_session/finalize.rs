//! Linux command-session finalize, teardown, and stdin/cancel handling.

use serde_json::{json, Value};

use eos_command_session::process::CommandRunnerResult;
use eos_workspace_api::{FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome};

use super::lifecycle::require_string;
use super::session::{
    command_session_registry, lock_command_session_state, wait_for_yield, CommandSession,
    WaitOutcome,
};
use super::{command_result, command_session_config, command_session_not_found, optional_u64};
use crate::error::DaemonError;

fn command_workspace_error(error: WorkspaceApiError) -> DaemonError {
    DaemonError::InvalidEnvelope(error.to_string())
}

fn finalize_request(
    session: &CommandSession,
    runner: Option<&CommandRunnerResult>,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<FinalizeCommandRequest, DaemonError> {
    Ok(FinalizeCommandRequest {
        finalize_context: session.finalize_context.clone(),
        runner_result: runner.map(|runner| runner.value().clone()),
        command_elapsed_s: session.started_at.elapsed().as_secs_f64(),
        spool_truncated: session.output.spool_truncated(),
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

pub(super) fn finalize_command_session_policy(
    session: &CommandSession,
    runner: Option<&CommandRunnerResult>,
    status: &str,
    exit_code: i64,
    stdout: &str,
    include_session_id: bool,
) -> Result<Value, DaemonError> {
    let policy = lock_command_session_state(&session.workspace_policy);
    let policy = policy.as_ref().ok_or_else(|| {
        DaemonError::InvalidEnvelope("command session has no workspace policy".to_owned())
    })?;
    let outcome = policy
        .finalize_command_workspace(finalize_request(
            session,
            runner,
            status,
            exit_code,
            stdout,
            include_session_id,
        )?)
        .map_err(command_workspace_error)?;
    let response = command_outcome_response(outcome);
    write_final_response(&session.final_path, &response)?;
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
    session.process.write_stdin(chars.as_bytes())?;
    // `\x03` interrupts the foreground program (SIGINT) only — teardown is a
    // separate concern (sense-2 D7).
    if chars.contains('\u{3}') {
        *lock_command_session_state(&session.interrupted) = true;
        session.process.interrupt();
    }
    // `terminate: true` tears the session down (SIGTERM→SIGKILL); `wait_for_yield`
    // then finalizes it inline with a `cancelled` status.
    if terminate {
        *lock_command_session_state(&session.cancelled) = true;
        session.process.terminate();
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
    session.process.terminate();
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

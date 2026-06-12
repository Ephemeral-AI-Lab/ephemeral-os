//! Isolated-workspace op adapters behind `sandbox.isolation.*`: wire arg
//! parsing and response/error shaping over [`crate::WorkspaceRuntime`].

#[cfg(test)]
use std::sync::{Mutex, MutexGuard, OnceLock, PoisonError};

use eos_operation::isolation::contract::{
    IsolationEnterInput, IsolationEnterOutput, IsolationExitInput, IsolationExitOutput,
    IsolationStatusInput, IsolationStatusOutput, ListOpenOutput, TestResetOutput,
};
use eos_operation::{OpError, OpResponse};
use eos_workspace::{IsolatedError, WorkspaceHandle};
use serde_json::{json, Value};

use crate::error::DaemonError;
use crate::DispatchContext;
use crate::{ExitOutcome, WorkspaceEnterError};

use super::to_wire_value;

const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";

pub(crate) fn op_enter(
    input: IsolationEnterInput,
    context: DispatchContext<'_>,
) -> Result<OpResponse, DaemonError> {
    let caller_id = input.caller.to_string();
    let root = input.layer_stack_root;
    let workspace = &context.require_services()?.workspace;
    match workspace.enter(&caller_id, &root) {
        Ok(handle) => Ok(success_response(to_wire_value(IsolationEnterOutput {
            success: true,
            manifest_version: handle.manifest_version,
            manifest_root_hash: handle.manifest_root_hash,
            workspace_handle_id: handle.workspace_id.0,
            workspace_root: handle.workspace_root,
        }))),
        Err(WorkspaceEnterError::ActiveCommands { active_commands }) => Ok(refused_response(
            "active_background_work",
            "cannot enter isolated workspace while commands are active",
            json!({"active_commands": active_commands}),
        )),
        Err(WorkspaceEnterError::Isolated(error)) => Ok(error_payload(&error)),
    }
}

pub(crate) fn op_exit(
    input: IsolationExitInput,
    context: DispatchContext<'_>,
) -> Result<OpResponse, DaemonError> {
    let caller_id = input.caller.to_string();
    let workspace = &context.require_services()?.workspace;
    // Exit is the per-caller workspace-run teardown: discard the caller's
    // isolated commands, then tear down its namespace + lease. The
    // isolated exit result carries this op's response shape.
    workspace
        .cancel_runs_for_caller(&caller_id, input.grace_s)
        .isolated
        .map_or_else(
            |error| Ok(error_payload(&error)),
            |exit| Ok(success_response(exit_response(exit))),
        )
}

pub(crate) fn op_status(
    input: IsolationStatusInput,
    context: DispatchContext<'_>,
) -> Result<OpResponse, DaemonError> {
    let caller_id = input.caller.to_string();
    let workspace = &context.require_services()?.workspace;
    match workspace.status(&caller_id) {
        Ok(Some(handle)) => Ok(success_response(status_response(&handle))),
        Ok(None) => Ok(success_response(to_wire_value(
            IsolationStatusOutput::Closed {
                success: true,
                open: false,
            },
        ))),
        Err(error) => Ok(error_payload(&error)),
    }
}

pub(crate) fn op_list_open(context: DispatchContext<'_>) -> Result<OpResponse, DaemonError> {
    let workspace = &context.require_services()?.workspace;
    Ok(success_response(to_wire_value(ListOpenOutput {
        success: true,
        open_caller_ids: workspace.list_open(),
    })))
}

pub(crate) fn op_test_reset(context: DispatchContext<'_>) -> Result<OpResponse, DaemonError> {
    if !env_true(TEST_HARNESS_ENV) {
        return Ok(refused_response(
            "forbidden",
            "sandbox.isolation.test_reset requires EOS_ISOLATED_WORKSPACE_TEST_HARNESS=true",
            json!({}),
        ));
    }
    let workspace = &context.require_services()?.workspace;
    let exited_callers = workspace.test_reset();
    Ok(success_response(to_wire_value(TestResetOutput {
        success: true,
        reset: true,
        exited_callers,
    })))
}

fn status_response(handle: &WorkspaceHandle) -> Value {
    to_wire_value(IsolationStatusOutput::Open {
        success: true,
        open: true,
        manifest_version: handle.manifest_version,
        manifest_root_hash: handle.manifest_root_hash.clone(),
        workspace_root: handle.workspace_root.clone(),
        created_at: handle.created_at,
        last_activity: handle.last_activity,
    })
}

/// Shape the stable exit response, splicing the lease custody fields into the
/// teardown inspection.
pub(crate) fn exit_response(exit: ExitOutcome) -> Value {
    let outcome = exit.isolated;
    let mut inspection = outcome.inspection;
    if let Some(object) = inspection.as_object_mut() {
        object.insert("lease_released".to_owned(), json!(exit.lease_released));
        object.insert(
            "active_leases_after".to_owned(),
            json!(exit.active_leases_after),
        );
    }
    to_wire_value(IsolationExitOutput {
        success: true,
        evicted_upperdir_bytes: outcome.evicted_upperdir_bytes,
        lifetime_s: outcome.lifetime_s,
        total_ms: outcome.total_ms,
        phases_ms: to_wire_value(outcome.phases_ms),
        inspection,
    })
}

/// Serialize tests that toggle the process-wide
/// `EOS_ISOLATED_WORKSPACE_TEST_HARNESS` environment variable.
#[cfg(test)]
pub(crate) fn lock_isolated_test_state() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
}

/// Map an [`IsolatedError`] onto the structured error payload, carrying the
/// variant-specific detail fields.
fn success_response(value: Value) -> OpResponse {
    OpResponse::Success(value)
}

fn refused_response(kind: &'static str, message: impl Into<String>, details: Value) -> OpResponse {
    OpResponse::Refused(OpError {
        kind,
        message: message.into(),
        details: Some(details),
    })
}

fn error_payload(error: &IsolatedError) -> OpResponse {
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
        IsolatedError::SetupFailed { step } => json!({
            "failed_step": step,
        }),
        _ => json!({}),
    };
    refused_response(error.kind(), error.to_string(), details)
}

fn env_true(key: &str) -> bool {
    std::env::var(key)
        .unwrap_or_default()
        .trim()
        .eq_ignore_ascii_case("true")
}

#[cfg(test)]
#[path = "../../tests/unit/isolated_workspace/service.rs"]
mod tests;

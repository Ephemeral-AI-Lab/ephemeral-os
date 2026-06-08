//! `RequireNoBackgroundSessions` is the agent-facing isolated/terminal guard for
//! every background session family: outstanding delegated workflows, in-flight
//! subagents, and daemon-visible command sessions.
//!
//! Terminal tools and `exit_isolated_workspace` settle in-process subagent
//! records before checking the remaining session families. `enter_isolated_workspace`
//! remains inspect-only. Workflows stay owned by persisted workflow state via
//! `WorkflowApi::find_outstanding_workflows`, and command sessions stay
//! owned by the sandbox daemon via `api.v1.command_session_count`.

use eos_types::JsonObject;
use serde_json::{json, Value};

use eos_tool_ports::{ExecutionMetadata, HookServices, ToolError, ToolName};

use super::{deferred_goal, HookDenial, HookOutcome};

/// Whether this protected tool **cancels** the agent's in-flight subagents (the
/// four terminals + `exit_isolated_workspace`) vs only inspects them
/// (`enter_isolated_workspace`, which keeps reject semantics).
fn cancels_inflight_subagents(tool: ToolName) -> bool {
    matches!(
        tool,
        ToolName::SubmitRootOutcome
            | ToolName::SubmitGeneratorOutcome
            | ToolName::SubmitReducerOutcome
            | ToolName::SubmitPlannerOutcome
            | ToolName::ExitIsolatedWorkspace
    )
}

/// Whether this submission is a "bailout" that fails-open on a daemon error
/// (Rust `_is_bailout_submission`).
fn is_bailout_submission(tool: ToolName, raw_input: &JsonObject) -> bool {
    match tool {
        ToolName::SubmitPlannerOutcome => deferred_goal(raw_input).is_some(),
        ToolName::SubmitGeneratorOutcome | ToolName::SubmitReducerOutcome => raw_input
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|s| s == "failed"),
        _ => false,
    }
}

fn subagent_in_flight_message(count: usize, tool: ToolName) -> String {
    format!(
        "BLOCKED: {count} subagent background task(s) are still in flight for this agent run. \
         Wait for them to finish or cancel them before calling {}, then retry.",
        tool.as_str()
    )
}

fn command_session_in_flight_message(count: usize, tool: ToolName) -> String {
    format!(
        "BLOCKED: {count} command session background task(s) are still in flight for this agent run. \
         Finish or interrupt active command sessions before calling {}, then retry.",
        tool.as_str()
    )
}

/// `RequireNoBackgroundSessions`: cancel subagents (or, for `enter_isolated_workspace`,
/// inspect) the agent run's in-flight subagents, deny on outstanding workflows, then
/// deny on daemon command sessions (fail-OPEN only for bailout submissions on a
/// daemon error). Invariant: no workflows, no subagents, and no command sessions.
pub(crate) async fn run_require_no_background_sessions(
    tool: ToolName,
    raw_input: &JsonObject,
    ctx: &ExecutionMetadata,
    services: &HookServices,
) -> Result<HookOutcome, ToolError> {
    let agent_run_id = ctx.require_agent_run_id()?;

    if let Some(subagent_sessions) = services.subagent_sessions() {
        // Terminal/exit tools settle the agent's subagents to 0; enter_isolated
        // only inspects (reject). After cancellation `report.subagent == 0`, so
        // the deny below fires only on the reject path.
        let subagents = if cancels_inflight_subagents(tool) {
            subagent_sessions
                .cancel_all_background_sessions("parent submitted its terminal")
                .await?;
            subagent_sessions.count_background_sessions().await?
        } else {
            subagent_sessions.count_background_sessions().await?
        };
        if subagents > 0 {
            return Ok(HookOutcome::Deny(
                HookDenial::new(
                    subagent_in_flight_message(subagents, tool),
                    "no_background_sessions",
                )
                .with_reason("ephemeral_jobs_in_flight")
                .with_count(subagents),
            ));
        }
    }

    // Workflow dimension: the background tracks workflow sessions, but persisted
    // workflow lifecycle remains authoritative here. Deny while a delegated
    // workflow is still open.
    if let (Some(service), Some(task_id)) = (services.workflow_service(), &ctx.task_id) {
        let outstanding = service
            .find_outstanding_workflows(task_id, agent_run_id)
            .await?;
        if !outstanding.is_empty() {
            return Ok(HookOutcome::Deny(
                HookDenial::new(
                    format!(
                        "BLOCKED: {} delegated workflow(s) are still outstanding for this agent run. \
                         Use check_workflow_status to collect them or cancel_workflow to stop them \
                         before calling {}, then retry.",
                        outstanding.len(),
                        tool.as_str()
                    ),
                    "no_background_sessions",
                )
                .with_reason("ephemeral_jobs_in_flight")
                .with_count(outstanding.len()),
            ));
        }
    }

    let sandbox_id = match &ctx.sandbox_id {
        Some(id) => id,
        None => return Ok(HookOutcome::pass()),
    };

    let Some(transport) = services.sandbox_transport() else {
        return Ok(HookOutcome::Deny(
            HookDenial::new(
                format!(
                    "BLOCKED: could not confirm background-task state from the sandbox daemon, \
                     so {} is refused to avoid orphaning in-flight work. Retry shortly.",
                    tool.as_str()
                ),
                "no_background_sessions",
            )
            .with_reason("command_session_count_unavailable"),
        ));
    };

    let daemon = match eos_sandbox_port::command_session_count(
        &**transport,
        sandbox_id,
        agent_run_id.as_str(),
    )
    .await
    {
        Ok(count) => count as usize,
        Err(_) => {
            if is_bailout_submission(tool, raw_input) {
                // Fail-OPEN: stamp the override reason in the pass-phase
                // metadata so the audit trail distinguishes a bailout from a
                // normal pass (Rust `daemon_unavailable_bailout`).
                let mut meta = JsonObject::new();
                meta.insert("policy".to_owned(), json!("no_background_sessions"));
                meta.insert("reason".to_owned(), json!("daemon_unavailable_bailout"));
                return Ok(HookOutcome::Pass(meta));
            }
            return Ok(HookOutcome::Deny(
                    HookDenial::new(
                        format!(
                            "BLOCKED: could not confirm background-task state from the sandbox daemon, \
                             so {} is refused to avoid orphaning in-flight work. Retry shortly.",
                            tool.as_str()
                        ),
                        "no_background_sessions",
                    )
                    .with_reason("command_session_count_unavailable"),
                ));
        }
    };
    if daemon > 0 {
        return Ok(HookOutcome::Deny(
            HookDenial::new(
                command_session_in_flight_message(daemon, tool),
                "no_background_sessions",
            )
            .with_reason("ephemeral_jobs_in_flight")
            .with_count(daemon),
        ));
    }
    Ok(HookOutcome::pass())
}

//! The disallow-nested-planner-deferral prehook — relocated out of the
//! `hooks.rs` monolith into its own file (mirrors `hooks/advisor_approval.rs`
//! and `hooks/require_no_inflight_background_tasks.rs`), porting Python
//! `tools/_hooks/disallow_nested_planner_deferral.py`.
//!
//! It denies a planner terminal that carries a nonblank
//! `deferred_goal_for_next_iteration` while the submitting workflow is nested
//! (`WorkflowControlPort::is_nested_workflow`). A nested planner cannot extend
//! its iteration chain, so deferral is the bounded-nesting guard.

use eos_types::JsonObject;
use serde_json::Value;

use crate::error::ToolError;
use crate::metadata::ExecutionMetadata;

use super::{HookDenial, HookOutcome};

const NESTED_PLANNER_DEFERRAL_MESSAGE: &str = "BLOCKED: nested workflow planners cannot set deferred_goal_for_next_iteration. Submit a plan that covers all current child-workflow goal items and leaves no remaining items.";

/// `DisallowNestedPlannerDeferral.run`: deny when a nonblank deferred goal is set
/// and the submitting workflow is nested. Passes when no deferred goal is set, or
/// when the nesting context (`workflow_id` + `workflow_control`) is unavailable.
pub(crate) async fn run_disallow_nested_planner_deferral(
    raw_input: &JsonObject,
    ctx: &ExecutionMetadata,
) -> Result<HookOutcome, ToolError> {
    let deferred = raw_input
        .get("deferred_goal_for_next_iteration")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    if deferred.is_none() {
        return Ok(HookOutcome::pass());
    }
    let (Some(workflow_id), Some(control)) = (&ctx.workflow_id, &ctx.workflow_control) else {
        return Ok(HookOutcome::pass());
    };
    if control.is_nested_workflow(workflow_id).await? {
        Ok(HookOutcome::Deny(
            HookDenial::new(NESTED_PLANNER_DEFERRAL_MESSAGE, "nested_planner_deferral")
                .with_reason("nested_workflow"),
        ))
    } else {
        Ok(HookOutcome::pass())
    }
}

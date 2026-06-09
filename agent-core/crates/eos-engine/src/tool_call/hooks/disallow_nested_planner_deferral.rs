//! The disallow-nested-planner-deferral prehook — relocated out of the hooks
//! module into its own file (mirrors `hooks/advisor_approval.rs`
//! and `hooks/require_no_background_sessions.rs`), porting Rust
//! `tools/_hooks/disallow_nested_planner_deferral.py`.
//!
//! It denies a planner terminal that carries a nonblank
//! `deferred_goal_for_next_iteration` when the submitting workflow's delegation
//! depth exceeds the configured `max_depth`. Depth is inferred from the workflow
//! context at hook execution via `WorkflowApi::workflow_depth`. A
//! too-deep planner cannot extend its iteration chain, bounding nesting.

use eos_types::JsonObject;

use eos_tool::{ExecutionMetadata, HookServices, ToolError};

use super::{deferred_goal, HookDenial, HookOutcome};

const NESTED_PLANNER_DEFERRAL_MESSAGE: &str = "BLOCKED: nested workflow planners cannot set deferred_goal_for_next_iteration. Submit a plan that covers all current child-workflow goal items and leaves no remaining items.";

/// `DisallowNestedPlannerDeferral.run`: deny when a nonblank deferred goal is set
/// and the submitting workflow's depth exceeds `max_depth`. Passes when no
/// deferred goal is set, or when the workflow context (`workflow_id` +
/// `workflow_service`) is unavailable to infer depth.
pub(crate) async fn run_disallow_nested_planner_deferral(
    max_depth: u32,
    raw_input: &JsonObject,
    ctx: &ExecutionMetadata,
    services: &HookServices,
) -> Result<HookOutcome, ToolError> {
    if deferred_goal(raw_input).is_none() {
        return Ok(HookOutcome::pass());
    }
    let (Some(workflow_id), Some(service)) = (&ctx.workflow_id, services.workflow_service()) else {
        return Ok(HookOutcome::pass());
    };
    if service.workflow_depth(workflow_id).await? > max_depth {
        Ok(HookOutcome::Deny(
            HookDenial::new(NESTED_PLANNER_DEFERRAL_MESSAGE, "nested_planner_deferral")
                .with_reason("nested_workflow"),
        ))
    } else {
        Ok(HookOutcome::pass())
    }
}

#![allow(clippy::unwrap_used)]
use std::sync::Arc;

use eos_types::{
    AttemptBudget, IterationCreationReason, IterationStatus, TaskOutcomeStatus, TaskStatus,
    WorkflowStatus,
};

use crate::support::{root_task, wait_for_workflow_status, MemoryStores, ScriptedRunner};
use crate::WorkflowStarter;

fn budget(value: u32) -> AttemptBudget {
    AttemptBudget::try_from_u32(value).unwrap()
}

// AC-eos-workflow-10 (retry budget): a failed attempt with budget remaining
// is retried; when the budget is exhausted the iteration closes FAILED and
// the workflow fails.
#[tokio::test]
async fn retry_and_continue() {
    let stores = Arc::new(MemoryStores::default());
    // Every attempt fails at the reducer, forcing retries until exhaustion.
    let runner = ScriptedRunner::new(1, TaskOutcomeStatus::Failed, 0, "");
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(2);
    runner.bind(&deps.orchestrator_registry);
    let parent = root_task("parent", TaskStatus::Running);
    stores.seed_task(parent.clone());
    let started = WorkflowStarter::new(deps)
        .start(
            "delegated goal",
            &parent.id,
            &eos_types::AgentRunId::new_v4(),
            None,
        )
        .await
        .unwrap();
    wait_for_workflow_status(&stores, &started.workflow_id, WorkflowStatus::Failed).await;

    let iteration = stores.iteration(&started.iteration_id).unwrap();
    assert_eq!(iteration.status, IterationStatus::Failed);
    // Budget 2 -> the initial attempt plus exactly one retry.
    assert_eq!(
        iteration.attempt_ids.len(),
        2,
        "one retry consumed the budget"
    );
    assert_eq!(
        stores.workflow(&started.workflow_id).unwrap().status,
        WorkflowStatus::Failed
    );
}

// AC-eos-workflow-10 (deferred-goal continuation): a passing attempt whose
// plan defers a goal closes its iteration SUCCEEDED and starts the next
// iteration (seq+1, DEFERRED_GOAL_CONTINUATION, goal = deferred goal).
#[tokio::test]
async fn deferred_goal_starts_next_iteration() {
    let stores = Arc::new(MemoryStores::default());
    // First planner run defers "continue the work"; the next completes.
    let runner = ScriptedRunner::new(1, TaskOutcomeStatus::Success, 1, "continue the work");
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(2);
    runner.bind(&deps.orchestrator_registry);
    let parent = root_task("parent", TaskStatus::Running);
    stores.seed_task(parent.clone());
    let started = WorkflowStarter::new(deps)
        .start(
            "delegated goal",
            &parent.id,
            &eos_types::AgentRunId::new_v4(),
            None,
        )
        .await
        .unwrap();
    wait_for_workflow_status(&stores, &started.workflow_id, WorkflowStatus::Succeeded).await;

    let iterations =
        eos_types::IterationStore::list_for_workflow(stores.as_ref(), &started.workflow_id)
            .await
            .unwrap();
    assert_eq!(
        iterations.len(),
        2,
        "the deferred goal created a second iteration"
    );
    assert_eq!(iterations[0].status, IterationStatus::Succeeded);
    let next = &iterations[1];
    assert_eq!(next.sequence_no, 2);
    assert_eq!(
        next.creation_reason,
        IterationCreationReason::DeferredGoalContinuation
    );
    assert_eq!(next.iteration_goal, "continue the work");
}

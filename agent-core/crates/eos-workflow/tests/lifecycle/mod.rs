#![allow(clippy::unwrap_used)]
use std::sync::Arc;

use eos_types::{DeferredGoal, IterationOutcome, WorkflowOutcome, WorkflowStatus};

use super::*;
use crate::support::{agent_registry_without_planner, root_task, MemoryStores, QueueRunner};

// AC-eos-workflow-05 / GC-eos-workflow-01: close_workflow sets the workflow
// status + outcomes and performs ZERO TaskStore writes (the parent task is
// never mutated at close).
#[tokio::test]
async fn close_does_not_touch_parent() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let deps = stores.deps(runner);
    let coordinators = deps.iteration_coordinators.clone().unwrap();
    let lifecycle = WorkflowLifecycle::new(deps, coordinators);

    let workflow = lifecycle
        .create_workflow(
            &eos_types::RequestId::new_v4(),
            &"parent".parse().unwrap(),
            &eos_types::AgentRunId::new_v4(),
            None,
            "delegated goal",
        )
        .await
        .unwrap();
    // One open iteration, no attempt -> close has no task work to do.
    lifecycle
        .create_iteration_with_coordinator(&workflow.id)
        .await
        .unwrap();
    // Prime the counter through the counted path so the zero *delta* below is
    // a real "close wrote no tasks", not a stuck-at-zero counter.
    eos_types::TaskStore::insert_task(
        stores.as_ref(),
        &crate::support::root_task("parent", eos_types::TaskStatus::Running),
    )
    .await
    .unwrap();

    let writes_before = stores.task_write_count();
    assert!(writes_before > 0, "counter must observe writes");
    let closed = lifecycle
        .close_workflow(&workflow.id, WorkflowOutcome::Succeeded)
        .await
        .unwrap();

    assert_eq!(stores.task_write_count(), writes_before);
    assert_eq!(closed.status, WorkflowStatus::Succeeded);
    assert!(closed.closed_at.is_some());
    assert_eq!(closed.outcomes.as_deref(), Some("[]"));
}

// AC-eos-workflow-10 (continuation compensation): a deferred-goal
// continuation whose first attempt fails to START runs the compensation saga
// — new iteration CANCELLED, workflow FAILED, and BOTH coordinators
// deregistered (the new one is FP1's leak, the old one FP2's) — and never
// mutates the parent. The error is swallowed (Ok), matching Rust's
// `except`. The deferred-path analogue of starter::compensation_rolls_back.
#[tokio::test]
async fn continuation_start_failure_compensates() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let mut deps = stores.deps(runner);
    // No `planner` profile -> the continuation's first-attempt launch fails.
    deps.agent_registry = Arc::new(agent_registry_without_planner());
    let coordinators = deps.iteration_coordinators.clone().unwrap();
    let lifecycle = WorkflowLifecycle::new(deps, coordinators.clone());

    let parent = root_task("parent", eos_types::TaskStatus::Running);
    stores.seed_task(parent.clone());

    let workflow = lifecycle
        .create_workflow(
            &eos_types::RequestId::new_v4(),
            &parent.id,
            &eos_types::AgentRunId::new_v4(),
            None,
            "delegated goal",
        )
        .await
        .unwrap();
    // Iteration 1 + its coordinator (the predecessor of the continuation).
    let (iter1, _coordinator1) = lifecycle
        .create_iteration_with_coordinator(&workflow.id)
        .await
        .unwrap();
    // Mark iter1 SUCCEEDED with a deferred goal so the continuation fires.
    // `create_iteration_with_coordinator` reads the iteration's
    // `deferred_goal_for_next_iteration`, not the signal, so both must be set.
    eos_types::IterationStore::close_succeeded(
        stores.as_ref(),
        &iter1.id,
        "[]",
        Some(eos_types::UtcDateTime::now()),
    )
    .await
    .unwrap();
    eos_types::IterationStore::set_deferred_goal_for_next_iteration(
        stores.as_ref(),
        &iter1.id,
        Some(&DeferredGoal::new("continue").unwrap()),
    )
    .await
    .unwrap();

    // Route the close: the continuation's planner launch fails, the saga
    // runs, and the original error is swallowed (Ok).
    lifecycle
        .handle_iteration_closed(IterationClosed {
            iteration_id: iter1.id.clone(),
            outcome: IterationOutcome::Continue {
                deferred_goal: DeferredGoal::new("continue").unwrap(),
            },
        })
        .await
        .unwrap();

    // Workflow failed (the continuation path closes FAILED, not CANCELLED).
    assert_eq!(
        stores.workflow(&workflow.id).unwrap().status,
        WorkflowStatus::Failed
    );
    // The continuation iteration (seq 2, DeferredGoalContinuation) is CANCELLED.
    let iterations = eos_types::IterationStore::list_for_workflow(stores.as_ref(), &workflow.id)
        .await
        .unwrap();
    assert_eq!(
        iterations.len(),
        2,
        "the deferred goal created a second iteration"
    );
    let iter2 = &iterations[1];
    assert_eq!(iter2.sequence_no, 2);
    assert_eq!(
        iter2.creation_reason,
        IterationCreationReason::DeferredGoalContinuation
    );
    assert_eq!(iter2.status, IterationStatus::Cancelled);
    // BOTH coordinators deregistered: new (FP1 primary) + old (FP2 secondary).
    assert!(
        coordinators.get(&iter2.id).is_none(),
        "new coordinator deregistered"
    );
    assert!(
        coordinators.get(&iter1.id).is_none(),
        "old coordinator deregistered"
    );
    // Parent untouched.
    assert_eq!(
        stores.task(&parent.id).unwrap().status,
        eos_types::TaskStatus::Running
    );
}

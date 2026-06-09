#![allow(clippy::unwrap_used)]
use std::sync::Arc;

use eos_types::{
    AttemptBudget, AttemptFailReason, AttemptStatus, GeneratorId, IterationStatus, PlanDisposition,
    ReducerId, Task, TaskOutcomeStatus, TaskRole, TaskStatus, WorkflowStatus,
};
use serde_json::json;

use super::AttemptStageAdvancer;
use crate::ids::generator_task_id;
use crate::support::{
    one_step_plan, root_task, wait_for_workflow_status, MemoryStores, QueueRunner, ScriptedRunner,
    ScriptedSubmission,
};
use crate::WorkflowStarter;

fn budget(value: u32) -> AttemptBudget {
    AttemptBudget::try_from_u32(value).unwrap()
}

fn gen_id(id: &str) -> GeneratorId {
    GeneratorId::new(id).unwrap()
}

fn red_id(id: &str) -> ReducerId {
    ReducerId::new(id).unwrap()
}

// AC-eos-workflow-08: the run is exercised entirely through the injected
// `AgentRunner` double (no eos-engine edge); the seam hands each role a
// well-formed launch.
#[tokio::test]
async fn injected_runner_double() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(1);
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
    let generator_id = generator_task_id(&started.attempt_id, &gen_id("g1")).unwrap();
    runner.push(ScriptedSubmission::Planner(one_step_plan(&started)));
    runner.push(ScriptedSubmission::Generator(
        eos_types::GeneratorSubmission {
            attempt_id: started.attempt_id.clone(),
            task_id: generator_id.clone(),
            status: TaskOutcomeStatus::Success,
            outcome: "generated".to_owned(),
            terminal_payload: crate::support::terminal_payload_fixture(),
        },
    ));
    runner.push(ScriptedSubmission::Reducer(eos_types::ReducerSubmission {
        attempt_id: started.attempt_id.clone(),
        task_id: crate::reducer_task_id(&started.attempt_id, &red_id("r1")).unwrap(),
        status: TaskOutcomeStatus::Success,
        outcome: "reduced".to_owned(),
        terminal_payload: crate::support::terminal_payload_fixture(),
    }));
    wait_for_workflow_status(&stores, &started.workflow_id, WorkflowStatus::Succeeded).await;

    let launches = runner.launches();
    assert_eq!(launches.len(), 3);
    assert_eq!(launches[0].role(), TaskRole::Planner);
    assert_eq!(launches[1].role(), TaskRole::Generator);
    assert_eq!(launches[1].task_id(), &generator_id);
    assert_eq!(launches[1].attempt_id(), &started.attempt_id);
    assert_eq!(launches[2].role(), TaskRole::Reducer);
}

// AC-eos-workflow-07 (liveness): a generator run that ends WITHOUT a terminal
// submission is mapped to a synthesized failure; the attempt advances to a
// terminal state instead of hanging.
#[tokio::test]
async fn dead_agent_synthesizes_failure() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(1);
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
    runner.push(ScriptedSubmission::Planner(one_step_plan(&started)));
    runner.push(ScriptedSubmission::NoSubmission(
        "generator ended without terminal".to_owned(),
    ));
    wait_for_workflow_status(&stores, &started.workflow_id, WorkflowStatus::Failed).await;

    let attempt = stores.attempt(&started.attempt_id).unwrap();
    assert_eq!(attempt.status(), AttemptStatus::Failed);
    assert_eq!(attempt.fail_reason(), Some(AttemptFailReason::TaskFailed));
    let generator_id = generator_task_id(&started.attempt_id, &gen_id("g1")).unwrap();
    let task = stores.task(&generator_id).unwrap();
    assert_eq!(task.status, TaskStatus::Failed);
    assert_eq!(
        task.terminal_payload.unwrap().get("fail_reason"),
        Some(&json!("run_exhausted"))
    );
    assert_eq!(
        stores.iteration(&started.iteration_id).unwrap().status,
        IterationStatus::Failed
    );
    assert_eq!(
        stores.workflow(&started.workflow_id).unwrap().status,
        WorkflowStatus::Failed
    );
}

// AC-eos-workflow-07 (liveness, planner): a planner run with no terminal is
// synthesized into a planner failure (run_exhausted) and the attempt fails.
#[tokio::test]
async fn dead_planner_synthesizes_failure() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(1);
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
    runner.push(ScriptedSubmission::NoSubmission(
        "planner ended without terminal".to_owned(),
    ));
    wait_for_workflow_status(&stores, &started.workflow_id, WorkflowStatus::Failed).await;

    let attempt = stores.attempt(&started.attempt_id).unwrap();
    assert_eq!(attempt.status(), AttemptStatus::Failed);
    assert_eq!(attempt.fail_reason(), Some(AttemptFailReason::TaskFailed));
    let planner_task = stores
        .task(&crate::planner_task_id(&started.attempt_id).unwrap())
        .unwrap();
    assert_eq!(planner_task.status, TaskStatus::Failed);
    assert_eq!(
        planner_task.terminal_payload.unwrap().get("fail_reason"),
        Some(&json!("run_exhausted"))
    );
}

// AC-eos-workflow-08b (per-attempt fan-out cap): with 10 ready generators and
// max_concurrent_task_runs = 3, no more than 3 agent runs are in flight at
// once; surplus ready tasks stay pending, and the reducer runs only after all
// its generator needs are done.
#[tokio::test]
async fn fanout_respects_concurrency_cap() {
    let stores = Arc::new(MemoryStores::default());
    let runner = ScriptedRunner::new(10, TaskOutcomeStatus::Success, 0, "");
    let mut deps = stores.deps(runner.clone());
    deps.lifecycle_config.default_attempt_budget = budget(1);
    deps.max_concurrent_task_runs = 3;
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

    // The cap binds exactly: with 10 ready generators the scheduler keeps 3
    // runs in flight (never more — the ceiling; never fewer once saturated —
    // proving launches are not serialized). Deterministic on the
    // current-thread test runtime: 3 are spawned before the first
    // `join_next().await`, and each runner future yields before completing,
    // so all 3 enter before any exits.
    assert_eq!(
        runner.max_in_flight(),
        3,
        "expected the per-attempt cap of 3 to be saturated, got {}",
        runner.max_in_flight()
    );
    assert_eq!(
        stores.attempt(&started.attempt_id).unwrap().status(),
        AttemptStatus::Passed
    );

    let launches = runner.launches();
    // planner + 10 generators + 1 reducer.
    assert_eq!(launches.len(), 12);
    assert_eq!(launches[0].role(), TaskRole::Planner);
    assert_eq!(launches.last().unwrap().role(), TaskRole::Reducer);
    let generators = launches[1..11]
        .iter()
        .filter(|l| l.role() == TaskRole::Generator)
        .count();
    assert_eq!(generators, 10, "all generators ran before the reducer");
}

// The launcher marks a task FAILED (instead of stranding it RUNNING) when its
// launch context cannot be built (here: a generator with no agent profile).
#[tokio::test]
async fn launch_failure_marks_task_failed() {
    let stores = Arc::new(MemoryStores::default());
    let runner = Arc::new(QueueRunner::default());
    let mut deps = stores.deps(runner);
    deps.lifecycle_config.default_attempt_budget = budget(1);
    let parent = root_task("parent", TaskStatus::Running);
    stores.seed_task(parent.clone());
    let started = WorkflowStarter::new(deps.clone())
        .start(
            "delegated goal",
            &parent.id,
            &eos_types::AgentRunId::new_v4(),
            None,
        )
        .await
        .unwrap();
    let generator_id = gen_id("missing-profile");
    let task_id = generator_task_id(&started.attempt_id, &generator_id).unwrap();
    stores.seed_task(Task {
        id: task_id.clone(),
        request_id: parent.request_id,
        role: TaskRole::Generator,
        instruction: "do work".to_owned(),
        status: TaskStatus::Pending,
        workflow_id: Some(started.workflow_id.clone()),
        iteration_id: Some(started.iteration_id.clone()),
        attempt_id: Some(started.attempt_id.clone()),
        agent_name: None,
        needs: Vec::new(),
        outcomes: Vec::new(),
        terminal_payload: None,
    });
    eos_types::AttemptStore::record_plan(
        stores.as_ref(),
        &started.attempt_id,
        &eos_types::MaterializedPlan {
            planner_task_id: crate::planner_task_id(&started.attempt_id).unwrap(),
            disposition: PlanDisposition::Complete,
            generator_task_ids: vec![task_id.clone()],
            reducer_task_ids: Vec::new(),
        },
    )
    .await
    .unwrap();

    let orchestrator = deps.orchestrator_registry.get(&started.attempt_id).unwrap();
    AttemptStageAdvancer::new(orchestrator)
        .advance_run_stage()
        .await
        .unwrap();

    let task = stores.task(&task_id).unwrap();
    assert_eq!(task.status, TaskStatus::Failed);
    assert_eq!(
        task.terminal_payload.unwrap().get("fail_reason"),
        Some(&json!("agent_launch_failed"))
    );
    assert_eq!(
        stores.attempt(&started.attempt_id).unwrap().status(),
        AttemptStatus::Failed
    );
    assert_eq!(
        stores.workflow(&started.workflow_id).unwrap().status,
        WorkflowStatus::Failed
    );
}

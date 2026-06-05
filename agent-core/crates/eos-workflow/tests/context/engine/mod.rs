#![allow(clippy::unwrap_used)]
use std::sync::Arc;

use eos_state::{
    ExecutionTaskOutcome, IterationCreationReason, RequestId, Task, TaskOutcomeStatus,
};

use super::*;
use crate::context::{render_context_xml, render_task_guidance, ContextScope, ContextSection};
use crate::ids::{generator_task_id, reducer_task_id};
use crate::testsupport::{tid, MemoryStores};

fn deps(stores: &Arc<MemoryStores>) -> ContextEngineDeps {
    ContextEngineDeps {
        workflow_store: stores.clone(),
        iteration_store: stores.clone(),
        attempt_store: stores.clone(),
        task_store: stores.clone(),
    }
}

fn outcome(
    status: TaskOutcomeStatus,
    role: ExecutionRole,
    task_id: &eos_state::TaskId,
    text: &str,
) -> ExecutionTaskOutcome {
    ExecutionTaskOutcome {
        status,
        role,
        task_id: task_id.clone(),
        outcome: text.to_owned(),
    }
}

#[allow(clippy::too_many_arguments)]
fn exec_task(
    stores: &MemoryStores,
    id: &eos_state::TaskId,
    request_id: &RequestId,
    role: TaskRole,
    instruction: &str,
    status: TaskStatus,
    needs: Vec<eos_state::TaskId>,
    outcomes: Vec<ExecutionTaskOutcome>,
    attempt: &Attempt,
) {
    stores.seed_task(Task {
        id: id.clone(),
        request_id: request_id.clone(),
        role,
        instruction: instruction.to_owned(),
        status,
        workflow_id: Some(attempt.workflow_id.clone()),
        iteration_id: Some(attempt.iteration_id.clone()),
        attempt_id: Some(attempt.id.clone()),
        agent_name: Some(
            if role == TaskRole::Reducer {
                "reducer"
            } else {
                "coder"
            }
            .to_owned(),
        ),
        needs,
        outcomes,
        terminal_tool_result: None,
    });
}

// AC-eos-workflow-09: planner context mirrors test_agent_context.py
// (workflow shape + prior-iteration + previous-attempt execution outcomes).
#[tokio::test]
async fn build_planner_context_matches_source() {
    let stores = Arc::new(MemoryStores::default());
    let request_id = RequestId::new_v4();
    let workflow = stores.seed_workflow("Build the complete feature.").await;

    let prior = stores
        .seed_iteration(
            &workflow.id,
            1,
            IterationCreationReason::Initial,
            "Build storage.",
            2,
        )
        .await;
    let prior_outcomes = serde_json::to_string(&vec![outcome(
        TaskOutcomeStatus::Success,
        ExecutionRole::Reducer,
        &tid("attempt1:red:verify_storage"),
        "Storage layer is implemented and verified.",
    )])
    .unwrap();
    eos_state::IterationStore::close_succeeded(
        stores.as_ref(),
        &prior.id,
        &prior_outcomes,
        Some(eos_state::UtcDateTime::now()),
    )
    .await
    .unwrap();

    let current = stores
        .seed_iteration(
            &workflow.id,
            2,
            IterationCreationReason::DeferredGoalContinuation,
            "Finish the API and CLI slice.",
            3,
        )
        .await;
    let previous_attempt = stores.seed_attempt(&current.id, &workflow.id, 1).await;
    let gen_id = generator_task_id(&previous_attempt.id, "api").unwrap();
    let red_id = reducer_task_id(&previous_attempt.id, "verify_api").unwrap();
    eos_state::AttemptStore::set_generator_task_ids(
        stores.as_ref(),
        &previous_attempt.id,
        std::slice::from_ref(&gen_id),
    )
    .await
    .unwrap();
    eos_state::AttemptStore::set_reducer_task_ids(
        stores.as_ref(),
        &previous_attempt.id,
        std::slice::from_ref(&red_id),
    )
    .await
    .unwrap();
    exec_task(
        &stores,
        &gen_id,
        &request_id,
        TaskRole::Generator,
        "Implement API.",
        TaskStatus::Done,
        Vec::new(),
        vec![outcome(
            TaskOutcomeStatus::Success,
            ExecutionRole::Generator,
            &gen_id,
            "API endpoints were implemented.",
        )],
        &previous_attempt,
    );
    exec_task(
        &stores,
        &red_id,
        &request_id,
        TaskRole::Reducer,
        "Verify API.",
        TaskStatus::Failed,
        vec![gen_id.clone()],
        vec![outcome(
            TaskOutcomeStatus::Failed,
            ExecutionRole::Reducer,
            &red_id,
            "Verification failed because the CLI command still calls the old endpoint.",
        )],
        &previous_attempt,
    );
    eos_state::AttemptStore::close(
        stores.as_ref(),
        &previous_attempt.id,
        eos_state::AttemptStatus::Failed,
        Some(eos_state::AttemptFailReason::TaskFailed),
        Some(&[]),
        eos_state::UtcDateTime::now(),
    )
    .await
    .unwrap();
    let current_attempt = stores.seed_attempt(&current.id, &workflow.id, 2).await;

    let context = ContextEngine::new(deps(&stores))
        .build(
            "planner",
            &ContextScope::for_planner(
                workflow.id.clone(),
                current.id.clone(),
                current_attempt.id.clone(),
            ),
        )
        .await
        .unwrap();
    let xml = render_context_xml(&context);

    assert!(xml.contains("<context role=\"planner\">"), "{xml}");
    assert!(xml.contains("<workflow>"));
    assert!(xml.contains("<prior_iterations>"));
    assert!(xml.contains(&format!("<iteration sequence=\"{}\">", prior.sequence_no)));
    assert!(xml.contains(
        "<task task_id=\"attempt1:red:verify_storage\" role=\"reducer\" status=\"success\">"
    ));
    assert!(xml.contains(&format!(
        "<current_iteration sequence=\"{}\">",
        current.sequence_no
    )));
    assert!(xml.contains("<attempt sequence=\"1\" status=\"failed\">"));
    assert!(xml.contains(&format!(
        "<task task_id=\"{}\" role=\"generator\" status=\"success\">",
        gen_id.as_str()
    )));
    assert!(xml.contains(&format!(
        "<task task_id=\"{}\" role=\"reducer\" status=\"failed\">",
        red_id.as_str()
    )));
    assert!(!xml.contains("<outcomes>"));
    // Planner outcomes are omitted from prior history.
    assert!(!xml
        .split("<prior_iterations>")
        .nth(1)
        .unwrap()
        .contains("planner"));

    let guidance = render_task_guidance(&context);
    assert!(guidance.contains("<workflow>: workflow goal and current planning frame"));
    assert!(guidance.contains("Planner outcomes are omitted"));
}

// AC-eos-workflow-09: generator context = dependencies + assigned_task.
#[tokio::test]
async fn build_generator_context_is_dependencies_plus_assigned_task() {
    let stores = Arc::new(MemoryStores::default());
    let request_id = RequestId::new_v4();
    let workflow = stores.seed_workflow("Build the complete feature.").await;
    let iteration = stores
        .seed_iteration(
            &workflow.id,
            1,
            IterationCreationReason::Initial,
            &workflow.workflow_goal,
            2,
        )
        .await;
    let attempt = stores.seed_attempt(&iteration.id, &workflow.id, 1).await;
    let dep_id = generator_task_id(&attempt.id, "storage").unwrap();
    let task_id = generator_task_id(&attempt.id, "api").unwrap();
    exec_task(
        &stores,
        &dep_id,
        &request_id,
        TaskRole::Generator,
        "Build storage.",
        TaskStatus::Done,
        Vec::new(),
        vec![outcome(
            TaskOutcomeStatus::Success,
            ExecutionRole::Generator,
            &dep_id,
            "Storage done.",
        )],
        &attempt,
    );
    exec_task(
        &stores,
        &task_id,
        &request_id,
        TaskRole::Generator,
        "Implement the API endpoints.",
        TaskStatus::Pending,
        vec![dep_id.clone()],
        Vec::new(),
        &attempt,
    );

    let context = ContextEngine::new(deps(&stores))
        .build(
            "generator",
            &ContextScope::for_generator(
                workflow.id.clone(),
                iteration.id.clone(),
                attempt.id.clone(),
                task_id.clone(),
            ),
        )
        .await
        .unwrap();
    let xml = render_context_xml(&context);

    assert!(xml.contains("<context role=\"generator\">"), "{xml}");
    assert!(xml.contains("<dependencies>"));
    assert!(xml.contains(&format!("<dependency task_id=\"{}\">", dep_id.as_str())));
    assert!(xml.contains(&format!("<assigned_task task_id=\"{}\">", task_id.as_str())));
    assert!(xml.contains("Implement the API endpoints."));
    assert!(!xml.contains("<workflow>"));
    assert!(!xml.contains("<needs>"));
    assert!(render_task_guidance(&context)
        .contains("Complete <assigned_task> using <dependencies>."));
}

// AC-eos-workflow-09: reducer context uses assigned_task, not assigned_prompt.
#[tokio::test]
async fn build_reducer_context_uses_assigned_task() {
    let stores = Arc::new(MemoryStores::default());
    let request_id = RequestId::new_v4();
    let workflow = stores.seed_workflow("Build the complete feature.").await;
    let iteration = stores
        .seed_iteration(
            &workflow.id,
            1,
            IterationCreationReason::Initial,
            &workflow.workflow_goal,
            2,
        )
        .await;
    let attempt = stores.seed_attempt(&iteration.id, &workflow.id, 1).await;
    let dep_id = generator_task_id(&attempt.id, "api").unwrap();
    let task_id = reducer_task_id(&attempt.id, "verify_api").unwrap();
    exec_task(
        &stores,
        &dep_id,
        &request_id,
        TaskRole::Generator,
        "Build API.",
        TaskStatus::Done,
        Vec::new(),
        vec![outcome(
            TaskOutcomeStatus::Success,
            ExecutionRole::Generator,
            &dep_id,
            "API done.",
        )],
        &attempt,
    );
    exec_task(
        &stores,
        &task_id,
        &request_id,
        TaskRole::Reducer,
        "Verify the API and CLI slice.",
        TaskStatus::Pending,
        vec![dep_id.clone()],
        Vec::new(),
        &attempt,
    );

    let context = ContextEngine::new(deps(&stores))
        .build(
            "reducer",
            &ContextScope::for_reducer(
                workflow.id.clone(),
                iteration.id.clone(),
                attempt.id.clone(),
                task_id.clone(),
            ),
        )
        .await
        .unwrap();
    let xml = render_context_xml(&context);

    assert!(xml.contains("<context role=\"reducer\">"), "{xml}");
    assert!(xml.contains(&format!("<assigned_task task_id=\"{}\">", task_id.as_str())));
    assert!(xml.contains("Verify the API and CLI slice."));
    assert!(!xml.contains("<assigned_prompt>"));
    assert!(!xml.contains("<needs>"));
}

// AC-eos-workflow-09: a recipe whose id != scope role is rejected.
#[tokio::test]
async fn build_rejects_recipe_role_mismatch() {
    let stores = Arc::new(MemoryStores::default());
    let err = ContextEngine::new(deps(&stores))
        .build(
            "planner",
            &ContextScope::for_generator(
                eos_state::WorkflowId::new_v4(),
                eos_state::IterationId::new_v4(),
                eos_state::AttemptId::new_v4(),
                tid("task"),
            ),
        )
        .await
        .unwrap_err();
    assert!(
        matches!(err, WorkflowError::Recipe(ref msg) if msg.contains("cannot build role")),
        "{err:?}"
    );
}

// AC-eos-workflow-09 golden: deterministic XML render (escaping, attr order,
// nesting, trailing newline) over a fixed-id context.
#[test]
fn render_context_xml_golden() {
    let context = AgentContext {
        role: ContextRole::Generator,
        sections: vec![
            ContextSection::new("dependencies").with_children(vec![ContextSection::new(
                "dependency",
            )
            .with_attrs(vec![("task_id".to_owned(), "dep-1".to_owned())])
            .with_children(vec![ContextSection::new("task")
                .with_attrs(vec![
                    ("task_id".to_owned(), "dep-1".to_owned()),
                    ("role".to_owned(), "generator".to_owned()),
                    ("status".to_owned(), "success".to_owned()),
                ])
                .with_text("Storage done.")])]),
            ContextSection::new("assigned_task")
                .with_attrs(vec![("task_id".to_owned(), "task-1".to_owned())])
                .with_text("Implement the API <endpoints>."),
        ],
        directive: "Complete <assigned_task> using <dependencies>.".to_owned(),
        context_limits: Vec::new(),
    };
    let expected = "\
<context role=\"generator\">
<dependencies>
<dependency task_id=\"dep-1\">
<task task_id=\"dep-1\" role=\"generator\" status=\"success\">
Storage done.
</task>
</dependency>
</dependencies>
<assigned_task task_id=\"task-1\">
Implement the API &lt;endpoints&gt;.
</assigned_task>
</context>
";
    assert_eq!(render_context_xml(&context), expected);
}

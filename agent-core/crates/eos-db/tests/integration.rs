//! Cross-store integration tests against a real temp `SQLite` file.
#![allow(clippy::expect_used)]

use eos_db::{Database, DatabaseConfig, DatabaseUrl};
use eos_types::{
    format_record_dir, AgentName, AgentType, AttemptBudget, AttemptClosure, DeferredGoal,
    ExecutionNode, ExecutionStatus, IterationCreationReason, IterationStatus, JsonObject,
    RequestId, RequestStatus, SubmissionOutcome, ToolUseId, UtcDateTime, WorkItemId, WorkItemSpec,
    WorkflowStatus,
};
use serde_json::json;
use sqlx::Row;

async fn open_temp() -> (tempfile::TempDir, Database) {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("test.db");
    let mut cfg = DatabaseConfig::default();
    cfg.url = DatabaseUrl::parse(format!("sqlite://{}", path.display())).expect("url");
    let db = Database::open(&cfg).await.expect("open");
    (dir, db)
}

async fn table_column_names(db: &Database, table: &str) -> Vec<String> {
    sqlx::query(&format!("PRAGMA table_info({table})"))
        .fetch_all(db.pool())
        .await
        .expect("table_info")
        .into_iter()
        .map(|row| row.get("name"))
        .collect()
}

async fn table_names(db: &Database) -> Vec<String> {
    sqlx::query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        .fetch_all(db.pool())
        .await
        .expect("table names")
        .into_iter()
        .map(|row| row.get("name"))
        .collect()
}

fn rid(s: &str) -> RequestId {
    s.parse().expect("request id")
}

fn arid(s: &str) -> eos_types::AgentRunId {
    s.parse().expect("agent run id")
}

fn tool_use_id(s: &str) -> ToolUseId {
    s.parse().expect("tool use id")
}

fn agent_name(s: &str) -> AgentName {
    AgentName::new(s).expect("agent name")
}

fn work_item_id(s: &str) -> WorkItemId {
    WorkItemId::new(s).expect("work item id")
}

fn json_obj(pairs: &[(&str, serde_json::Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), value.clone()))
        .collect()
}

#[tokio::test]
async fn schema_uses_agent_run_only_contract_columns() {
    let (_dir, db) = open_temp().await;

    assert_eq!(
        table_names(&db).await,
        [
            "_sqlx_migrations",
            "agent_runs",
            "attempts",
            "iterations",
            "model_registrations",
            "requests",
            "sqlite_sequence",
            "workflows",
        ]
    );
    assert_eq!(
        table_column_names(&db, "workflows").await,
        [
            "id",
            "request_id",
            "parent_agent_run_id",
            "tool_use_id",
            "workflow_goal",
            "status",
            "iteration_ids",
            "created_at",
            "updated_at",
            "closed_at",
        ]
    );
    assert_eq!(
        table_column_names(&db, "agent_runs").await,
        [
            "agent_run_id",
            "request_id",
            "agent_type",
            "status",
            "agent_name",
            "parent_agent_run_id",
            "tool_use_id",
            "terminal_payload",
            "submission_outcome",
            "token_count",
            "error",
            "created_at",
            "updated_at",
            "finished_at",
        ]
    );
    assert_eq!(
        table_column_names(&db, "attempts").await,
        [
            "id",
            "iteration_id",
            "workflow_id",
            "attempt_sequence_no",
            "stage",
            "status",
            "plan_id",
            "execution_tree",
            "fail_reason",
            "created_at",
            "updated_at",
            "closed_at",
        ]
    );
}

#[tokio::test]
async fn workflow_iteration_attempt_roundtrip_uses_agent_run_bindings() {
    let (_dir, db) = open_temp().await;
    let request_id = rid("req-1");
    db.requests()
        .create_request(&request_id, "/work", None, "build")
        .await
        .expect("create request");

    let parent_run_id = arid("run-parent");
    db.agent_runs()
        .create_agent_run(
            &parent_run_id,
            &request_id,
            &agent_name("root"),
            AgentType::Main,
            None,
            None,
        )
        .await
        .expect("create parent run");

    let workflow = db
        .workflows()
        .insert(
            &request_id,
            &parent_run_id,
            Some(&tool_use_id("toolu-workflow")),
            "workflow goal",
        )
        .await
        .expect("insert workflow");
    assert_eq!(workflow.workflow_goal, "workflow goal");
    assert_eq!(workflow.parent_agent_run_id, parent_run_id);

    let iteration = db
        .iterations()
        .insert(
            &workflow.id,
            1,
            IterationCreationReason::Initial,
            "workflow goal",
            "iteration goal",
            AttemptBudget::try_from_u32(2).expect("budget"),
        )
        .await
        .expect("insert iteration");
    assert_eq!(iteration.workflow_goal, "workflow goal");
    assert_eq!(iteration.iteration_goal, "iteration goal");
    db.workflows()
        .append_iteration_id(&workflow.id, &iteration.id)
        .await
        .expect("append iteration");

    let attempt = db
        .attempts()
        .insert(&iteration.id, &workflow.id, 1)
        .await
        .expect("insert attempt");
    assert_eq!(attempt.plan_id, attempt.execution_tree.plan_id);
    assert!(!attempt.execution_tree.planner_started);
    assert!(attempt.execution_tree.planner_outcome.is_none());
    assert!(attempt.execution_tree.nodes.is_empty());

    let started = db
        .attempts()
        .mark_planner_started(&attempt.id)
        .await
        .expect("mark planner started");
    assert!(started.execution_tree.planner_started);

    let work_items = vec![
        WorkItemSpec {
            id: work_item_id("w1"),
            agent_name: agent_name("executor"),
            work_spec: "do first".to_owned(),
            needs: Vec::new(),
        },
        WorkItemSpec {
            id: work_item_id("w2"),
            agent_name: agent_name("executor"),
            work_spec: "do second".to_owned(),
            needs: vec![work_item_id("w1")],
        },
    ];
    let planner_outcome = SubmissionOutcome::Planner {
        plan_spec: "plan spec".to_owned(),
        work_items: work_items.clone(),
        deferred_goal_for_next_iteration: Some(
            DeferredGoal::new("next iteration").expect("deferred goal"),
        ),
    };
    let nodes = vec![
        ExecutionNode {
            work_item_id: work_item_id("w1"),
            needs: Vec::new(),
            agent_run_id: None,
            status: None,
            outcome: None,
        },
        ExecutionNode {
            work_item_id: work_item_id("w2"),
            needs: vec![work_item_id("w1")],
            agent_run_id: None,
            status: None,
            outcome: None,
        },
    ];
    let running = db
        .attempts()
        .record_plan_outcome(&attempt.id, &planner_outcome, &nodes)
        .await
        .expect("record planner outcome");
    assert_eq!(
        running.execution_tree.planner_outcome,
        Some(planner_outcome)
    );
    assert_eq!(running.execution_tree.nodes, nodes);
    assert_eq!(running.stage(), eos_types::AttemptStage::Run);

    let worker_run_id = arid("run-worker");
    db.agent_runs()
        .create_agent_run(
            &worker_run_id,
            &request_id,
            &agent_name("executor"),
            AgentType::Worker,
            Some(&parent_run_id),
            Some(&tool_use_id("toolu-worker")),
        )
        .await
        .expect("create worker run");
    let bound = db
        .attempts()
        .bind_worker_agent_run(&attempt.id, &work_item_id("w1"), &worker_run_id)
        .await
        .expect("bind worker");
    let node = bound
        .execution_tree
        .node(&work_item_id("w1"))
        .expect("node");
    assert_eq!(node.agent_run_id.as_ref(), Some(&worker_run_id));
    assert_eq!(node.status, Some(ExecutionStatus::Running));

    let worker_outcome = SubmissionOutcome::Worker {
        is_pass: true,
        outcome: "done".to_owned(),
    };
    let completed = db
        .attempts()
        .record_worker_outcome(
            &attempt.id,
            &work_item_id("w1"),
            ExecutionStatus::Done,
            &worker_outcome,
        )
        .await
        .expect("record worker outcome");
    let node = completed
        .execution_tree
        .node(&work_item_id("w1"))
        .expect("node");
    assert_eq!(node.status, Some(ExecutionStatus::Done));
    assert_eq!(node.outcome.as_ref(), Some(&worker_outcome));

    let closed = db
        .attempts()
        .close(
            &attempt.id,
            AttemptClosure::Passed {
                closed_at: UtcDateTime::now(),
            },
        )
        .await
        .expect("close attempt");
    assert_eq!(closed.status(), eos_types::AttemptStatus::Passed);
    assert!(closed.closed_at().is_some());

    let closed_iteration = db
        .iterations()
        .set_status(
            &iteration.id,
            IterationStatus::Succeeded,
            Some(UtcDateTime::now()),
        )
        .await
        .expect("close iteration");
    assert_eq!(closed_iteration.status, IterationStatus::Succeeded);

    let closed_workflow = db
        .workflows()
        .set_status(
            &workflow.id,
            WorkflowStatus::Succeeded,
            Some(UtcDateTime::now()),
        )
        .await
        .expect("close workflow");
    assert_eq!(closed_workflow.status, WorkflowStatus::Succeeded);
}

#[tokio::test]
async fn agent_run_outcomes_and_all_type_lineage_roundtrip() {
    let (_dir, db) = open_temp().await;
    let request_id = rid("req-runs");
    db.requests()
        .create_request(&request_id, "/work", None, "run")
        .await
        .expect("create request");

    let root_run = db
        .agent_runs()
        .create_agent_run(
            &arid("run-root"),
            &request_id,
            &agent_name("root"),
            AgentType::Main,
            None,
            None,
        )
        .await
        .expect("root run");
    assert_eq!(
        root_run.record_target.record_dir,
        format_record_dir(&eos_types::AgentRunRecordIndex {
            request_id: request_id.clone(),
            agent_run_id: arid("run-root"),
        })
    );

    let root_payload = json_obj(&[
        ("kind", json!("root")),
        ("is_pass", json!(true)),
        ("outcome", json!("ok")),
    ]);
    let root_outcome = SubmissionOutcome::Root {
        is_pass: true,
        outcome: "ok".to_owned(),
    };
    let finished_root = db
        .agent_runs()
        .finish_agent_run(
            &root_run.agent_run_id,
            ExecutionStatus::Done,
            Some(&root_payload),
            Some(&root_outcome),
            9,
            None,
        )
        .await
        .expect("finish root")
        .expect("root row");
    assert_eq!(finished_root.submission_outcome, Some(root_outcome));
    assert_eq!(finished_root.terminal_payload, Some(root_payload));

    let child_cases = [
        ("run-main-child", AgentType::Main, "main-child"),
        ("run-planner", AgentType::Planner, "planner"),
        ("run-worker", AgentType::Worker, "worker"),
        ("run-advisor", AgentType::Advisor, "advisor"),
        ("run-subagent", AgentType::Subagent, "subagent"),
    ];
    for (run_id, agent_type, name) in child_cases {
        let agent_run_id = arid(run_id);
        let tool_id = tool_use_id(&format!("toolu-{name}"));
        let child = db
            .agent_runs()
            .create_agent_run(
                &agent_run_id,
                &request_id,
                &agent_name(name),
                agent_type,
                Some(&root_run.agent_run_id),
                Some(&tool_id),
            )
            .await
            .expect("child run");
        assert_eq!(
            child.record_target.record_dir,
            format_record_dir(&eos_types::AgentRunRecordIndex {
                request_id: request_id.clone(),
                agent_run_id: agent_run_id.clone(),
            })
        );
        let index = db
            .agent_runs()
            .record_index_for_agent_run(&agent_run_id)
            .await
            .expect("record index")
            .expect("record index exists");
        assert_eq!(index.request_id, request_id);
        assert_eq!(index.agent_run_id, agent_run_id);
    }

    let all_runs = db
        .agent_runs()
        .list_agent_runs_for_request(&request_id)
        .await
        .expect("request runs");
    assert_eq!(all_runs.len(), 6);
    assert!(all_runs.iter().any(|run| run.agent_type == AgentType::Main));
    assert!(all_runs
        .iter()
        .any(|run| run.agent_type == AgentType::Planner));
    assert!(all_runs
        .iter()
        .any(|run| run.agent_type == AgentType::Worker));
    assert!(all_runs
        .iter()
        .any(|run| run.agent_type == AgentType::Advisor));
    assert!(all_runs
        .iter()
        .any(|run| run.agent_type == AgentType::Subagent));

    let advisor_run_id = arid("run-advisor");
    let children = db
        .agent_runs()
        .list_child_agent_runs_for_parent_agent_run(&arid("run-root"), Some(AgentType::Advisor))
        .await
        .expect("children");
    assert_eq!(children.len(), 1);
    assert_eq!(children[0].agent_run_id, advisor_run_id);
    assert_eq!(children[0].parent_agent_run_id, Some(arid("run-root")));

    let advisor_payload = json_obj(&[
        ("kind", json!("advisor")),
        ("verdict", json!("approve")),
        ("outcome", json!("approved")),
    ]);
    let finished_advisor = db
        .agent_runs()
        .finish_agent_run(
            &advisor_run_id,
            ExecutionStatus::Done,
            Some(&advisor_payload),
            None,
            2,
            None,
        )
        .await
        .expect("finish advisor")
        .expect("advisor row");
    assert_eq!(finished_advisor.terminal_payload, Some(advisor_payload));
    assert_eq!(finished_advisor.submission_outcome, None);
}

#[tokio::test]
async fn record_index_resolves_from_flat_agent_run_identity() {
    let (_dir, db) = open_temp().await;
    let request_id = rid("req-record-index");
    let agent_run_id = arid("run-record");
    db.requests()
        .create_request(&request_id, "/work", None, "record index")
        .await
        .expect("create request");
    db.agent_runs()
        .create_agent_run(
            &agent_run_id,
            &request_id,
            &agent_name("root"),
            AgentType::Main,
            None,
            None,
        )
        .await
        .expect("agent run");

    let index = db
        .agent_runs()
        .record_index_for_agent_run(&agent_run_id)
        .await
        .expect("record index")
        .expect("record index exists");
    assert_eq!(index.request_id, request_id);
    assert_eq!(index.agent_run_id, agent_run_id);
    assert_eq!(
        format_record_dir(&index).as_str(),
        "requests/req-record-index/agent-runs/agent-run-run-record"
    );
}

#[tokio::test]
async fn request_finish_remains_terminal_noop() {
    let (_dir, db) = open_temp().await;
    let request_id = rid("req-terminal");
    db.requests()
        .create_request(&request_id, "/work", None, "done")
        .await
        .expect("create request");
    db.requests()
        .finish_request(&request_id, RequestStatus::Done)
        .await
        .expect("finish")
        .expect("request");
    let again = db
        .requests()
        .finish_request(&request_id, RequestStatus::Failed)
        .await
        .expect("finish again")
        .expect("request");
    assert_eq!(again.status, RequestStatus::Done);
}

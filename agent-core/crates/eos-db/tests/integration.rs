//! Cross-store integration tests against a real temp `SQLite` file (AC-eos-db-01..05, 08).
#![allow(clippy::expect_used)]

use eos_config::{DatabaseConfig, DatabaseUrl};
use eos_db::Database;
use eos_state::{
    AttemptFailReason, AttemptStage, AttemptStatus, ExecutionRole, ExecutionTaskOutcome,
    IterationCreationReason, IterationStatus, JsonObject, RequestId, RequestStatus, Task, TaskId,
    TaskRole, TaskStatus, UtcDateTime, WorkflowStatus,
};

async fn open_temp() -> (tempfile::TempDir, Database) {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("test.db");
    let mut cfg = DatabaseConfig::default();
    cfg.url = DatabaseUrl::parse(format!("sqlite://{}", path.display())).expect("url");
    let db = Database::open(&cfg).await.expect("open");
    (dir, db)
}

fn rid(s: &str) -> RequestId {
    s.parse().expect("request id")
}

fn tid(s: &str) -> TaskId {
    s.parse().expect("task id")
}

fn sample_task(id: &str, request_id: &RequestId, instruction: &str) -> Task {
    Task {
        id: tid(id),
        request_id: request_id.clone(),
        role: TaskRole::Generator,
        instruction: instruction.to_owned(),
        status: TaskStatus::Pending,
        workflow_id: None,
        iteration_id: None,
        attempt_id: None,
        agent_name: Some("coder".to_owned()),
        needs: Vec::new(),
        outcomes: Vec::new(),
        terminal_tool_result: None,
    }
}

fn json_obj(pairs: &[(&str, serde_json::Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), v.clone()))
        .collect()
}

// AC-eos-db-01: request + task roundtrip, terminal no-op, upsert, CAS.
#[tokio::test]
async fn request_task_roundtrip() {
    let (_dir, db) = open_temp().await;
    let requests = db.requests();
    let tasks = db.tasks();
    let id = rid("req-1");

    requests
        .create_request(&id, "/work", None, "build the thing")
        .await
        .expect("create");
    let got = requests.get(&id).await.expect("get").expect("present");
    assert_eq!(got.cwd, "/work");
    assert_eq!(got.request_prompt, "build the thing");
    assert_eq!(got.status, RequestStatus::Running);

    let with_root = requests
        .set_root_task_id(&id, &tid("root-1"))
        .await
        .expect("set root");
    assert_eq!(with_root.root_task_id, Some(tid("root-1")));

    let done = requests
        .finish_request(&id, RequestStatus::Done)
        .await
        .expect("finish")
        .expect("some");
    assert_eq!(done.status, RequestStatus::Done);
    assert!(done.finished_at.is_some());
    // Terminal no-op: a second finish leaves the request unchanged.
    let again = requests
        .finish_request(&id, RequestStatus::Failed)
        .await
        .expect("finish2")
        .expect("some");
    assert_eq!(again.status, RequestStatus::Done);

    // upsert insert then full-field update.
    let t = sample_task("t-1", &id, "first");
    tasks.upsert_task(&t).await.expect("insert");
    assert_eq!(
        tasks
            .get(&t.id)
            .await
            .expect("get")
            .expect("present")
            .instruction,
        "first"
    );
    let mut t2 = t.clone();
    t2.instruction = "second".to_owned();
    tasks.upsert_task(&t2).await.expect("update");
    assert_eq!(
        tasks
            .get(&t.id)
            .await
            .expect("get")
            .expect("present")
            .instruction,
        "second"
    );

    // CAS: mismatch is a no-op; match flips.
    let miss = tasks
        .set_task_status_if_current(&t.id, TaskStatus::Running, TaskStatus::Done, None, None)
        .await
        .expect("cas");
    assert!(miss.is_none());
    let hit = tasks
        .set_task_status_if_current(&t.id, TaskStatus::Pending, TaskStatus::Running, None, None)
        .await
        .expect("cas")
        .expect("flipped");
    assert_eq!(hit.status, TaskStatus::Running);
}

// AC-eos-db-02: workflow roundtrip + the goal -> workflow_goal naming gap.
#[tokio::test]
async fn workflow_roundtrip_goal_mapping() {
    let (_dir, db) = open_temp().await;
    let requests = db.requests();
    let workflows = db.workflows();
    let id = rid("req-2");
    requests
        .create_request(&id, "/w", None, "p")
        .await
        .expect("create");

    let parent = tid("parent-1");
    let wf = workflows
        .insert(&id, &parent, "build the parser")
        .await
        .expect("insert");
    assert_eq!(wf.workflow_goal, "build the parser");
    assert!(wf.is_open());

    // The raw DB column is `goal`, the domain field is `workflow_goal`.
    let raw_goal: String =
        sqlx::query_scalar::<sqlx::Sqlite, String>("SELECT goal FROM workflows WHERE id = ?")
            .bind(wf.id.as_str())
            .fetch_one(db.pool())
            .await
            .expect("raw goal");
    assert_eq!(raw_goal, "build the parser");

    let it_id: eos_state::IterationId = "iter-x".parse().expect("iter id");
    let appended = workflows
        .append_iteration_id(&wf.id, &it_id)
        .await
        .expect("append");
    assert_eq!(appended.iteration_ids, vec![it_id]);

    let now = UtcDateTime::now();
    let closed = workflows
        .set_status(&wf.id, WorkflowStatus::Succeeded, Some(now), Some("[]"))
        .await
        .expect("set status");
    assert_eq!(closed.status, WorkflowStatus::Succeeded);
    assert_eq!(closed.outcomes.as_deref(), Some("[]"));
    assert!(closed.closed_at.is_some());

    let listed = workflows.list_for_parent_task(&parent).await.expect("list");
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].id, wf.id);
}

// AC-eos-db-03: iteration roundtrip, close_succeeded, deferred_goal naming, unique.
#[tokio::test]
async fn iteration_roundtrip() {
    let (_dir, db) = open_temp().await;
    let requests = db.requests();
    let workflows = db.workflows();
    let iterations = db.iterations();
    let id = rid("req-3");
    requests
        .create_request(&id, "/w", None, "p")
        .await
        .expect("create");
    let wf = workflows.insert(&id, &tid("p3"), "goal").await.expect("wf");

    let it = iterations
        .insert(
            &wf.id,
            0,
            IterationCreationReason::Initial,
            "iterate well",
            3,
        )
        .await
        .expect("insert");
    assert_eq!(it.iteration_goal, "iterate well");
    assert_eq!(it.status, IterationStatus::Open);
    assert_eq!(it.attempt_budget, 3);

    let deferred = iterations
        .set_deferred_goal_for_next_iteration(&it.id, Some("next time"))
        .await
        .expect("set deferred");
    assert_eq!(
        deferred.deferred_goal_for_next_iteration.as_deref(),
        Some("next time")
    );
    // Raw column is `deferred_goal`.
    let raw: Option<String> = sqlx::query_scalar::<sqlx::Sqlite, Option<String>>(
        "SELECT deferred_goal FROM iterations WHERE id = ?",
    )
    .bind(it.id.as_str())
    .fetch_one(db.pool())
    .await
    .expect("raw deferred");
    assert_eq!(raw.as_deref(), Some("next time"));

    let now = UtcDateTime::now();
    let closed = iterations
        .close_succeeded(&it.id, "[{\"x\":1}]", Some(now))
        .await
        .expect("close");
    assert_eq!(closed.status, IterationStatus::Succeeded);
    assert_eq!(closed.outcomes.as_deref(), Some("[{\"x\":1}]"));
    assert!(closed.closed_at.is_some());

    // Unique (workflow_id, sequence_no): a duplicate insert errors.
    assert!(iterations
        .insert(&wf.id, 0, IterationCreationReason::Initial, "dup", 1)
        .await
        .is_err());

    let listed = iterations.list_for_workflow(&wf.id).await.expect("list");
    assert_eq!(listed.len(), 1);
}

// AC-eos-db-04: attempt roundtrip, outcome parse, unique.
#[tokio::test]
async fn attempt_roundtrip() {
    let (_dir, db) = open_temp().await;
    let requests = db.requests();
    let workflows = db.workflows();
    let iterations = db.iterations();
    let attempts = db.attempts();
    let id = rid("req-4");
    requests
        .create_request(&id, "/w", None, "p")
        .await
        .expect("create");
    let wf = workflows.insert(&id, &tid("p4"), "goal").await.expect("wf");
    let it = iterations
        .insert(&wf.id, 0, IterationCreationReason::Initial, "g", 3)
        .await
        .expect("it");

    let att = attempts.insert(&it.id, &wf.id, 0).await.expect("insert");
    assert_eq!(att.stage, AttemptStage::Plan);
    assert_eq!(att.status, AttemptStatus::Running);
    assert!(att.outcomes.is_empty());

    attempts
        .set_planner_task_id(&att.id, &tid("planner-1"))
        .await
        .expect("planner");
    attempts
        .set_generator_task_ids(&att.id, &[tid("g1"), tid("g2")])
        .await
        .expect("gen");
    let with_red = attempts
        .set_reducer_task_ids(&att.id, &[tid("r1")])
        .await
        .expect("red");
    assert_eq!(with_red.generator_task_ids, vec![tid("g1"), tid("g2")]);
    assert_eq!(with_red.reducer_task_ids, vec![tid("r1")]);

    attempts
        .set_stage(&att.id, AttemptStage::Run)
        .await
        .expect("stage");

    let outcomes = vec![ExecutionTaskOutcome {
        status: eos_state::TaskOutcomeStatus::Failed,
        role: ExecutionRole::Generator,
        task_id: tid("g1"),
        outcome: "boom".to_owned(),
    }];
    let closed = attempts
        .close(
            &att.id,
            AttemptStatus::Failed,
            Some(AttemptFailReason::TaskFailed),
            Some(&outcomes),
            UtcDateTime::now(),
        )
        .await
        .expect("close");
    assert_eq!(closed.stage, AttemptStage::Closed);
    assert_eq!(closed.status, AttemptStatus::Failed);
    assert_eq!(closed.fail_reason, Some(AttemptFailReason::TaskFailed));
    assert_eq!(closed.outcomes, outcomes); // round-trips through normalization

    // Unique (iteration_id, attempt_sequence_no).
    assert!(attempts.insert(&it.id, &wf.id, 0).await.is_err());

    let listed = attempts.list_for_iteration(&it.id).await.expect("list");
    assert_eq!(listed.len(), 1);
}

// AC-eos-db-04b: agent-run roundtrip + unique task_id; null-preserving columns.
#[tokio::test]
async fn agent_run_roundtrip() {
    let (_dir, db) = open_temp().await;
    let requests = db.requests();
    let tasks = db.tasks();
    let agent_runs = db.agent_runs();
    let id = rid("req-5");
    requests
        .create_request(&id, "/w", None, "p")
        .await
        .expect("create");
    tasks
        .upsert_task(&sample_task("t-5", &id, "do"))
        .await
        .expect("task");

    let run_id: eos_state::AgentRunId = "run-1".parse().expect("run id");
    let initial = vec![json_obj(&[("role", serde_json::json!("user"))])];
    let created = agent_runs
        .create_run(&run_id, &tid("t-5"), "coder", Some(&initial))
        .await
        .expect("create run");
    assert_eq!(created.agent_name, "coder");
    assert_eq!(created.initial_messages, Some(initial));
    // Null-preserving: history/terminal stay None until finish.
    assert!(created.message_history.is_none());
    assert!(created.terminal_tool_result.is_none());
    assert_eq!(created.token_count, 0);

    let history = vec![json_obj(&[("role", serde_json::json!("assistant"))])];
    let ttr = json_obj(&[("ok", serde_json::json!(true))]);
    let finished = agent_runs
        .finish_run(&run_id, Some(&history), Some(&ttr), 42, None)
        .await
        .expect("finish")
        .expect("some");
    assert_eq!(finished.message_history, Some(history));
    assert_eq!(finished.terminal_tool_result, Some(ttr));
    assert_eq!(finished.token_count, 42);
    assert!(finished.finished_at.is_some());

    // Unique task_id: a second run for the same task errors.
    assert!(agent_runs
        .create_run(
            &"run-2".parse::<eos_state::AgentRunId>().expect("id"),
            &tid("t-5"),
            "coder",
            None
        )
        .await
        .is_err());
}

// AC-eos-db-05: migrations build the full schema with final column names + FKs on.
#[tokio::test]
async fn migrations_create_schema() {
    let (_dir, db) = open_temp().await;
    for table in [
        "requests",
        "tasks",
        "workflows",
        "iterations",
        "attempts",
        "agent_runs",
        "model_registrations",
    ] {
        let found: Option<String> = sqlx::query_scalar::<sqlx::Sqlite, String>(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        )
        .bind(table)
        .fetch_optional(db.pool())
        .await
        .expect("query master");
        assert_eq!(found.as_deref(), Some(table), "missing table {table}");
    }

    // Final (renamed) column names exist.
    for sql in [
        "SELECT instruction FROM tasks LIMIT 0",
        "SELECT request_id FROM tasks LIMIT 0",
        "SELECT outcomes FROM iterations LIMIT 0",
    ] {
        sqlx::query(sql).fetch_optional(db.pool()).await.expect(sql);
    }

    // Foreign keys are enforced.
    let fk: i64 = sqlx::query_scalar::<sqlx::Sqlite, i64>("PRAGMA foreign_keys")
        .fetch_one(db.pool())
        .await
        .expect("pragma fk");
    assert_eq!(fk, 1);
}

// AC-eos-db-08: composition root yields working stores; deleting a request
// cascades to its tasks and workflows.
#[tokio::test]
async fn composition_root_and_cascade() {
    let (_dir, db) = open_temp().await;
    let id = rid("req-8");
    db.requests()
        .create_request(&id, "/w", None, "p")
        .await
        .expect("create");
    db.tasks()
        .upsert_task(&sample_task("t-8", &id, "do"))
        .await
        .expect("task");
    let wf = db
        .workflows()
        .insert(&id, &tid("p8"), "goal")
        .await
        .expect("wf");

    // Sanity: rows are present before the cascade.
    assert!(db.tasks().get(&tid("t-8")).await.expect("get").is_some());
    assert!(db.workflows().get(&wf.id).await.expect("get").is_some());

    sqlx::query("DELETE FROM requests WHERE id = ?")
        .bind(id.as_str())
        .execute(db.pool())
        .await
        .expect("delete request");

    // FK ON DELETE CASCADE removed the child rows.
    assert!(db.tasks().get(&tid("t-8")).await.expect("get").is_none());
    assert!(db.workflows().get(&wf.id).await.expect("get").is_none());
}

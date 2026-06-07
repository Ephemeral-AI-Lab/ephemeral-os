//! Typed SQL row structs (`sqlx::FromRow`) and the row → `eos-state` DTO
//! mappers.
//!
//! The DB columns keep the short legacy names (`goal`, `deferred_goal`); the
//! domain DTOs use the normalized names (anchor §4). These mappers are the
//! single explicit bridge. Enum-backed TEXT columns are parsed here (not by
//! `FromRow`) so parse failures surface as [`DbError::InvalidEnum`].

use serde_json::Value as JsonValue;
use time::OffsetDateTime;

use eos_state::{
    present_status, AgentRun, Attempt, AttemptBudget, AttemptClosure, AttemptFailReason,
    AttemptStage, AttemptState, AttemptStatus, CoreError, DeferredGoal, ExecutionRole,
    ExecutionTaskOutcome, Iteration, MaterializedPlan, PlanDisposition, Request, RequestStatus,
    Task, TaskOutcomeStatus, UtcDateTime, Workflow, NO_OUTCOME,
};

use crate::error::DbError;
use crate::json_col;

// ---- typed rows (column names; sqlx-native field types) --------------------

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct RequestRow {
    pub id: String,
    pub cwd: String,
    pub sandbox_id: Option<String>,
    pub request_prompt: String,
    pub root_task_id: Option<String>,
    pub status: String,
    pub created_at: OffsetDateTime,
    pub updated_at: OffsetDateTime,
    pub finished_at: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct TaskRow {
    pub id: String,
    pub request_id: String,
    pub role: String,
    pub instruction: String,
    pub status: String,
    pub workflow_id: Option<String>,
    pub iteration_id: Option<String>,
    pub attempt_id: Option<String>,
    pub agent_name: Option<String>,
    pub needs: String,
    pub outcomes: String,
    pub terminal_tool_result: Option<String>,
    // `created_at`/`updated_at` columns exist but the `Task` DTO has no timestamp
    // fields (matching Rust); `FromRow` ignores the extra columns.
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct WorkflowRow {
    pub id: String,
    pub request_id: String,
    pub parent_task_id: String,
    pub goal: String,
    pub status: String,
    pub iteration_ids: String,
    pub outcomes: Option<String>,
    pub created_at: OffsetDateTime,
    pub updated_at: OffsetDateTime,
    pub closed_at: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct IterationRow {
    pub id: String,
    pub workflow_id: String,
    pub sequence_no: i64,
    pub creation_reason: String,
    pub goal: String,
    pub attempt_budget: i64,
    pub status: String,
    pub attempt_ids: String,
    pub deferred_goal: Option<String>,
    pub created_at: OffsetDateTime,
    pub updated_at: OffsetDateTime,
    pub closed_at: Option<OffsetDateTime>,
    pub outcomes: Option<String>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct AttemptRow {
    pub id: String,
    pub iteration_id: String,
    pub workflow_id: String,
    pub attempt_sequence_no: i64,
    pub stage: String,
    pub status: String,
    pub planner_task_id: Option<String>,
    pub generator_task_ids: String,
    pub reducer_task_ids: String,
    pub outcomes: String,
    pub deferred_goal: Option<String>,
    pub fail_reason: Option<String>,
    pub created_at: OffsetDateTime,
    pub updated_at: OffsetDateTime,
    pub closed_at: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub(crate) struct AgentRunRow {
    pub id: String,
    pub task_id: String,
    pub initial_messages: Option<String>,
    pub agent_name: String,
    pub message_history: Option<String>,
    pub terminal_tool_result: Option<String>,
    pub token_count: i64,
    pub error: Option<String>,
    pub created_at: OffsetDateTime,
    pub finished_at: Option<OffsetDateTime>,
}

// ---- parse helpers --------------------------------------------------------

fn parse_id<T>(field: &'static str, raw: &str) -> Result<T, DbError>
where
    T: std::str::FromStr<Err = CoreError>,
{
    raw.parse().map_err(|_| DbError::InvalidEnum {
        field,
        value: raw.to_owned(),
    })
}

fn opt_id<T>(field: &'static str, raw: Option<&str>) -> Result<Option<T>, DbError>
where
    T: std::str::FromStr<Err = CoreError>,
{
    raw.map(|s| parse_id(field, s)).transpose()
}

pub(crate) fn parse_enum<T: serde::de::DeserializeOwned>(
    field: &'static str,
    raw: &str,
) -> Result<T, DbError> {
    serde_json::from_value(JsonValue::String(raw.to_owned())).map_err(|_| DbError::InvalidEnum {
        field,
        value: raw.to_owned(),
    })
}

/// Serialize a `snake_case` enum to its wire string for binding. Infallible:
/// every status/stage/reason enum serializes to a JSON string (a true invariant).
pub(crate) fn enum_to_db<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .expect("status/stage/reason enums serialize to a json string")
}

// ---- outcome-record normalization (the eos-db parse boundary, §6.8) --------

fn execution_role(raw: Option<&str>) -> Option<ExecutionRole> {
    match raw {
        Some("generator") => Some(ExecutionRole::Generator),
        Some("reducer") => Some(ExecutionRole::Reducer),
        _ => None,
    }
}

/// `_normalize_status`: a *present* record status. `"success"` → success, every
/// other value (incl. `"done"`) → failed.
fn normalize_status(raw: Option<&str>) -> TaskOutcomeStatus {
    match raw.map(str::trim) {
        Some("success") => TaskOutcomeStatus::Success,
        _ => TaskOutcomeStatus::Failed,
    }
}

fn record_str<'a>(record: &'a serde_json::Map<String, JsonValue>, key: &str) -> Option<&'a str> {
    record.get(key).and_then(JsonValue::as_str)
}

/// Build one typed outcome from a raw record (Rust `_outcomes_from_record`).
/// `status` is resolved by the caller (the task/attempt columns fill a missing
/// status differently). A record whose task id is empty/unparseable is dropped
/// (the empty `TaskId` Rust would emit is unrepresentable, and our own
/// serializer never writes one).
fn outcome_from_record(
    record: &serde_json::Map<String, JsonValue>,
    status: TaskOutcomeStatus,
    fallback_role: Option<ExecutionRole>,
    fallback_task_id: &str,
) -> Option<ExecutionTaskOutcome> {
    let role = execution_role(record_str(record, "role"))
        .or(fallback_role)
        .unwrap_or(ExecutionRole::Generator);
    let task_id_raw = record_str(record, "task_id")
        .filter(|s| !s.is_empty())
        .unwrap_or(fallback_task_id);
    let task_id = task_id_raw.parse().ok()?;
    let outcome = record_str(record, "outcome")
        .filter(|s| !s.is_empty())
        .unwrap_or(NO_OUTCOME)
        .to_owned();
    Some(ExecutionTaskOutcome {
        status,
        role,
        task_id,
        outcome,
    })
}

/// Normalize a task row's `outcomes` (Rust `task_outcomes_from_row`): a
/// *missing* per-record status is filled from the task status via
/// `present_status` (`"done"` → success); a *present* status uses
/// `normalize_status` (`"done"` → failed). Role falls back to the task's role,
/// task id to the owning task id.
pub(crate) fn normalize_task_outcomes(
    records: &[JsonValue],
    task_status: &str,
    task_role: &str,
    owning_task_id: &str,
) -> Vec<ExecutionTaskOutcome> {
    let fallback_role = execution_role(Some(task_role));
    records
        .iter()
        .filter_map(JsonValue::as_object)
        .filter_map(|record| {
            let status = if record.contains_key("status") {
                normalize_status(record_str(record, "status"))
            } else {
                present_status(task_status)
            };
            outcome_from_record(record, status, fallback_role, owning_task_id)
        })
        .collect()
}

/// Normalize an attempt row's `outcomes` (Rust `parse_outcomes_record`): no
/// status fill (missing → failed), no role fallback (missing → generator), task
/// id from the record only.
pub(crate) fn normalize_attempt_outcomes(records: &[JsonValue]) -> Vec<ExecutionTaskOutcome> {
    records
        .iter()
        .filter_map(JsonValue::as_object)
        .filter_map(|record| {
            let status = normalize_status(record_str(record, "status"));
            outcome_from_record(record, status, None, "")
        })
        .collect()
}

// ---- row → DTO mappers ----------------------------------------------------

pub(crate) fn row_to_request(r: RequestRow) -> Result<Request, DbError> {
    Ok(Request {
        id: parse_id("requests.id", &r.id)?,
        cwd: r.cwd,
        sandbox_id: opt_id("requests.sandbox_id", r.sandbox_id.as_deref())?,
        request_prompt: r.request_prompt,
        root_task_id: opt_id("requests.root_task_id", r.root_task_id.as_deref())?,
        status: parse_enum::<RequestStatus>("requests.status", &r.status)?,
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
        finished_at: r.finished_at.map(UtcDateTime::from_offset),
    })
}

pub(crate) fn row_to_task(r: TaskRow) -> Result<Task, DbError> {
    let records = json_col::decode_default::<Vec<JsonValue>>(Some(&r.outcomes))?;
    let outcomes = normalize_task_outcomes(&records, &r.status, &r.role, &r.id);
    Ok(Task {
        id: parse_id("tasks.id", &r.id)?,
        request_id: parse_id("tasks.request_id", &r.request_id)?,
        role: parse_enum("tasks.role", &r.role)?,
        instruction: r.instruction,
        status: parse_enum("tasks.status", &r.status)?,
        workflow_id: opt_id("tasks.workflow_id", r.workflow_id.as_deref())?,
        iteration_id: opt_id("tasks.iteration_id", r.iteration_id.as_deref())?,
        attempt_id: opt_id("tasks.attempt_id", r.attempt_id.as_deref())?,
        agent_name: r.agent_name,
        needs: json_col::decode_default(Some(&r.needs))?,
        outcomes,
        terminal_tool_result: json_col::decode_opt(r.terminal_tool_result.as_deref())?,
    })
}

pub(crate) fn row_to_workflow(r: WorkflowRow) -> Result<Workflow, DbError> {
    Ok(Workflow {
        id: parse_id("workflows.id", &r.id)?,
        request_id: parse_id("workflows.request_id", &r.request_id)?,
        workflow_goal: r.goal, // §4 column `goal` → domain `workflow_goal`
        status: parse_enum("workflows.status", &r.status)?,
        iteration_ids: json_col::decode_default(Some(&r.iteration_ids))?,
        parent_task_id: parse_id("workflows.parent_task_id", &r.parent_task_id)?,
        outcomes: r.outcomes, // raw projection string, not decoded
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
        closed_at: r.closed_at.map(UtcDateTime::from_offset),
    })
}

pub(crate) fn row_to_iteration(r: IterationRow) -> Result<Iteration, DbError> {
    let attempt_budget =
        AttemptBudget::try_from_i64(r.attempt_budget).map_err(|_| DbError::InvalidEnum {
            field: "iterations.attempt_budget",
            value: r.attempt_budget.to_string(),
        })?;
    let deferred_goal = r
        .deferred_goal
        .map(DeferredGoal::new)
        .transpose()
        .map_err(|_| DbError::InvalidEnum {
            field: "iterations.deferred_goal",
            value: String::new(),
        })?;
    Ok(Iteration {
        id: parse_id("iterations.id", &r.id)?,
        workflow_id: parse_id("iterations.workflow_id", &r.workflow_id)?,
        sequence_no: r.sequence_no,
        creation_reason: parse_enum("iterations.creation_reason", &r.creation_reason)?,
        iteration_goal: r.goal, // §4 column `goal` → domain `iteration_goal`
        attempt_budget,
        status: parse_enum("iterations.status", &r.status)?,
        attempt_ids: json_col::decode_default(Some(&r.attempt_ids))?,
        deferred_goal_for_next_iteration: deferred_goal, // §4 rename
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
        closed_at: r.closed_at.map(UtcDateTime::from_offset),
        outcomes: r.outcomes,
    })
}

pub(crate) fn row_to_attempt(r: AttemptRow) -> Result<Attempt, DbError> {
    let records = json_col::decode_default::<Vec<JsonValue>>(Some(&r.outcomes))?;
    let outcomes = normalize_attempt_outcomes(&records);
    let stage = parse_enum::<AttemptStage>("attempts.stage", &r.stage)?;
    let status = parse_enum::<AttemptStatus>("attempts.status", &r.status)?;
    let planner_task_id = opt_id("attempts.planner_task_id", r.planner_task_id.as_deref())?;
    let generator_task_ids = json_col::decode_default(Some(&r.generator_task_ids))?;
    let reducer_task_ids = json_col::decode_default(Some(&r.reducer_task_ids))?;
    let fail_reason: Option<AttemptFailReason> = r
        .fail_reason
        .as_deref()
        .map(|s| parse_enum("attempts.fail_reason", s))
        .transpose()?;
    let deferred_goal = r
        .deferred_goal
        .map(DeferredGoal::new)
        .transpose()
        .map_err(|_| DbError::InvalidEnum {
            field: "attempts.deferred_goal",
            value: String::new(),
        })?;
    let closed_at = r.closed_at.map(UtcDateTime::from_offset);
    let state = attempt_state_from_columns(AttemptLifecycleColumns {
        stage,
        status,
        planner_task_id,
        generator_task_ids,
        reducer_task_ids,
        deferred_goal: deferred_goal.as_ref(),
        fail_reason,
        closed_at,
        outcomes,
    })?;
    Ok(Attempt {
        id: parse_id("attempts.id", &r.id)?,
        iteration_id: parse_id("attempts.iteration_id", &r.iteration_id)?,
        workflow_id: parse_id("attempts.workflow_id", &r.workflow_id)?,
        attempt_sequence_no: r.attempt_sequence_no,
        state,
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
    })
}

struct AttemptLifecycleColumns<'a> {
    stage: AttemptStage,
    status: AttemptStatus,
    planner_task_id: Option<eos_state::TaskId>,
    generator_task_ids: Vec<eos_state::TaskId>,
    reducer_task_ids: Vec<eos_state::TaskId>,
    deferred_goal: Option<&'a DeferredGoal>,
    fail_reason: Option<AttemptFailReason>,
    closed_at: Option<UtcDateTime>,
    outcomes: Vec<ExecutionTaskOutcome>,
}

fn attempt_state_from_columns(
    columns: AttemptLifecycleColumns<'_>,
) -> Result<AttemptState, DbError> {
    let AttemptLifecycleColumns {
        stage,
        status,
        planner_task_id,
        generator_task_ids,
        reducer_task_ids,
        deferred_goal,
        fail_reason,
        closed_at,
        outcomes,
    } = columns;
    let lifecycle_value = format!("{stage:?}/{status:?}");
    let invalid_lifecycle = || DbError::InvalidEnum {
        field: "attempts.lifecycle",
        value: lifecycle_value.clone(),
    };
    let has_plan_tasks = !generator_task_ids.is_empty() || !reducer_task_ids.is_empty();
    let plan = planner_task_id.clone().and_then(|planner_task_id| {
        has_plan_tasks.then(|| MaterializedPlan {
            planner_task_id,
            disposition: PlanDisposition::from_deferred_goal(deferred_goal.cloned()),
            generator_task_ids,
            reducer_task_ids,
        })
    });
    match stage {
        AttemptStage::Plan => {
            if status != AttemptStatus::Running
                || fail_reason.is_some()
                || closed_at.is_some()
                || has_plan_tasks
                || deferred_goal.is_some()
            {
                return Err(invalid_lifecycle());
            }
            Ok(AttemptState::Planning { planner_task_id })
        }
        AttemptStage::Run => {
            if status != AttemptStatus::Running || fail_reason.is_some() || closed_at.is_some() {
                return Err(invalid_lifecycle());
            }
            Ok(AttemptState::Running {
                plan: plan.ok_or_else(invalid_lifecycle)?,
            })
        }
        AttemptStage::Closed => {
            let closed_at = closed_at.ok_or_else(invalid_lifecycle)?;
            let closure = match status {
                AttemptStatus::Running => return Err(invalid_lifecycle()),
                AttemptStatus::Passed => {
                    if fail_reason.is_some() {
                        return Err(invalid_lifecycle());
                    }
                    AttemptClosure::Passed {
                        outcomes,
                        closed_at,
                    }
                }
                AttemptStatus::Failed => AttemptClosure::Failed {
                    reason: fail_reason.ok_or_else(invalid_lifecycle)?,
                    outcomes,
                    closed_at,
                },
            };
            let planner_task_id = if plan.is_some() {
                None
            } else {
                planner_task_id
            };
            Ok(AttemptState::Closed {
                closure,
                planner_task_id,
                plan,
            })
        }
    }
}

pub(crate) fn row_to_agent_run(r: AgentRunRow) -> Result<AgentRun, DbError> {
    Ok(AgentRun {
        id: parse_id("agent_runs.id", &r.id)?,
        task_id: parse_id("agent_runs.task_id", &r.task_id)?,
        initial_messages: json_col::decode_opt(r.initial_messages.as_deref())?,
        agent_name: r.agent_name,
        message_history: json_col::decode_opt(r.message_history.as_deref())?,
        terminal_tool_result: json_col::decode_opt(r.terminal_tool_result.as_deref())?,
        token_count: r.token_count,
        error: r.error,
        created_at: UtcDateTime::from_offset(r.created_at),
        finished_at: r.finished_at.map(UtcDateTime::from_offset),
    })
}

// Statically guard that the iteration outcome enums map to the same wire values
// Rust used (defends the `_normalize_status` vs `present_status` split).
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ids(o: &ExecutionTaskOutcome) -> (TaskOutcomeStatus, ExecutionRole, String, String) {
        (
            o.status,
            o.role,
            o.task_id.as_str().to_owned(),
            o.outcome.clone(),
        )
    }

    // The task-column normalizer (`task_outcomes_from_row`): present status uses
    // `_normalize_status` (`done` → failed); missing status is filled from the
    // task via `present_status` (`done` → success); role falls back to task role.
    #[test]
    fn task_outcomes_parity() {
        let records = json!([
            // present status "done" -> _normalize_status -> FAILED (not success!)
            { "status": "done", "role": "generator", "task_id": "g1", "outcome": "x" },
            // present status "success" -> success
            { "status": "success", "role": "reducer", "task_id": "r1", "outcome": "y" },
            // missing status -> present_status(task_status="done") -> SUCCESS
            { "role": "generator", "task_id": "g2", "outcome": "z" },
            // missing role -> fallback to task role (reducer); missing outcome -> default
            { "status": "success", "task_id": "r2" },
        ]);
        let recs: Vec<JsonValue> = records.as_array().expect("arr").clone();
        let got = normalize_task_outcomes(&recs, "done", "reducer", "owner");

        assert_eq!(
            got.iter().map(ids).collect::<Vec<_>>(),
            vec![
                (
                    TaskOutcomeStatus::Failed,
                    ExecutionRole::Generator,
                    "g1".to_owned(),
                    "x".to_owned()
                ),
                (
                    TaskOutcomeStatus::Success,
                    ExecutionRole::Reducer,
                    "r1".to_owned(),
                    "y".to_owned()
                ),
                (
                    TaskOutcomeStatus::Success,
                    ExecutionRole::Generator,
                    "g2".to_owned(),
                    "z".to_owned()
                ),
                (
                    TaskOutcomeStatus::Success,
                    ExecutionRole::Reducer,
                    "r2".to_owned(),
                    "(no outcome recorded)".to_owned()
                ),
            ]
        );
    }

    // The attempt-column normalizer (`parse_outcomes_record`): no status fill
    // (missing -> failed), no role fallback (missing/invalid -> generator).
    #[test]
    fn attempt_outcomes_parity() {
        let records = json!([
            { "status": "success", "role": "reducer", "task_id": "r1", "outcome": "ok" },
            // missing status -> FAILED; invalid role -> generator; missing outcome -> default
            { "role": "weird", "task_id": "g1" },
            // missing task_id -> dropped (unrepresentable empty TaskId)
            { "status": "success", "role": "generator", "outcome": "orphan" },
        ]);
        let recs: Vec<JsonValue> = records.as_array().expect("arr").clone();
        let got = normalize_attempt_outcomes(&recs);

        assert_eq!(
            got.iter().map(ids).collect::<Vec<_>>(),
            vec![
                (
                    TaskOutcomeStatus::Success,
                    ExecutionRole::Reducer,
                    "r1".to_owned(),
                    "ok".to_owned()
                ),
                (
                    TaskOutcomeStatus::Failed,
                    ExecutionRole::Generator,
                    "g1".to_owned(),
                    "(no outcome recorded)".to_owned()
                ),
            ]
        );
    }

    // Round-trip: a typed outcome serialized then re-normalized is identity.
    #[test]
    fn attempt_outcomes_roundtrip_typed() {
        let typed = vec![
            ExecutionTaskOutcome {
                status: TaskOutcomeStatus::Success,
                role: ExecutionRole::Reducer,
                task_id: "r1".parse().expect("id"),
                outcome: "done well".to_owned(),
            },
            ExecutionTaskOutcome {
                status: TaskOutcomeStatus::Failed,
                role: ExecutionRole::Generator,
                task_id: "g1".parse().expect("id"),
                outcome: "boom".to_owned(),
            },
        ];
        let encoded = json_col::encode(&typed).expect("encode");
        let recs = json_col::decode_default::<Vec<JsonValue>>(Some(&encoded)).expect("decode");
        assert_eq!(normalize_attempt_outcomes(&recs), typed);
    }

    // ---- attempt lifecycle reconstruction (`attempt_state_from_columns`) -----
    //
    // This is the home of the persisted Attempt-lifecycle invariant: it rejects
    // incoherent (stage, status, plan, closed_at, fail_reason, deferred_goal)
    // column combinations a corrupt or migration-skewed row could carry. Each
    // test pairs the *valid* combo (must reconstruct to the right state) with the
    // single-field deviations that must be rejected — so the suite would fail both
    // if a guard were weakened (deviation → Ok) and if reconstruction broke
    // (valid → Err).

    fn tid(raw: &str) -> eos_state::TaskId {
        raw.parse().expect("task id")
    }

    fn epoch() -> UtcDateTime {
        UtcDateTime::from_offset(OffsetDateTime::UNIX_EPOCH)
    }

    fn base_cols(
        stage: AttemptStage,
        status: AttemptStatus,
    ) -> AttemptLifecycleColumns<'static> {
        AttemptLifecycleColumns {
            stage,
            status,
            planner_task_id: None,
            generator_task_ids: Vec::new(),
            reducer_task_ids: Vec::new(),
            deferred_goal: None,
            fail_reason: None,
            closed_at: None,
            outcomes: Vec::new(),
        }
    }

    #[test]
    fn attempt_state_plan_reconstructs_and_rejects_incoherent_columns() {
        // Valid: PLAN/Running, no materialized plan, no terminal/deferred fields.
        assert!(matches!(
            attempt_state_from_columns(base_cols(AttemptStage::Plan, AttemptStatus::Running))
                .expect("bare plan reconstructs"),
            AttemptState::Planning {
                planner_task_id: None
            }
        ));
        let mut with_planner = base_cols(AttemptStage::Plan, AttemptStatus::Running);
        with_planner.planner_task_id = Some(tid("p1"));
        assert!(matches!(
            attempt_state_from_columns(with_planner).expect("plan with planner task"),
            AttemptState::Planning {
                planner_task_id: Some(_)
            }
        ));

        // Each single deviation is incoherent for PLAN.
        assert!(
            attempt_state_from_columns(base_cols(AttemptStage::Plan, AttemptStatus::Passed))
                .is_err(),
            "PLAN may only be Running"
        );
        let mut failed = base_cols(AttemptStage::Plan, AttemptStatus::Running);
        failed.fail_reason = Some(AttemptFailReason::TaskFailed);
        assert!(
            attempt_state_from_columns(failed).is_err(),
            "PLAN carries no fail_reason"
        );
        let mut closed = base_cols(AttemptStage::Plan, AttemptStatus::Running);
        closed.closed_at = Some(epoch());
        assert!(
            attempt_state_from_columns(closed).is_err(),
            "PLAN is never closed_at"
        );
        let mut with_tasks = base_cols(AttemptStage::Plan, AttemptStatus::Running);
        with_tasks.generator_task_ids = vec![tid("g1")];
        assert!(
            attempt_state_from_columns(with_tasks).is_err(),
            "PLAN has no materialized plan tasks"
        );
        let deferred = DeferredGoal::new("later").expect("deferred goal");
        let mut with_deferred = base_cols(AttemptStage::Plan, AttemptStatus::Running);
        with_deferred.deferred_goal = Some(&deferred);
        assert!(
            attempt_state_from_columns(with_deferred).is_err(),
            "PLAN carries no deferred goal"
        );
    }

    #[test]
    fn attempt_state_run_requires_materialized_plan_and_running_status() {
        let valid = || {
            let mut cols = base_cols(AttemptStage::Run, AttemptStatus::Running);
            cols.planner_task_id = Some(tid("p1"));
            cols.generator_task_ids = vec![tid("g1")];
            cols.reducer_task_ids = vec![tid("r1")];
            cols
        };
        assert!(matches!(
            attempt_state_from_columns(valid()).expect("run with plan reconstructs"),
            AttemptState::Running { .. }
        ));

        // No planner task -> no materialized plan -> reject.
        let mut no_planner = valid();
        no_planner.planner_task_id = None;
        assert!(
            attempt_state_from_columns(no_planner).is_err(),
            "RUN needs a planner task"
        );
        // No generator/reducer tasks -> no materialized plan -> reject.
        let mut no_tasks = valid();
        no_tasks.generator_task_ids = Vec::new();
        no_tasks.reducer_task_ids = Vec::new();
        assert!(
            attempt_state_from_columns(no_tasks).is_err(),
            "RUN needs materialized plan tasks"
        );
        // Terminal status / fields are incoherent for an open RUN.
        let mut passed = valid();
        passed.status = AttemptStatus::Passed;
        assert!(
            attempt_state_from_columns(passed).is_err(),
            "RUN may only be Running"
        );
        let mut failed = valid();
        failed.fail_reason = Some(AttemptFailReason::TaskFailed);
        assert!(
            attempt_state_from_columns(failed).is_err(),
            "open RUN carries no fail_reason"
        );
        let mut closed = valid();
        closed.closed_at = Some(epoch());
        assert!(
            attempt_state_from_columns(closed).is_err(),
            "open RUN is not closed_at"
        );
    }

    #[test]
    fn attempt_state_closed_passed_and_failed_reconstruct_with_required_fields() {
        // Passed closure (the success branch the store path never exercised):
        // closed_at present, no fail_reason.
        let mut passed = base_cols(AttemptStage::Closed, AttemptStatus::Passed);
        passed.closed_at = Some(epoch());
        assert!(matches!(
            attempt_state_from_columns(passed).expect("passed closure reconstructs"),
            AttemptState::Closed {
                closure: AttemptClosure::Passed { .. },
                ..
            }
        ));

        // Failed closure: closed_at + fail_reason both present.
        let mut failed = base_cols(AttemptStage::Closed, AttemptStatus::Failed);
        failed.closed_at = Some(epoch());
        failed.fail_reason = Some(AttemptFailReason::TaskFailed);
        assert!(matches!(
            attempt_state_from_columns(failed).expect("failed closure reconstructs"),
            AttemptState::Closed {
                closure: AttemptClosure::Failed {
                    reason: AttemptFailReason::TaskFailed,
                    ..
                },
                ..
            }
        ));

        // A Closed attempt without closed_at is incoherent.
        assert!(
            attempt_state_from_columns(base_cols(AttemptStage::Closed, AttemptStatus::Passed))
                .is_err(),
            "CLOSED requires closed_at"
        );
        // Closed stage with a still-Running status is incoherent.
        let mut running = base_cols(AttemptStage::Closed, AttemptStatus::Running);
        running.closed_at = Some(epoch());
        assert!(
            attempt_state_from_columns(running).is_err(),
            "CLOSED is never Running"
        );
        // Passed must not carry a fail_reason.
        let mut passed_with_reason = base_cols(AttemptStage::Closed, AttemptStatus::Passed);
        passed_with_reason.closed_at = Some(epoch());
        passed_with_reason.fail_reason = Some(AttemptFailReason::TaskFailed);
        assert!(
            attempt_state_from_columns(passed_with_reason).is_err(),
            "Passed closure carries no fail_reason"
        );
        // Failed must carry a fail_reason.
        let mut failed_no_reason = base_cols(AttemptStage::Closed, AttemptStatus::Failed);
        failed_no_reason.closed_at = Some(epoch());
        assert!(
            attempt_state_from_columns(failed_no_reason).is_err(),
            "Failed closure requires a fail_reason"
        );
    }
}

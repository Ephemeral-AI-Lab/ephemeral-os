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
    present_status, AgentRun, Attempt, AttemptFailReason, AttemptStage, AttemptStatus, CoreError,
    ExecutionRole, ExecutionTaskOutcome, Iteration, Request, Task, TaskOutcomeStatus, UtcDateTime,
    Workflow,
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
    // fields (matching Python); `FromRow` ignores the extra columns.
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

fn parse_enum<T: serde::de::DeserializeOwned>(field: &'static str, raw: &str) -> Result<T, DbError> {
    serde_json::from_value(JsonValue::String(raw.to_owned())).map_err(|_| DbError::InvalidEnum {
        field,
        value: raw.to_owned(),
    })
}

/// Serialize a snake_case enum to its wire string for binding. Infallible: every
/// status/stage/reason enum serializes to a JSON string (a true invariant).
pub(crate) fn enum_to_db<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .expect("status/stage/reason enums serialize to a json string")
}

// ---- outcome-record normalization (the eos-db parse boundary, §6.8) --------

const NO_OUTCOME: &str = "(no outcome recorded)";

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

/// Build one typed outcome from a raw record (Python `_outcomes_from_record`).
/// `status` is resolved by the caller (the task/attempt columns fill a missing
/// status differently). A record whose task id is empty/unparseable is dropped
/// (the empty `TaskId` Python would emit is unrepresentable, and our own
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

/// Normalize a task row's `outcomes` (Python `task_outcomes_from_row`): a
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

/// Normalize an attempt row's `outcomes` (Python `parse_outcomes_record`): no
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
        status: r.status,
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
    Ok(Iteration {
        id: parse_id("iterations.id", &r.id)?,
        workflow_id: parse_id("iterations.workflow_id", &r.workflow_id)?,
        sequence_no: r.sequence_no,
        creation_reason: parse_enum("iterations.creation_reason", &r.creation_reason)?,
        iteration_goal: r.goal, // §4 column `goal` → domain `iteration_goal`
        attempt_budget: r.attempt_budget,
        status: parse_enum("iterations.status", &r.status)?,
        attempt_ids: json_col::decode_default(Some(&r.attempt_ids))?,
        deferred_goal_for_next_iteration: r.deferred_goal, // §4 rename
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
        closed_at: r.closed_at.map(UtcDateTime::from_offset),
        outcomes: r.outcomes,
    })
}

pub(crate) fn row_to_attempt(r: AttemptRow) -> Result<Attempt, DbError> {
    let records = json_col::decode_default::<Vec<JsonValue>>(Some(&r.outcomes))?;
    let outcomes = normalize_attempt_outcomes(&records);
    let fail_reason: Option<AttemptFailReason> = r
        .fail_reason
        .as_deref()
        .map(|s| parse_enum("attempts.fail_reason", s))
        .transpose()?;
    Ok(Attempt {
        id: parse_id("attempts.id", &r.id)?,
        iteration_id: parse_id("attempts.iteration_id", &r.iteration_id)?,
        workflow_id: parse_id("attempts.workflow_id", &r.workflow_id)?,
        attempt_sequence_no: r.attempt_sequence_no,
        stage: parse_enum::<AttemptStage>("attempts.stage", &r.stage)?,
        status: parse_enum::<AttemptStatus>("attempts.status", &r.status)?,
        planner_task_id: opt_id("attempts.planner_task_id", r.planner_task_id.as_deref())?,
        generator_task_ids: json_col::decode_default(Some(&r.generator_task_ids))?,
        reducer_task_ids: json_col::decode_default(Some(&r.reducer_task_ids))?,
        deferred_goal_for_next_iteration: r.deferred_goal,
        fail_reason,
        created_at: UtcDateTime::from_offset(r.created_at),
        updated_at: UtcDateTime::from_offset(r.updated_at),
        closed_at: r.closed_at.map(UtcDateTime::from_offset),
        outcomes,
    })
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
// Python used (defends the `_normalize_status` vs `present_status` split).
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
                (TaskOutcomeStatus::Failed, ExecutionRole::Generator, "g1".to_owned(), "x".to_owned()),
                (TaskOutcomeStatus::Success, ExecutionRole::Reducer, "r1".to_owned(), "y".to_owned()),
                (TaskOutcomeStatus::Success, ExecutionRole::Generator, "g2".to_owned(), "z".to_owned()),
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
                (TaskOutcomeStatus::Success, ExecutionRole::Reducer, "r1".to_owned(), "ok".to_owned()),
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
}

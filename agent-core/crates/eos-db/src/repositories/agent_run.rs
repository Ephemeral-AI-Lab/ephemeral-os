//! `SqlAgentRunStore` — flat agent-run repository.

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_types::{
    format_record_dir, AgentName, AgentRun, AgentRunId, AgentRunRecordIndex,
    AgentRunRecordTarget, AgentRunStore, CoreError, CreatedAgentRun, RequestId,
    RunningRequestAgentRun, Sealed, TaskOutcome, TaskStatus, ToolUseId, AgentType,
};

use crate::error::DbError;
use crate::json_col;
use crate::rows::{enum_to_db, parse_enum, parse_id};

/// `SQLite` repository for flat agent-run rows.
#[derive(Debug)]
pub struct SqlAgentRunStore {
    pool: SqlitePool,
}

impl SqlAgentRunStore {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }
}

impl Sealed for SqlAgentRunStore {}

#[async_trait]
impl AgentRunStore for SqlAgentRunStore {
    async fn create_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        request_id: &RequestId,
        agent_name: &AgentName,
        agent_type: AgentType,
        parent_agent_run_id: Option<&AgentRunId>,
        tool_use_id: Option<&ToolUseId>,
    ) -> Result<CreatedAgentRun, CoreError> {
        let now = OffsetDateTime::now_utc();

        sqlx::query(
            "INSERT INTO agent_runs \
             (agent_run_id, request_id, agent_type, status, agent_name, parent_agent_run_id, \
              tool_use_id, terminal_payload, task_outcome, token_count, error, created_at, \
              updated_at, finished_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, ?, ?, NULL)",
        )
        .bind(agent_run_id.as_str())
        .bind(request_id.as_str())
        .bind(enum_to_db(&agent_type))
        .bind(enum_to_db(&TaskStatus::Running))
        .bind(agent_name.as_str())
        .bind(parent_agent_run_id.map(AgentRunId::as_str))
        .bind(tool_use_id.map(ToolUseId::as_str))
        .bind(now)
        .bind(now)
        .execute(&self.pool)
        .await
        .map_err(DbError::from)?;

        Ok(created_from_index(&AgentRunRecordIndex {
            request_id: request_id.clone(),
            agent_run_id: agent_run_id.clone(),
        }))
    }

    async fn finish_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        status: TaskStatus,
        terminal_payload: Option<&eos_types::JsonObject>,
        task_outcome: Option<&TaskOutcome>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<Option<AgentRun>, CoreError> {
        let now = OffsetDateTime::now_utc();
        let terminal = terminal_payload.map(json_col::encode).transpose()?;
        let outcome = task_outcome.map(json_col::encode).transpose()?;
        let row = sqlx::query_as::<Sqlite, AgentRunRow>(
            "UPDATE agent_runs SET status = ?, terminal_payload = COALESCE(?, terminal_payload), \
             task_outcome = COALESCE(?, task_outcome), token_count = ?, error = ?, \
             updated_at = ?, finished_at = ? WHERE agent_run_id = ? RETURNING *",
        )
        .bind(enum_to_db(&status))
        .bind(terminal)
        .bind(outcome)
        .bind(token_count)
        .bind(error)
        .bind(now)
        .bind(now)
        .bind(agent_run_id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        row.map(row_to_agent_run).transpose().map_err(Into::into)
    }

    async fn record_index_for_agent_run(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunRecordIndex>, CoreError> {
        let row = sqlx::query_as::<Sqlite, AgentRunRecordIndexRow>(
            "SELECT request_id, agent_run_id FROM agent_runs WHERE agent_run_id = ?",
        )
        .bind(agent_run_id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        row.map(row_to_record_index).transpose().map_err(Into::into)
    }

    async fn get_agent_run(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRun>, CoreError> {
        let row =
            sqlx::query_as::<Sqlite, AgentRunRow>("SELECT * FROM agent_runs WHERE agent_run_id = ?")
                .bind(agent_run_id.as_str())
                .fetch_optional(&self.pool)
                .await
                .map_err(DbError::from)?;
        row.map(row_to_agent_run).transpose().map_err(Into::into)
    }

    async fn list_agent_runs_for_request(
        &self,
        request_id: &RequestId,
    ) -> Result<Vec<AgentRun>, CoreError> {
        let rows = sqlx::query_as::<Sqlite, AgentRunRow>(
            "SELECT * FROM agent_runs WHERE request_id = ? ORDER BY created_at ASC, agent_run_id ASC",
        )
        .bind(request_id.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(DbError::from)?;
        rows.into_iter()
            .map(row_to_agent_run)
            .collect::<Result<Vec<_>, DbError>>()
            .map_err(Into::into)
    }

    async fn list_running_agent_runs_for_request(
        &self,
        request_id: &RequestId,
    ) -> Result<Vec<RunningRequestAgentRun>, CoreError> {
        let rows = sqlx::query_as::<Sqlite, RunningRequestAgentRunRow>(
            "SELECT request_id, agent_run_id, status FROM agent_runs \
             WHERE request_id = ? AND status = 'running' ORDER BY agent_run_id ASC",
        )
        .bind(request_id.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(DbError::from)?;
        rows.iter()
            .map(row_to_running_request_agent_run)
            .collect::<Result<Vec<_>, DbError>>()
            .map_err(Into::into)
    }

    async fn list_child_agent_runs_for_parent_agent_run(
        &self,
        parent_agent_run_id: &AgentRunId,
        agent_type: Option<AgentType>,
    ) -> Result<Vec<AgentRun>, CoreError> {
        let rows = match agent_type {
            Some(agent_type) => {
                sqlx::query_as::<Sqlite, AgentRunRow>(
                    "SELECT * FROM agent_runs WHERE parent_agent_run_id = ? AND agent_type = ? \
                     ORDER BY created_at ASC, agent_run_id ASC",
                )
                .bind(parent_agent_run_id.as_str())
                .bind(enum_to_db(&agent_type))
                .fetch_all(&self.pool)
                .await
            }
            None => {
                sqlx::query_as::<Sqlite, AgentRunRow>(
                    "SELECT * FROM agent_runs WHERE parent_agent_run_id = ? \
                     ORDER BY created_at ASC, agent_run_id ASC",
                )
                .bind(parent_agent_run_id.as_str())
                .fetch_all(&self.pool)
                .await
            }
        }
        .map_err(DbError::from)?;
        rows.into_iter()
            .map(row_to_agent_run)
            .collect::<Result<Vec<_>, DbError>>()
            .map_err(Into::into)
    }
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct AgentRunRow {
    agent_run_id: String,
    request_id: String,
    agent_type: String,
    status: String,
    agent_name: String,
    parent_agent_run_id: Option<String>,
    tool_use_id: Option<String>,
    terminal_payload: Option<String>,
    task_outcome: Option<String>,
    token_count: i64,
    error: Option<String>,
    created_at: OffsetDateTime,
    updated_at: OffsetDateTime,
    finished_at: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct AgentRunRecordIndexRow {
    request_id: String,
    agent_run_id: String,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct RunningRequestAgentRunRow {
    request_id: String,
    agent_run_id: String,
    status: String,
}

fn created_from_index(index: &AgentRunRecordIndex) -> CreatedAgentRun {
    CreatedAgentRun {
        agent_run_id: index.agent_run_id.clone(),
        record_target: AgentRunRecordTarget {
            request_id: index.request_id.clone(),
            agent_run_id: index.agent_run_id.clone(),
            record_dir: format_record_dir(index),
        },
    }
}

fn row_to_record_index(row: AgentRunRecordIndexRow) -> Result<AgentRunRecordIndex, DbError> {
    Ok(AgentRunRecordIndex {
        request_id: parse_id("agent_runs.request_id", &row.request_id)?,
        agent_run_id: parse_id("agent_runs.agent_run_id", &row.agent_run_id)?,
    })
}

fn row_to_running_request_agent_run(
    row: &RunningRequestAgentRunRow,
) -> Result<RunningRequestAgentRun, DbError> {
    Ok(RunningRequestAgentRun {
        request_id: parse_id("running_request_agent_runs.request_id", &row.request_id)?,
        agent_run_id: parse_id("running_request_agent_runs.agent_run_id", &row.agent_run_id)?,
        status: parse_enum("running_request_agent_runs.status", &row.status)?,
    })
}

fn row_to_agent_run(row: AgentRunRow) -> Result<AgentRun, DbError> {
    Ok(AgentRun {
        agent_run_id: parse_id("agent_runs.agent_run_id", &row.agent_run_id)?,
        request_id: parse_id("agent_runs.request_id", &row.request_id)?,
        agent_type: parse_enum("agent_runs.agent_type", &row.agent_type)?,
        status: parse_enum("agent_runs.status", &row.status)?,
        agent_name: AgentName::new(&row.agent_name).map_err(|_| DbError::InvalidEnum {
            field: "agent_runs.agent_name",
            value: row.agent_name.clone(),
        })?,
        parent_agent_run_id: opt_parsed_id(
            "agent_runs.parent_agent_run_id",
            row.parent_agent_run_id.as_deref(),
        )?,
        tool_use_id: opt_parsed_id("agent_runs.tool_use_id", row.tool_use_id.as_deref())?,
        terminal_payload: json_col::decode_opt(row.terminal_payload.as_deref())?,
        task_outcome: json_col::decode_opt(row.task_outcome.as_deref())?,
        token_count: row.token_count,
        error: row.error,
        created_at: eos_types::UtcDateTime::from_offset(row.created_at),
        updated_at: eos_types::UtcDateTime::from_offset(row.updated_at),
        finished_at: row.finished_at.map(eos_types::UtcDateTime::from_offset),
    })
}

fn opt_parsed_id<T>(field: &'static str, raw: Option<&str>) -> Result<Option<T>, DbError>
where
    T: std::str::FromStr<Err = CoreError>,
{
    raw.map(|value| parse_id(field, value)).transpose()
}

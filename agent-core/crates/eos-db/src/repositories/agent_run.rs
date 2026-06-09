//! `SqlAgentRunStore` — the agent-run repository (Rust `agent_run_store.py`).
//!
//! Two-phase: `create_run` sets only the create-time fields; the nullable JSON
//! columns stay NULL until `finish_run` writes them (null-preserving).

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_types::{AgentRun, AgentRunId, AgentRunStore, CoreError, JsonObject, Sealed, TaskId};

use crate::error::DbError;
use crate::rows::{row_to_agent_run, AgentRunRow};

/// `SQLite` repository for agent runs.
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
    async fn create_run(
        &self,
        agent_run_id: &AgentRunId,
        task_id: Option<&TaskId>,
        agent_name: &str,
    ) -> Result<AgentRun, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AgentRunRow>(
            "INSERT INTO agent_runs \
             (id, task_id, agent_name, terminal_payload, token_count, error, created_at, \
              finished_at) \
             VALUES (?, ?, ?, NULL, 0, NULL, ?, NULL) RETURNING *",
        )
        .bind(agent_run_id.as_str())
        .bind(task_id.map(TaskId::as_str))
        .bind(agent_name)
        .bind(now)
        .fetch_one(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_agent_run(row)?)
    }

    async fn finish_run(
        &self,
        agent_run_id: &AgentRunId,
        terminal_payload: Option<&JsonObject>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<Option<AgentRun>, CoreError> {
        let now = OffsetDateTime::now_utc();
        let terminal = terminal_payload.map(crate::json_col::encode).transpose()?;
        let row = sqlx::query_as::<Sqlite, AgentRunRow>(
            "UPDATE agent_runs SET terminal_payload = ?, \
               token_count = ?, error = ?, finished_at = ? WHERE id = ? RETURNING *",
        )
        .bind(terminal)
        .bind(token_count)
        .bind(error)
        .bind(now)
        .bind(agent_run_id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row.map(row_to_agent_run).transpose()?)
    }

    async fn get(&self, agent_run_id: &AgentRunId) -> Result<Option<AgentRun>, CoreError> {
        let row = sqlx::query_as::<Sqlite, AgentRunRow>("SELECT * FROM agent_runs WHERE id = ?")
            .bind(agent_run_id.as_str())
            .fetch_optional(&self.pool)
            .await
            .map_err(DbError::from)?;
        Ok(row.map(row_to_agent_run).transpose()?)
    }

    async fn get_for_task(&self, task_id: &TaskId) -> Result<Option<AgentRun>, CoreError> {
        let row = sqlx::query_as::<Sqlite, AgentRunRow>(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
        )
        .bind(task_id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row.map(row_to_agent_run).transpose()?)
    }
}

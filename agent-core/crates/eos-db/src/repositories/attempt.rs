//! `SqlAttemptStore` — the attempt repository (Python `attempt_store.py`).

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_state::{
    Attempt, AttemptFailReason, AttemptId, AttemptStage, AttemptStatus, AttemptStore, CoreError,
    ExecutionTaskOutcome, IterationId, Sealed, TaskId, UtcDateTime, WorkflowId,
};

use crate::error::DbError;
use crate::json_col;
use crate::rows::{enum_to_db, row_to_attempt, AttemptRow};

/// `SQLite` repository for attempts. Returns frozen `Attempt` DTOs.
#[derive(Debug)]
pub struct SqlAttemptStore {
    pool: SqlitePool,
}

impl SqlAttemptStore {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    fn not_found(id: &AttemptId) -> DbError {
        DbError::NotFound {
            table: "attempts",
            id: id.to_string(),
        }
    }
}

impl Sealed for SqlAttemptStore {}

#[async_trait]
impl AttemptStore for SqlAttemptStore {
    async fn insert(
        &self,
        iteration_id: &IterationId,
        workflow_id: &WorkflowId,
        attempt_sequence_no: i64,
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "INSERT INTO attempts \
             (id, iteration_id, workflow_id, attempt_sequence_no, stage, status, planner_task_id, \
              generator_task_ids, reducer_task_ids, outcomes, deferred_goal, fail_reason, \
              created_at, updated_at, closed_at) \
             VALUES (?, ?, ?, ?, 'plan', 'running', NULL, '[]', '[]', '[]', NULL, NULL, ?, ?, NULL) \
             RETURNING *",
        )
        .bind(AttemptId::new_v4().as_str())
        .bind(iteration_id.as_str())
        .bind(workflow_id.as_str())
        .bind(attempt_sequence_no)
        .bind(now)
        .bind(now)
        .fetch_one(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row)?)
    }

    async fn get(&self, id: &AttemptId) -> Result<Option<Attempt>, CoreError> {
        let row = sqlx::query_as::<Sqlite, AttemptRow>("SELECT * FROM attempts WHERE id = ?")
            .bind(id.as_str())
            .fetch_optional(&self.pool)
            .await
            .map_err(DbError::from)?;
        Ok(row.map(row_to_attempt).transpose()?)
    }

    async fn set_stage(&self, id: &AttemptId, stage: AttemptStage) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET stage = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&stage))
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn set_planner_task_id(
        &self,
        id: &AttemptId,
        planner_task_id: &TaskId,
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET planner_task_id = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(planner_task_id.as_str())
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn set_generator_task_ids(
        &self,
        id: &AttemptId,
        generator_task_ids: &[TaskId],
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET generator_task_ids = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(json_col::encode(generator_task_ids)?)
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn set_reducer_task_ids(
        &self,
        id: &AttemptId,
        reducer_task_ids: &[TaskId],
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET reducer_task_ids = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(json_col::encode(reducer_task_ids)?)
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn set_deferred_goal(
        &self,
        id: &AttemptId,
        deferred_goal_for_next_iteration: Option<&str>,
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET deferred_goal = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(deferred_goal_for_next_iteration)
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn close(
        &self,
        id: &AttemptId,
        status: AttemptStatus,
        fail_reason: Option<AttemptFailReason>,
        outcomes: Option<&[ExecutionTaskOutcome]>,
        closed_at: UtcDateTime,
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let outcomes_json = outcomes.map(json_col::encode).transpose()?;
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET stage = 'closed', status = ?, fail_reason = ?, \
               outcomes = COALESCE(?, outcomes), closed_at = ?, updated_at = ? \
             WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&status))
        .bind(fail_reason.as_ref().map(enum_to_db))
        .bind(outcomes_json)
        .bind(closed_at.into_inner())
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn list_for_iteration(
        &self,
        iteration_id: &IterationId,
    ) -> Result<Vec<Attempt>, CoreError> {
        let rows = sqlx::query_as::<Sqlite, AttemptRow>(
            "SELECT * FROM attempts WHERE iteration_id = ? ORDER BY attempt_sequence_no ASC",
        )
        .bind(iteration_id.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(rows
            .into_iter()
            .map(row_to_attempt)
            .collect::<Result<Vec<_>, _>>()?)
    }
}

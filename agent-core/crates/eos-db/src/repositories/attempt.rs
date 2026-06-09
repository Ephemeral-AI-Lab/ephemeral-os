//! `SqlAttemptStore` — the attempt repository (Rust `attempt_store.py`).

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_types::{
    Attempt, AttemptClosure, AttemptId, AttemptStage, AttemptStore, CoreError, IterationId,
    MaterializedPlan, RequestId, Sealed, TaskId, WorkflowId,
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

    async fn record_planner_task(
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

    async fn record_plan(
        &self,
        id: &AttemptId,
        plan: &MaterializedPlan,
    ) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET stage = ?, planner_task_id = ?, generator_task_ids = ?, \
               reducer_task_ids = ?, deferred_goal = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&AttemptStage::Run))
        .bind(plan.planner_task_id.as_str())
        .bind(json_col::encode(&plan.generator_task_ids)?)
        .bind(json_col::encode(&plan.reducer_task_ids)?)
        .bind(plan.deferred_goal().map(eos_types::DeferredGoal::as_str))
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_attempt(row.ok_or_else(|| Self::not_found(id))?)?)
    }

    async fn close(&self, id: &AttemptId, closure: AttemptClosure) -> Result<Attempt, CoreError> {
        let now = OffsetDateTime::now_utc();
        let outcomes_json = json_col::encode(closure.outcomes())?;
        let row = sqlx::query_as::<Sqlite, AttemptRow>(
            "UPDATE attempts SET stage = 'closed', status = ?, fail_reason = ?, \
               outcomes = ?, closed_at = ?, updated_at = ? \
             WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&closure.status()))
        .bind(closure.fail_reason().as_ref().map(enum_to_db))
        .bind(outcomes_json)
        .bind(closure.closed_at().into_inner())
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

    async fn cancel_open_attempts_for_request(
        &self,
        request_id: &RequestId,
        _reason: &str,
    ) -> Result<usize, CoreError> {
        let now = OffsetDateTime::now_utc();
        let updated = sqlx::query(
            "UPDATE attempts SET stage = 'closed', status = 'cancelled', outcomes = '[]', \
             fail_reason = NULL, closed_at = COALESCE(closed_at, ?), updated_at = ? \
             WHERE status = 'running' AND workflow_id IN \
             (SELECT id FROM workflows WHERE request_id = ?)",
        )
        .bind(now)
        .bind(now)
        .bind(request_id.as_str())
        .execute(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(updated.rows_affected() as usize)
    }
}

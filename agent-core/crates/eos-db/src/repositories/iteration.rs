//! `SqlIterationStore` — the iteration repository (Rust `iteration_store.py`).

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_types::{
    AttemptBudget, AttemptId, CoreError, DeferredGoal, Iteration, IterationCreationReason,
    IterationId, IterationStatus, IterationStore, Sealed, UtcDateTime, WorkflowId,
};

use crate::error::DbError;
use crate::rows::{enum_to_db, row_to_iteration, IterationRow};

/// `SQLite` repository for iterations. Returns frozen `Iteration` DTOs.
#[derive(Debug)]
pub struct SqlIterationStore {
    pool: SqlitePool,
}

impl SqlIterationStore {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }
}

impl Sealed for SqlIterationStore {}

#[async_trait]
impl IterationStore for SqlIterationStore {
    async fn insert(
        &self,
        workflow_id: &WorkflowId,
        sequence_no: i64,
        creation_reason: IterationCreationReason,
        iteration_goal: &str,
        attempt_budget: AttemptBudget,
    ) -> Result<Iteration, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, IterationRow>(
            "INSERT INTO iterations \
             (id, workflow_id, sequence_no, creation_reason, goal, attempt_budget, status, \
              attempt_ids, deferred_goal, created_at, updated_at, closed_at, outcomes) \
             VALUES (?, ?, ?, ?, ?, ?, 'open', '[]', NULL, ?, ?, NULL, NULL) RETURNING *",
        )
        .bind(IterationId::new_v4().as_str())
        .bind(workflow_id.as_str())
        .bind(sequence_no)
        .bind(enum_to_db(&creation_reason))
        .bind(iteration_goal)
        .bind(attempt_budget.as_i64())
        .bind(now)
        .bind(now)
        .fetch_one(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row_to_iteration(row)?)
    }

    async fn get(&self, id: &IterationId) -> Result<Option<Iteration>, CoreError> {
        let row = sqlx::query_as::<Sqlite, IterationRow>("SELECT * FROM iterations WHERE id = ?")
            .bind(id.as_str())
            .fetch_optional(&self.pool)
            .await
            .map_err(DbError::from)?;
        Ok(row.map(row_to_iteration).transpose()?)
    }

    async fn append_attempt_id(
        &self,
        id: &IterationId,
        attempt_id: &AttemptId,
    ) -> Result<Iteration, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, IterationRow>(
            "UPDATE iterations \
             SET attempt_ids = json_insert(COALESCE(attempt_ids, '[]'), '$[#]', ?), \
                 updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(attempt_id.as_str())
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        let row = row.ok_or_else(|| DbError::NotFound {
            table: "iterations",
            id: id.to_string(),
        })?;
        Ok(row_to_iteration(row)?)
    }

    async fn set_status(
        &self,
        id: &IterationId,
        status: IterationStatus,
        closed_at: Option<UtcDateTime>,
        outcomes: Option<&str>,
    ) -> Result<Iteration, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, IterationRow>(
            "UPDATE iterations SET status = ?, \
               closed_at = COALESCE(?, closed_at), \
               outcomes = COALESCE(?, outcomes), \
               updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&status))
        .bind(closed_at.map(UtcDateTime::into_inner))
        .bind(outcomes)
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        let row = row.ok_or_else(|| DbError::NotFound {
            table: "iterations",
            id: id.to_string(),
        })?;
        Ok(row_to_iteration(row)?)
    }

    async fn set_deferred_goal_for_next_iteration(
        &self,
        id: &IterationId,
        deferred_goal_for_next_iteration: Option<&DeferredGoal>,
    ) -> Result<Iteration, CoreError> {
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, IterationRow>(
            "UPDATE iterations SET deferred_goal = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(deferred_goal_for_next_iteration.map(DeferredGoal::as_str))
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        let row = row.ok_or_else(|| DbError::NotFound {
            table: "iterations",
            id: id.to_string(),
        })?;
        Ok(row_to_iteration(row)?)
    }

    async fn close_succeeded(
        &self,
        id: &IterationId,
        outcomes: &str,
        closed_at: Option<UtcDateTime>,
    ) -> Result<Iteration, CoreError> {
        // Single UPDATE => status + outcomes are written atomically (a crash
        // leaves the row untouched), preserving the close_succeeded invariant.
        let now = OffsetDateTime::now_utc();
        let row = sqlx::query_as::<Sqlite, IterationRow>(
            "UPDATE iterations SET status = 'succeeded', outcomes = ?, \
               closed_at = COALESCE(?, closed_at), updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(outcomes)
        .bind(closed_at.map(UtcDateTime::into_inner))
        .bind(now)
        .bind(id.as_str())
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        let row = row.ok_or_else(|| DbError::NotFound {
            table: "iterations",
            id: id.to_string(),
        })?;
        Ok(row_to_iteration(row)?)
    }

    async fn list_for_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<Vec<Iteration>, CoreError> {
        let rows = sqlx::query_as::<Sqlite, IterationRow>(
            "SELECT * FROM iterations WHERE workflow_id = ? ORDER BY sequence_no ASC",
        )
        .bind(workflow_id.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(rows
            .into_iter()
            .map(row_to_iteration)
            .collect::<Result<Vec<_>, _>>()?)
    }
}

//! `SqlRequestStore` — the requests repository.

use async_trait::async_trait;
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_types::{CoreError, Request, RequestId, RequestStatus, RequestStore, SandboxId, Sealed};

use crate::error::DbError;
use crate::rows::{enum_to_db, parse_enum, row_to_request, RequestRow};

/// `SQLite` repository for requests. Holds a cheap `SqlitePool` clone.
#[derive(Debug)]
pub struct SqlRequestStore {
    pool: SqlitePool,
}

impl SqlRequestStore {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }
}

impl Sealed for SqlRequestStore {}

#[async_trait]
impl RequestStore for SqlRequestStore {
    async fn create_request(
        &self,
        request_id: &RequestId,
        cwd: &str,
        sandbox_id: Option<&SandboxId>,
        request_prompt: &str,
    ) -> Result<(), CoreError> {
        let now = OffsetDateTime::now_utc();
        sqlx::query(
            "INSERT INTO requests \
             (id, cwd, sandbox_id, request_prompt, status, created_at, updated_at, finished_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
        )
        .bind(request_id.as_str())
        .bind(cwd)
        .bind(sandbox_id.map(SandboxId::as_str))
        .bind(request_prompt)
        .bind(enum_to_db(&RequestStatus::Running))
        .bind(now)
        .bind(now)
        .execute(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(())
    }

    async fn get(&self, id: &RequestId) -> Result<Option<Request>, CoreError> {
        let row = sqlx::query_as::<Sqlite, RequestRow>("SELECT * FROM requests WHERE id = ?")
            .bind(id.as_str())
            .fetch_optional(&self.pool)
            .await
            .map_err(DbError::from)?;
        Ok(row.map(row_to_request).transpose()?)
    }

    async fn finish_request(
        &self,
        id: &RequestId,
        status: RequestStatus,
    ) -> Result<Option<Request>, CoreError> {
        let mut tx = self.pool.begin().await.map_err(DbError::from)?;
        let existing = sqlx::query_as::<Sqlite, RequestRow>("SELECT * FROM requests WHERE id = ?")
            .bind(id.as_str())
            .fetch_optional(&mut *tx)
            .await
            .map_err(DbError::from)?;
        let Some(row) = existing else {
            return Ok(None);
        };
        // Idempotent on a terminal request: return it unchanged.
        if parse_enum::<RequestStatus>("requests.status", &row.status)?.is_terminal() {
            return Ok(Some(row_to_request(row)?));
        }
        let now = OffsetDateTime::now_utc();
        let updated = sqlx::query_as::<Sqlite, RequestRow>(
            "UPDATE requests SET status = ?, finished_at = ?, updated_at = ? WHERE id = ? RETURNING *",
        )
        .bind(enum_to_db(&status))
        .bind(now)
        .bind(now)
        .bind(id.as_str())
        .fetch_one(&mut *tx)
        .await
        .map_err(DbError::from)?;
        tx.commit().await.map_err(DbError::from)?;
        Ok(Some(row_to_request(updated)?))
    }

    async fn list(&self) -> Result<Vec<Request>, CoreError> {
        let rows = sqlx::query_as::<Sqlite, RequestRow>(
            "SELECT * FROM requests ORDER BY created_at DESC, id DESC",
        )
        .fetch_all(&self.pool)
        .await
        .map_err(DbError::from)?;
        rows.into_iter()
            .map(row_to_request)
            .collect::<Result<Vec<_>, DbError>>()
            .map_err(CoreError::from)
    }
}

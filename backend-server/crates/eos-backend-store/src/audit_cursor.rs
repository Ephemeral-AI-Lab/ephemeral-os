//! `AuditCursorRepo` — the per-sandbox `audit_cursor` repository.

use sqlx::sqlite::SqliteRow;
use sqlx::{Row, SqlitePool};
use time::OffsetDateTime;

use eos_backend_types::AuditCursor;
use eos_types::SandboxId;

use crate::db::{id_in, ts_in, ts_out, StoreError};

const COLUMNS: &str =
    "sandbox_id, last_seq, boot_epoch_id, lost_before_seq, dropped_count, updated_at";

/// Repository for sandbox audit pull cursors. Holds a cheap `SqlitePool` clone.
#[derive(Debug, Clone)]
pub struct AuditCursorRepo {
    pool: SqlitePool,
}

impl AuditCursorRepo {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Insert or replace a sandbox's audit cursor.
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn upsert(&self, cursor: &AuditCursor) -> Result<(), StoreError> {
        sqlx::query(&format!(
            "INSERT INTO audit_cursor ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?) \
             ON CONFLICT(sandbox_id) DO UPDATE SET \
               last_seq = excluded.last_seq, \
               boot_epoch_id = excluded.boot_epoch_id, \
               lost_before_seq = excluded.lost_before_seq, \
               dropped_count = excluded.dropped_count, \
               updated_at = excluded.updated_at"
        ))
        .bind(cursor.sandbox_id.as_str())
        .bind(cursor.last_seq)
        .bind(cursor.boot_epoch_id)
        .bind(cursor.lost_before_seq)
        .bind(i64::try_from(cursor.dropped_count).unwrap_or(i64::MAX))
        .bind(ts_in(cursor.updated_at))
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Fetch a sandbox's audit cursor.
    ///
    /// # Errors
    /// [`StoreError`] on a query or decode failure.
    pub async fn get(&self, sandbox_id: &SandboxId) -> Result<Option<AuditCursor>, StoreError> {
        let row = sqlx::query(&format!(
            "SELECT {COLUMNS} FROM audit_cursor WHERE sandbox_id = ?"
        ))
        .bind(sandbox_id.as_str())
        .fetch_optional(&self.pool)
        .await?;
        row.as_ref().map(row_to_cursor).transpose()
    }

    /// Durable daemon-audit loss totals across all sandboxes: the summed
    /// `dropped_count` and the number of sandboxes whose cursor recorded a
    /// `lost_before_seq` boundary (an epoch reset or ring eviction).
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn loss_totals(&self) -> Result<(u64, u64), StoreError> {
        let row = sqlx::query(
            "SELECT \
               CAST(COALESCE(SUM(dropped_count), 0) AS INTEGER) AS dropped, \
               COUNT(CASE WHEN lost_before_seq IS NOT NULL THEN 1 END) AS with_loss \
             FROM audit_cursor",
        )
        .fetch_one(&self.pool)
        .await?;
        let dropped: i64 = row.try_get("dropped")?;
        let with_loss: i64 = row.try_get("with_loss")?;
        Ok((dropped.max(0) as u64, with_loss.max(0) as u64))
    }
}

fn row_to_cursor(row: &SqliteRow) -> Result<AuditCursor, StoreError> {
    let dropped_count: i64 = row.try_get("dropped_count")?;
    let updated_at: OffsetDateTime = row.try_get("updated_at")?;
    Ok(AuditCursor {
        sandbox_id: id_in("audit_cursor.sandbox_id", row.try_get("sandbox_id")?)?,
        last_seq: row.try_get("last_seq")?,
        boot_epoch_id: row.try_get("boot_epoch_id")?,
        lost_before_seq: row.try_get("lost_before_seq")?,
        dropped_count: dropped_count.max(0) as u64,
        updated_at: ts_out(updated_at),
    })
}

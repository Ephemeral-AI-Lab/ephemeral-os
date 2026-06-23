use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension};
use thiserror::Error;

use crate::paths::ObservabilityPaths;
use crate::records::{RecordValidationError, SandboxSnapshotRecord, SpanRecord, TraceRecord};

struct Migration {
    version: i64,
    name: &'static str,
    sql: &'static str,
}

const MIGRATIONS: &[Migration] = &[Migration {
    version: 1,
    name: "phase_1_observability_foundation",
    sql: V1_SCHEMA_SQL,
}];

const SCHEMA_MIGRATIONS_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at_unix_ms INTEGER NOT NULL
);
"#;

const V1_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  sandbox_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  request_id TEXT,
  started_at_unix_ms INTEGER NOT NULL,
  finished_at_unix_ms INTEGER,
  duration_ms REAL,
  error_kind TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS spans (
  span_id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  parent_span_id TEXT,
  method_name TEXT NOT NULL,
  call_index INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at_unix_ms INTEGER NOT NULL,
  finished_at_unix_ms INTEGER,
  duration_ms REAL,
  error_kind TEXT,
  error_message TEXT,
  FOREIGN KEY(trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE,
  FOREIGN KEY(parent_span_id) REFERENCES spans(span_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sandbox_snapshots (
  sandbox_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  sampled_at_unix_ms INTEGER NOT NULL,
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_traces_request
  ON traces(request_id);

CREATE INDEX IF NOT EXISTS idx_traces_sandbox_started
  ON traces(sandbox_id, started_at_unix_ms);

CREATE INDEX IF NOT EXISTS idx_spans_trace_call_index
  ON spans(trace_id, call_index);
"#;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("failed to create observability directory {path}")]
    CreateDirectory {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("sqlite error")]
    Sqlite(#[from] rusqlite::Error),
    #[error("invalid record")]
    InvalidRecord(#[from] RecordValidationError),
    #[error("observability connection lock is poisoned")]
    ConnectionLock,
    #[error("schema migration {version} checksum mismatch: expected {expected}, found {actual}")]
    MigrationChecksumMismatch {
        version: i64,
        expected: String,
        actual: String,
    },
}

pub struct ObservabilityStore {
    connection: Mutex<Connection>,
}

impl ObservabilityStore {
    pub fn open(paths: &ObservabilityPaths) -> Result<Self, StoreError> {
        fs::create_dir_all(paths.observability_dir()).map_err(|source| {
            StoreError::CreateDirectory {
                path: paths.observability_dir().to_path_buf(),
                source,
            }
        })?;

        let mut connection = Connection::open(paths.database_path())?;
        configure_connection(&connection)?;
        apply_schema(&mut connection)?;

        Ok(Self {
            connection: Mutex::new(connection),
        })
    }

    pub fn insert_trace(
        &self,
        trace: &TraceRecord,
        spans: &[SpanRecord],
    ) -> Result<(), StoreError> {
        trace.validate()?;
        for span in spans {
            span.validate_for_trace(&trace.trace_id)?;
        }

        let mut connection = self.connection()?;
        let transaction = connection.transaction()?;
        transaction.execute(
            "INSERT INTO traces (
                trace_id,
                kind,
                status,
                sandbox_id,
                operation,
                request_id,
                started_at_unix_ms,
                finished_at_unix_ms,
                duration_ms,
                error_kind,
                error_message
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                &trace.trace_id,
                &trace.kind,
                &trace.status,
                &trace.sandbox_id,
                &trace.operation,
                &trace.request_id,
                trace.started_at_unix_ms,
                trace.finished_at_unix_ms,
                trace.duration_ms,
                &trace.error_kind,
                &trace.error_message,
            ],
        )?;

        for span in spans {
            transaction.execute(
                "INSERT INTO spans (
                    span_id,
                    trace_id,
                    parent_span_id,
                    method_name,
                    call_index,
                    status,
                    started_at_unix_ms,
                    finished_at_unix_ms,
                    duration_ms,
                    error_kind,
                    error_message
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    &span.span_id,
                    &span.trace_id,
                    &span.parent_span_id,
                    &span.method_name,
                    span.call_index,
                    &span.status,
                    span.started_at_unix_ms,
                    span.finished_at_unix_ms,
                    span.duration_ms,
                    &span.error_kind,
                    &span.error_message,
                ],
            )?;
        }

        transaction.commit()?;
        Ok(())
    }

    pub fn upsert_sandbox_snapshot(
        &self,
        snapshot: &SandboxSnapshotRecord,
    ) -> Result<(), StoreError> {
        snapshot.validate()?;

        let connection = self.connection()?;
        connection.execute(
            "INSERT INTO sandbox_snapshots (
                sandbox_id,
                state,
                sampled_at_unix_ms,
                error_message
            ) VALUES (?1, ?2, ?3, ?4)
            ON CONFLICT(sandbox_id) DO UPDATE SET
                state = excluded.state,
                sampled_at_unix_ms = excluded.sampled_at_unix_ms,
                error_message = excluded.error_message",
            params![
                &snapshot.sandbox_id,
                &snapshot.state,
                snapshot.sampled_at_unix_ms,
                &snapshot.error_message,
            ],
        )?;

        Ok(())
    }

    fn connection(&self) -> Result<MutexGuard<'_, Connection>, StoreError> {
        self.connection
            .lock()
            .map_err(|_| StoreError::ConnectionLock)
    }
}

fn configure_connection(connection: &Connection) -> Result<(), StoreError> {
    connection.busy_timeout(Duration::from_millis(1000))?;
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "NORMAL")?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    Ok(())
}

fn apply_schema(connection: &mut Connection) -> Result<(), StoreError> {
    let transaction = connection.transaction()?;
    transaction.execute_batch(SCHEMA_MIGRATIONS_SQL)?;

    for migration in MIGRATIONS {
        let expected_checksum = schema_checksum(migration.sql);
        let applied_checksum = transaction
            .query_row(
                "SELECT checksum FROM schema_migrations WHERE version = ?1",
                [migration.version],
                |row| row.get::<_, String>(0),
            )
            .optional()?;

        match applied_checksum {
            Some(checksum) if checksum == expected_checksum => {}
            Some(actual) => {
                return Err(StoreError::MigrationChecksumMismatch {
                    version: migration.version,
                    expected: expected_checksum,
                    actual,
                });
            }
            None => {
                transaction.execute_batch(migration.sql)?;
                transaction.execute(
                    "INSERT INTO schema_migrations (
                        version,
                        name,
                        checksum,
                        applied_at_unix_ms
                    ) VALUES (?1, ?2, ?3, ?4)",
                    params![
                        migration.version,
                        migration.name,
                        &expected_checksum,
                        unix_time_ms()
                    ],
                )?;
            }
        }
    }

    transaction.commit()?;
    Ok(())
}

fn schema_checksum(sql: &str) -> String {
    const FNV_OFFSET: u64 = 0xcbf29ce484222325;
    const FNV_PRIME: u64 = 0x00000100000001b3;

    let mut hash = FNV_OFFSET;
    for byte in sql.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }

    format!("fnv1a64:{hash:016x}")
}

fn unix_time_ms() -> i64 {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    i64::try_from(duration.as_millis()).unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests {
    use super::{schema_checksum, V1_SCHEMA_SQL};

    #[test]
    fn schema_checksum_changes_with_sql_text() {
        let checksum = schema_checksum(V1_SCHEMA_SQL);

        assert!(checksum.starts_with("fnv1a64:"));
        assert_ne!(checksum, schema_checksum("SELECT 1;"));
    }
}

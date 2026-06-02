//! The single `eos-db` error enum and its bridge to `eos-state`'s `CoreError`.

use eos_state::CoreError;

/// Errors raised by the SQLite persistence layer.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum DbError {
    /// A non-SQLite (network) database url was configured (GC-eos-db-04).
    #[error("postgresql is not supported in agent-core; configure a sqlite url")]
    PostgresRejected,
    /// An underlying `sqlx` error (connection, query, constraint violation).
    #[error("database error")]
    Sqlx(#[from] sqlx::Error),
    /// A JSON column failed to encode.
    #[error("failed to encode json column")]
    JsonEncode(#[source] serde_json::Error),
    /// A JSON column failed to decode.
    #[error("failed to decode json column")]
    JsonDecode(#[source] serde_json::Error),
    /// A row referenced by id was not present where one was required.
    #[error("row {id} not found in {table}")]
    NotFound {
        /// The table the row was expected in.
        table: &'static str,
        /// The missing row id.
        id: String,
    },
    /// A TEXT column held a value outside the expected enum vocabulary.
    #[error("invalid enum value {value:?} for {field}")]
    InvalidEnum {
        /// The domain field being parsed.
        field: &'static str,
        /// The offending raw value.
        value: String,
    },
    /// A migration failed to apply.
    #[error("migration failed")]
    Migrate(#[from] sqlx::migrate::MigrateError),
    /// A filesystem error creating the database's parent directory.
    #[error("filesystem error preparing the database path")]
    Io(#[from] std::io::Error),
}

impl From<DbError> for CoreError {
    /// Flatten a `DbError` into the `Store`-trait contract error. `CoreError`
    /// is the upstream leaf enum and cannot name `DbError`, so the error is
    /// carried as its `Display` string (see `eos-types::CoreError::Store`).
    fn from(err: DbError) -> Self {
        CoreError::Store(err.to_string())
    }
}

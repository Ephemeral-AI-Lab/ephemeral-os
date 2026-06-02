//! `SqlitePool` builder: reject non-`SQLite` urls, apply PRAGMA discipline, create
//! the parent directory, and run the embedded migrations.

use std::str::FromStr;
use std::time::Duration;

use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions};
use sqlx::SqlitePool;

use eos_config::DatabaseConfig;

use crate::error::DbError;

/// Defence-in-depth guard: reject a network database url (GC-eos-db-04). The
/// primary fail-fast lives in `eos-config::DatabaseUrl::parse`; a `DatabaseConfig`
/// therefore cannot normally carry a non-`SQLite` url, but this re-check keeps the
/// invariant at the pool boundary too.
pub(crate) fn ensure_sqlite_url(url: &str) -> Result<(), DbError> {
    let lower = url.to_ascii_lowercase();
    if lower.starts_with("postgres://")
        || lower.starts_with("postgresql://")
        || lower.starts_with("mysql://")
    {
        return Err(DbError::PostgresRejected);
    }
    Ok(())
}

/// Open the `SQLite` pool: reject Postgres, set WAL / foreign-keys / busy-timeout
/// PRAGMAs on every connection, create the parent directory for a file-backed
/// db, and run `migrations/`.
pub(crate) async fn open_pool(config: &DatabaseConfig) -> Result<SqlitePool, DbError> {
    let url = config.url.as_str();
    ensure_sqlite_url(url)?;

    let mut options = if url.starts_with("sqlite:") {
        SqliteConnectOptions::from_str(url)?
    } else {
        SqliteConnectOptions::new().filename(url)
    };
    options = options
        .create_if_missing(true)
        .foreign_keys(config.foreign_keys)
        .busy_timeout(Duration::from_millis(config.busy_timeout_ms));
    if config.wal {
        options = options.journal_mode(SqliteJournalMode::Wal);
    }

    // Mirror the Python `mkdir(parents=True, exist_ok=True)` for non-:memory: dbs.
    let filename = options.get_filename().to_path_buf();
    if filename != std::path::Path::new(":memory:") {
        if let Some(parent) = filename.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
    }

    let pool = SqlitePoolOptions::new()
        .max_connections(config.pool_size)
        .connect_with(options)
        .await?;
    sqlx::migrate!().run(&pool).await?;
    Ok(pool)
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-eos-db-06: a non-`SQLite` url is rejected before any connection. The
    // config layer also rejects it, so this guards the pool boundary directly.
    #[test]
    fn postgres_url_rejected() {
        assert!(matches!(
            ensure_sqlite_url("postgresql://user:pw@db.example.com/app"),
            Err(DbError::PostgresRejected)
        ));
        assert!(matches!(
            ensure_sqlite_url("postgres://db/app"),
            Err(DbError::PostgresRejected)
        ));
        assert!(matches!(
            ensure_sqlite_url("mysql://host/app"),
            Err(DbError::PostgresRejected)
        ));
        assert!(ensure_sqlite_url("sqlite:///./x.db").is_ok());
    }
}

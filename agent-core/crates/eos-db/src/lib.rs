//! eos-db — the single `SQLite`-backed persistence implementation for agent-core.
//!
//! Turns the abstract per-entity `Store` traits (owned by `eos-types`) into
//! concrete `sqlx` repositories over one local `SQLite` file: it owns the
//! `SqlitePool` (PRAGMA discipline), the versioned `migrations/`, the typed row
//! structs and their explicit row↔domain mapping (the naming gap, anchor §4),
//! the model registry, and the single database constructor [`Database`].
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod config;
mod database;
mod error;
mod json_col;
mod model_registry;
mod pool;
mod repositories;
mod rows;

pub use config::{DatabaseConfig, DatabaseUrl, DEFAULT_SQLITE_DATABASE_URL};
pub use database::Database;
pub use error::DbError;
pub use model_registry::{ModelRegistry, ResolvedModel};

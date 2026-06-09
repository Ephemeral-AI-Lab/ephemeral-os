//! `Database` — the single database constructor and store accessors.

use std::sync::Arc;

use sqlx::SqlitePool;

use eos_types::{
    AttemptStore, IterationStore, ModelStore, RequestStore, TaskAgentRunStore, TaskStore,
    WorkflowStore,
};

use crate::error::DbError;
use crate::model_registry::ModelRegistry;
use crate::pool;
use crate::repositories::{
    SqlAttemptStore, SqlIterationStore, SqlRequestTaskStore, SqlTaskAgentRunStore, SqlWorkflowStore,
};
use crate::DatabaseConfig;

/// Owns the pool and one instance of each store, handed out as `Arc<dyn …Store>`
/// for DIP at the seam. Cloning is cheap (every field is `Arc`-backed).
#[derive(Debug, Clone)]
pub struct Database {
    pool: SqlitePool,
    request_tasks: Arc<SqlRequestTaskStore>,
    workflows: Arc<SqlWorkflowStore>,
    iterations: Arc<SqlIterationStore>,
    attempts: Arc<SqlAttemptStore>,
    task_agent_runs: Arc<SqlTaskAgentRunStore>,
    models: Arc<ModelRegistry>,
}

impl Database {
    /// Open the `SQLite` file, reject Postgres, apply PRAGMAs, run migrations, and
    /// construct every store.
    ///
    /// # Errors
    /// Returns [`DbError`] for a non-`SQLite` url, a connection/filesystem failure,
    /// or a migration failure.
    pub async fn open(config: &DatabaseConfig) -> Result<Self, DbError> {
        let pool = pool::open_pool(config).await?;
        Ok(Self {
            request_tasks: Arc::new(SqlRequestTaskStore::new(pool.clone())),
            workflows: Arc::new(SqlWorkflowStore::new(pool.clone())),
            iterations: Arc::new(SqlIterationStore::new(pool.clone())),
            attempts: Arc::new(SqlAttemptStore::new(pool.clone())),
            task_agent_runs: Arc::new(SqlTaskAgentRunStore::new(pool.clone())),
            models: Arc::new(ModelRegistry::new(pool.clone())),
            pool,
        })
    }

    /// The request store.
    #[must_use]
    pub fn requests(&self) -> Arc<dyn RequestStore> {
        self.request_tasks.clone()
    }

    /// The task store.
    #[must_use]
    pub fn tasks(&self) -> Arc<dyn TaskStore> {
        self.request_tasks.clone()
    }

    /// The workflow store.
    #[must_use]
    pub fn workflows(&self) -> Arc<dyn WorkflowStore> {
        self.workflows.clone()
    }

    /// The iteration store.
    #[must_use]
    pub fn iterations(&self) -> Arc<dyn IterationStore> {
        self.iterations.clone()
    }

    /// The attempt store.
    #[must_use]
    pub fn attempts(&self) -> Arc<dyn AttemptStore> {
        self.attempts.clone()
    }

    /// The task-agent-run lineage store.
    #[must_use]
    pub fn task_agent_runs(&self) -> Arc<dyn TaskAgentRunStore> {
        self.task_agent_runs.clone()
    }

    /// The model store (trait surface).
    #[must_use]
    pub fn models(&self) -> Arc<dyn ModelStore> {
        self.models.clone()
    }

    /// The concrete model registry (for `active_resolved` / `sync_from_config`,
    /// which are not part of the `ModelStore` trait).
    #[must_use]
    pub fn model_registry(&self) -> Arc<ModelRegistry> {
        self.models.clone()
    }

    /// The underlying connection pool (migration / introspection use).
    #[must_use]
    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }
}

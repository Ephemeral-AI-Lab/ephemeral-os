//! Shared in-crate test fakes (`#[cfg(test)]` only).
//!
//! `FakeTaskStore` is an in-memory [`TaskStore`] used by the outcome-projection
//! and store-contract tests to prove trait substitutability without sqlx
//! (`test-mock-traits`). It mirrors the Python store semantics for the methods
//! the tests exercise.
#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use eos_types::{CoreError, JsonObject, TaskId};

use crate::outcomes::ExecutionTaskOutcome;
use crate::store::{Sealed, TaskStore};
use crate::task::{Task, TaskStatus};

/// In-memory [`TaskStore`] fake backed by a `Mutex<HashMap<…>>`.
#[derive(Debug, Default)]
pub(crate) struct FakeTaskStore {
    tasks: Mutex<HashMap<TaskId, Task>>,
}

impl FakeTaskStore {
    /// A fresh empty fake.
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Seed a task directly (test helper, not part of the trait).
    pub(crate) fn put(&self, task: Task) {
        self.tasks
            .lock()
            .expect("lock")
            .insert(task.id.clone(), task);
    }
}

impl Sealed for FakeTaskStore {}

#[async_trait]
impl TaskStore for FakeTaskStore {
    async fn upsert_task(&self, task: &Task) -> Result<(), CoreError> {
        self.tasks
            .lock()
            .expect("lock")
            .insert(task.id.clone(), task.clone());
        Ok(())
    }

    async fn get(&self, id: &TaskId) -> Result<Option<Task>, CoreError> {
        Ok(self.tasks.lock().expect("lock").get(id).cloned())
    }

    async fn set_task_status(
        &self,
        id: &TaskId,
        status: TaskStatus,
        outcomes: Option<&[ExecutionTaskOutcome]>,
        terminal_tool_result: Option<&JsonObject>,
    ) -> Result<Task, CoreError> {
        let mut guard = self.tasks.lock().expect("lock");
        let task = guard
            .get_mut(id)
            .ok_or_else(|| CoreError::Store(format!("task {id} not found")))?;
        task.status = status;
        if let Some(o) = outcomes {
            task.outcomes = o.to_vec();
        }
        if let Some(r) = terminal_tool_result {
            task.terminal_tool_result = Some(r.clone());
        }
        Ok(task.clone())
    }

    async fn set_task_status_if_current(
        &self,
        id: &TaskId,
        expected: TaskStatus,
        status: TaskStatus,
        outcomes: Option<&[ExecutionTaskOutcome]>,
        terminal_tool_result: Option<&JsonObject>,
    ) -> Result<Option<Task>, CoreError> {
        let mut guard = self.tasks.lock().expect("lock");
        let task = guard
            .get_mut(id)
            .ok_or_else(|| CoreError::Store(format!("task {id} not found")))?;
        if task.status != expected {
            return Ok(None);
        }
        task.status = status;
        if let Some(o) = outcomes {
            task.outcomes = o.to_vec();
        }
        if let Some(r) = terminal_tool_result {
            task.terminal_tool_result = Some(r.clone());
        }
        Ok(Some(task.clone()))
    }
}

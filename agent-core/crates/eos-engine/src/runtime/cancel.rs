//! [`EngineCancelPort`] — the engine-side implementation of the two recursive
//! agent-core cancellation primitives (spec §7.2, §12.2, §12.3).
//!
//! The trait lives in `eos-tools` to avoid an `eos-engine` <-> `eos-workflow`
//! crate cycle; this is the wired implementor. Both methods are awaited
//! end-to-end and idempotent:
//!
//! - `cancel_task` CASes the persisted task `{Pending,Running} -> Cancelled`
//!   (repeat calls no-op via the CAS) and recurses into the live run, if any.
//! - `cancel_agent_run` claims the registry entry (`Running -> Claimed`), then —
//!   only on the first claim — requests cooperative cancellation, tears down the
//!   run's foreground and background resources, finishes its durable row, and
//!   removes the entry. A second call sees the entry gone and returns `Ok(())`.
//!
//! Claim-before-flag ordering (claim, *then* `request_cancel`) guarantees that by
//! the time the query loop observes the cancel flag, the claim is taken, so the
//! run's own `run_agent` finalizer reliably loses the claim and skips its row +
//! teardown (the message-record is still finished by `run_agent`, cancel-aware).

use std::sync::Arc;

use async_trait::async_trait;
use eos_tools::{CancelPort, ToolError};
use eos_types::{AgentRunId, JsonObject, TaskId};
use eos_types::{TaskStatus, TaskStore};

use super::registry::AgentRunRegistry;

/// Request-scoped implementation of the recursive cancellation primitives.
#[derive(Clone)]
pub struct EngineCancelPort {
    registry: AgentRunRegistry,
    task_store: Arc<dyn TaskStore>,
}

impl std::fmt::Debug for EngineCancelPort {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EngineCancelPort").finish_non_exhaustive()
    }
}

impl EngineCancelPort {
    /// Wire the port with the live-run registry and task store.
    #[must_use]
    pub fn new(registry: AgentRunRegistry, task_store: Arc<dyn TaskStore>) -> Self {
        Self {
            registry,
            task_store,
        }
    }
}

/// The cancelled task terminal payload (spec §14): `{ fail_reason, reason }`.
fn cancelled_terminal(reason: &str) -> JsonObject {
    let mut terminal = JsonObject::new();
    terminal.insert("fail_reason".to_owned(), "cancelled".into());
    terminal.insert("reason".to_owned(), reason.into());
    terminal
}

#[async_trait]
impl CancelPort for EngineCancelPort {
    async fn cancel_task(&self, task_id: &TaskId, reason: &str) -> Result<(), ToolError> {
        // Persisted-state cancellation: CAS the task row from its current
        // non-terminal status to Cancelled. Reading first then CASing on the
        // observed status keeps terminal tasks untouched and makes repeats no-op.
        if let Some(task) = self
            .task_store
            .get(task_id)
            .await
            .map_err(ToolError::Store)?
        {
            if matches!(task.status, TaskStatus::Pending | TaskStatus::Running) {
                let terminal = cancelled_terminal(reason);
                self.task_store
                    .set_task_status_if_current(
                        task_id,
                        task.status,
                        TaskStatus::Cancelled,
                        None,
                        Some(&terminal),
                    )
                    .await
                    .map_err(ToolError::Store)?;
            }
        }
        // Tear down the live run owning this task, if one is registered.
        if let Some(run_id) = self.registry.agent_run_for_task(task_id) {
            self.cancel_agent_run(&run_id, reason).await?;
        }
        Ok(())
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), ToolError> {
        // Claim first (idempotency latch): a missing/already-claimed entry means
        // the run already finished or another cancel is handling it.
        let Some(control) = self.registry.begin_cancel(agent_run_id) else {
            return Ok(());
        };
        // Then flag cooperative cancellation, so the query loop stops at its next
        // turn boundary.
        control.cancellation().request_cancel(reason);
        // Tear down foreground effects (inline child runs / registered resources)
        // and background work (subagents, delegated workflows, command sessions).
        control.foreground().teardown(self, reason).await?;
        control.background().teardown(reason).await;
        // Finish the durable agent_run row (cancelled payload); ephemeral runs
        // own no row. The message-record is finished by `run_agent`.
        control
            .finalization()
            .finish_cancelled(reason)
            .await
            .map_err(|err| ToolError::Internal(err.to_string()))?;
        self.registry.finish_cancel(agent_run_id);
        Ok(())
    }
}

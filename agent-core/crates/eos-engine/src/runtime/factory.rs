//! [`AgentRunControlFactory`] — the request-scoped composition helper that builds
//! one fresh [`AgentRunControl`] per root / workflow / subagent run (spec §6.2).
//!
//! The factory is **not** per agent run: it is created once per request/workspace
//! composition and reused. It holds only immutable construction capability
//! (the foreground and background factories) and must never retain a live
//! `AgentRunControl`, `NotificationService`, `ForegroundExecutor`,
//! `BackgroundSessionService`, or manager state. Each call mints a fresh
//! notification service, foreground executor, background session service, and
//! completion monitors.

use std::sync::Arc;

use eos_types::{AgentRunId, TaskId};

use crate::background::BackgroundSessionFactory;
use crate::notifications::NotificationService;

use super::control::{AgentRunControl, AgentRunControlParts};
use super::foreground::ForegroundExecutorFactory;

/// Request-scoped, cloneable factory for per-agent-run [`AgentRunControl`]s.
#[derive(Clone)]
pub struct AgentRunControlFactory {
    foreground: ForegroundExecutorFactory,
    background: BackgroundSessionFactory,
}

impl std::fmt::Debug for AgentRunControlFactory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunControlFactory")
            .finish_non_exhaustive()
    }
}

impl AgentRunControlFactory {
    /// Compose the factory from the per-request foreground and background
    /// builders.
    #[must_use]
    pub fn new(
        foreground: ForegroundExecutorFactory,
        background: BackgroundSessionFactory,
    ) -> Self {
        Self {
            foreground,
            background,
        }
    }

    /// Build a control for a durable agent run. Root and workflow runs carry a
    /// task id; subagent runs are persisted with no owning task.
    #[must_use]
    pub fn persisted(
        &self,
        agent_run_id: AgentRunId,
        task_id: Option<TaskId>,
    ) -> Arc<AgentRunControl> {
        self.build(agent_run_id, task_id)
    }

    /// Must be called within a Tokio runtime: it spawns the run's
    /// completion monitors.
    fn build(&self, agent_run_id: AgentRunId, task_id: Option<TaskId>) -> Arc<AgentRunControl> {
        let notifications = NotificationService::new();
        let foreground = Arc::new(self.foreground.create(agent_run_id.clone()));
        // The background service carries a clone of this factory so its `spawn` can mint each
        // subagent its own run control (spec §8.1/§11.3). This is value
        // capability only — the factory holds no `AgentRunControl`, so there is
        // no reference cycle. The command manager's monitor emits completions
        // against `notifications` internally.
        let background =
            self.background
                .create(agent_run_id.clone(), notifications.clone(), self.clone());
        Arc::new(AgentRunControl::assemble(AgentRunControlParts {
            agent_run_id,
            task_id,
            agent_run_store: self.background.agent_run_store(),
            foreground,
            notifications,
            background,
        }))
    }
}

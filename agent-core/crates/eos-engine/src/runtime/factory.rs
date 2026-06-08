//! [`AgentRunControlFactory`] — the request-scoped composition helper that builds
//! one fresh [`AgentRunControl`] per root / workflow / subagent run.
//!
//! The factory is **not** per agent run: it is created once per request/workspace
//! composition and reused. It holds only immutable construction capability
//! (foreground creation plus background service construction inputs) and must never retain a live
//! `AgentRunControl`, `NotificationService`, `ForegroundExecutor`,
//! `BackgroundManagers`, or manager state. Each call mints a fresh
//! notification service, foreground executor, background managers, and
//! completion monitors.

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use eos_sandbox_port::SandboxCommandApi;
use eos_types::WorkflowApi;
use eos_types::{AgentRunId, TaskId};

use crate::background::BackgroundManagers;
use crate::notifications::NotificationService;

use super::control::{AgentRunControl, AgentRunControlParts};
use super::foreground::ForegroundExecutorFactory;
use super::types::EngineRunHandles;

/// Request-scoped, cloneable factory for per-agent-run [`AgentRunControl`]s.
#[derive(Clone)]
pub struct AgentRunControlFactory {
    foreground: ForegroundExecutorFactory,
    handles: EngineRunHandles,
    command_service: Arc<dyn SandboxCommandApi>,
    completion_poll_interval: Duration,
    workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
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
        handles: EngineRunHandles,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    ) -> Self {
        Self {
            foreground,
            handles,
            command_service,
            completion_poll_interval,
            workflow_service,
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
        // The background service carries a clone of this factory so its
        // `AgentRunService` can mint each subagent its own run control. This is
        // value capability only: the factory holds no `AgentRunControl`, so
        // there is no reference cycle.
        let background = BackgroundManagers::new(
            agent_run_id.clone(),
            self.handles.clone(),
            self.command_service.clone(),
            self.completion_poll_interval,
            notifications.clone(),
            self.clone(),
            &self.workflow_service,
        );
        Arc::new(AgentRunControl::assemble(AgentRunControlParts {
            agent_run_id,
            task_id,
            agent_run_store: self.handles.agent_run_store.clone(),
            foreground,
            notifications,
            background,
        }))
    }
}

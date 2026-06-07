//! [`BackgroundSupervisorFactory`] — the request-scoped builder of one
//! per-agent-run [`BackgroundSupervisorHandle`] (spec §8.1).
//!
//! Owned by the request-scoped `AgentRunControlFactory`. It is immutable and
//! cheap to clone, and holds only the immutable construction dependencies (run
//! handles, sandbox transport, completion poll interval) — never a per-agent
//! ledger. Each `create` mints a fresh per-run supervisor whose
//! [`CommandSessionLane`](super::lanes::CommandSessionLane) spawns this run's
//! command-completion heartbeat against the run's own notification service.

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use eos_sandbox_port::SandboxTransport;
use eos_tools::WorkflowControlPort;
use eos_types::AgentRunId;

use super::handle::BackgroundSupervisorHandle;
use super::workflow_poll::WorkflowControlCell;
use crate::notifications::NotificationService;
use crate::runtime::AgentRunControlFactory;
use crate::EngineRunHandles;

/// Request-scoped, immutable factory for per-agent-run background supervisors.
#[derive(Clone)]
pub struct BackgroundSupervisorFactory {
    handles: EngineRunHandles,
    transport: Arc<dyn SandboxTransport>,
    completion_poll_interval: Duration,
    /// Late-bound workflow-control cell (built downstream of this factory in
    /// `eos-runtime`), shared into each run's workflow-completion poller so it can
    /// observe terminal delegated-workflow state once control is wired.
    workflow_control: WorkflowControlCell,
}

impl std::fmt::Debug for BackgroundSupervisorFactory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundSupervisorFactory")
            .field("completion_poll_interval", &self.completion_poll_interval)
            .finish_non_exhaustive()
    }
}

impl BackgroundSupervisorFactory {
    /// Build the factory from the immutable per-request construction inputs.
    /// `workflow_control` is the request's late-bound control cell (it may be
    /// unset at construction; each run's poller reads it once it is wired).
    #[must_use]
    pub fn new(
        handles: EngineRunHandles,
        transport: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
        workflow_control: Arc<OnceLock<Arc<dyn WorkflowControlPort>>>,
    ) -> Self {
        Self {
            handles,
            transport,
            completion_poll_interval,
            workflow_control,
        }
    }

    /// Mint a fresh per-agent-run background supervisor handle (empty ledger).
    /// `owner_agent_run_id` is the run that owns the handle (`== caller_id` for
    /// daemon calls); `notifications` is this run's queue (the handle wraps it so
    /// background completions surface to the owning run, spec §8.4); and
    /// `control_factory` lets `spawn` give each subagent its own ephemeral control
    /// (spec §8.1/§11.3). Must be called within a Tokio runtime — the command lane
    /// spawns this run's heartbeat.
    #[must_use]
    pub fn create(
        &self,
        owner_agent_run_id: AgentRunId,
        notifications: NotificationService,
        control_factory: AgentRunControlFactory,
    ) -> BackgroundSupervisorHandle {
        BackgroundSupervisorHandle::new(
            owner_agent_run_id,
            self.handles.clone(),
            self.transport.clone(),
            self.completion_poll_interval,
            notifications,
            control_factory,
            &self.workflow_control,
        )
    }

    /// The durable agent-run store, used by a control's finalization to finish a
    /// persisted run as cancelled.
    #[must_use]
    pub(crate) fn agent_run_store(&self) -> Arc<dyn eos_state::AgentRunStore> {
        self.handles.agent_run_store.clone()
    }
}

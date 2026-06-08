//! Agent-loop composition contracts owned by the engine.

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use eos_agent_ports::AgentRunApi;
use eos_sandbox_port::SandboxCommandApi;
use eos_tool_ports::{
    CommandSessionToolService, SubagentToolService, ToolRegistry, WorkflowToolService,
};
use eos_types::{AgentRunId, WorkflowApi};

use crate::background::BackgroundManagers;
use crate::notifications::NotificationService;
use crate::EngineError;

/// Factory input for building one loop's concrete tool registry.
#[derive(Debug, Clone)]
pub struct AgentLoopToolRegistryBuildInput {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Subagent-session service for the run.
    pub subagent_sessions: SubagentToolService,
    /// Workflow-session service for the run.
    pub workflow_sessions: WorkflowToolService,
    /// Command-session service for the run.
    pub command_sessions: CommandSessionToolService,
}

/// Runtime-provided factory for concrete tool registries.
pub trait AgentLoopToolRegistryFactory: Send + Sync {
    /// Build a concrete tool registry for one loop.
    fn build_tool_registry(
        &self,
        input: AgentLoopToolRegistryBuildInput,
    ) -> Result<ToolRegistry, EngineError>;
}

/// Runtime-supplied ports needed by engine-owned background managers.
#[derive(Clone)]
pub struct AgentLoopBackgroundDependencies {
    agent_run_service: Arc<dyn AgentRunApi>,
    command_service: Arc<dyn SandboxCommandApi>,
    completion_poll_interval: Duration,
    workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
}

impl std::fmt::Debug for AgentLoopBackgroundDependencies {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopBackgroundDependencies")
            .field("completion_poll_interval", &self.completion_poll_interval)
            .finish_non_exhaustive()
    }
}

impl AgentLoopBackgroundDependencies {
    /// Build concrete background dependencies from runtime-owned ports.
    #[must_use]
    pub fn new(
        agent_run_service: Arc<dyn AgentRunApi>,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        workflow_service: Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    ) -> Self {
        Self {
            agent_run_service,
            command_service,
            completion_poll_interval,
            workflow_service,
        }
    }

    pub(crate) fn build_managers(
        &self,
        agent_run_id: AgentRunId,
        notifications: NotificationService,
    ) -> BackgroundManagers {
        BackgroundManagers::new(
            agent_run_id,
            self.agent_run_service.clone(),
            self.command_service.clone(),
            self.completion_poll_interval,
            notifications,
            &self.workflow_service,
        )
    }
}

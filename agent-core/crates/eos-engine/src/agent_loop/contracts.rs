//! Agent-loop composition contracts owned by the engine.

use eos_tool_ports::{
    CommandSessionToolService, SubagentToolService, ToolRegistry, WorkflowToolService,
};
use eos_types::AgentRunId;

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

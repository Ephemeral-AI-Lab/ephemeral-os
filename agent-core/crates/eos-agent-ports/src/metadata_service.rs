//! Agent execution metadata service contract.

use async_trait::async_trait;
use eos_audit::AuditNode;
use eos_tool_ports::{ExecutionMetadata, ToolName};
use eos_types::{AgentRunId, Message, ToolUseId};
use std::sync::Arc;

use crate::{AgentPortError, AgentState};

/// Input for rendering one tool call's execution metadata.
#[derive(Debug, Clone)]
pub struct ExecutionMetadataBuildInput {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Tool name.
    pub tool_name: ToolName,
    /// Tool-use id.
    pub tool_use_id: ToolUseId,
    /// Current model-visible conversation snapshot.
    pub conversation: Arc<[Message]>,
}

/// Input for rendering one audit node.
#[derive(Debug, Clone)]
pub struct AuditNodeBuildInput {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Tool name when rendering a tool event.
    pub tool_name: Option<ToolName>,
    /// Tool-use id when rendering a tool event.
    pub tool_use_id: Option<ToolUseId>,
}

/// Read-only service for current agent-run facts and per-call metadata.
#[async_trait]
pub trait AgentExecutionMetadataService: Send + Sync {
    /// Load the current runtime snapshot for one agent run.
    async fn agent_state(&self, agent_run_id: &AgentRunId) -> Result<AgentState, AgentPortError>;

    /// Render per-tool-call execution metadata from the current agent state.
    async fn build_execution_metadata(
        &self,
        input: ExecutionMetadataBuildInput,
    ) -> Result<ExecutionMetadata, AgentPortError>;

    /// Render an audit node from the current agent state.
    async fn build_audit_node(
        &self,
        input: AuditNodeBuildInput,
    ) -> Result<AuditNode, AgentPortError>;
}

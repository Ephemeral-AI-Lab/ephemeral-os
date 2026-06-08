//! Mutable state for one agent loop.

use std::sync::Arc;

use eos_agent_ports::{
    AgentExecutionMetadataService, AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind,
    StartAgentLoopRequest,
};
use eos_tool_ports::{
    CommandSessionToolService, SubagentToolService, ToolRegistry, ToolResult, WorkflowToolService,
};
use eos_types::AgentRunId;

use super::{AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory};
use crate::EngineError;

/// Engine-private mutable loop state.
#[allow(dead_code)] // Phase 2 API shell; read by the production turn executor in Phase 5.
pub(crate) struct AgentLoopState {
    /// Agent-run id.
    pub(crate) agent_run_id: AgentRunId,
    /// Loop transcript.
    pub(crate) conversation_messages: Vec<AgentLoopMessage>,
    /// Resolved model key.
    pub(crate) model_key: String,
    /// Completion token cap.
    pub(crate) max_completion_tokens: u32,
    /// Tool-call limit.
    pub(crate) tool_call_limit: u32,
    /// Concrete registry for this loop.
    pub(crate) tool_registry: ToolRegistry,
    /// Metadata rendering service.
    pub(crate) metadata_service: Arc<dyn AgentExecutionMetadataService>,
    /// Total provider token count when known.
    pub(crate) total_token_count: Option<i64>,
    /// Completed assistant turns.
    pub(crate) completed_turns: u32,
}

impl std::fmt::Debug for AgentLoopState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopState")
            .field("agent_run_id", &self.agent_run_id)
            .field("conversation_messages", &self.conversation_messages.len())
            .field("model_key", &self.model_key)
            .field("max_completion_tokens", &self.max_completion_tokens)
            .field("tool_call_limit", &self.tool_call_limit)
            .field("tool_registry_len", &self.tool_registry.len())
            .field("total_token_count", &self.total_token_count)
            .field("completed_turns", &self.completed_turns)
            .finish_non_exhaustive()
    }
}

impl AgentLoopState {
    pub(crate) fn from_request(
        request: StartAgentLoopRequest,
        tool_registry_factory: &dyn AgentLoopToolRegistryFactory,
        metadata_service: Arc<dyn AgentExecutionMetadataService>,
    ) -> Result<Self, EngineError> {
        let tool_registry =
            tool_registry_factory.build_tool_registry(AgentLoopToolRegistryBuildInput {
                agent_run_id: request.agent_run_id.clone(),
                subagent_sessions: inert_subagent_sessions(),
                workflow_sessions: inert_workflow_sessions(),
                command_sessions: inert_command_sessions(),
            })?;
        Ok(Self {
            agent_run_id: request.agent_run_id,
            conversation_messages: request.initial_messages,
            model_key: request.model_key,
            max_completion_tokens: request.max_completion_tokens,
            tool_call_limit: request.tool_call_limit,
            tool_registry,
            metadata_service,
            total_token_count: None,
            completed_turns: 0,
        })
    }

    pub(crate) fn advance_turn(&mut self) {
        self.completed_turns = self.completed_turns.saturating_add(1);
    }

    pub(crate) fn terminal_tool_submitted(self, outcome: ToolResult) -> AgentLoopOutcome {
        AgentLoopOutcome {
            kind: AgentLoopOutcomeKind::TerminalToolSubmitted { outcome },
            final_conversation_messages: self.conversation_messages,
            total_token_count: self.total_token_count,
        }
    }

    pub(crate) fn loop_failed(self, error: EngineError) -> AgentLoopOutcome {
        self.loop_failed_summary(error.to_string())
    }

    pub(crate) fn loop_failed_summary(self, error_summary: String) -> AgentLoopOutcome {
        AgentLoopOutcome {
            kind: AgentLoopOutcomeKind::LoopFailed { error_summary },
            final_conversation_messages: self.conversation_messages,
            total_token_count: self.total_token_count,
        }
    }

    pub(crate) fn turn_limit_reached(&self) -> bool {
        self.completed_turns >= self.tool_call_limit.max(1)
    }
}

fn inert_subagent_sessions() -> SubagentToolService {
    SubagentToolService::new(
        |_agent_run_id| async {},
        |_agent_run_id, _reason| async { false },
        || async { 0 },
        |_reason| async {},
    )
}

fn inert_workflow_sessions() -> WorkflowToolService {
    WorkflowToolService::new(|_workflow| async {})
}

fn inert_command_sessions() -> CommandSessionToolService {
    CommandSessionToolService::new(|_command_session_id, _sandbox_id| async {})
}

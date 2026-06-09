//! Agent-loop composition contracts owned by the engine.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_sandbox_port::SandboxCommandApi;
use eos_tool::{ExecutionMetadata, ToolName, ToolRegistry, ToolResult};
use eos_types::{
    AgentRunApi, AgentRunId, AgentRunStore, AgentState, JsonObject, Message, TaskStore, ToolUseId,
    WorkflowApi, WorkflowStore,
};
use serde_json::json;

use crate::background::BackgroundManagers;
use crate::notifications::EngineNotificationQueue;
use crate::EngineError;

/// Thin request to start one agent loop.
#[derive(Debug, Clone)]
pub struct StartAgentLoopRequest {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Runner-prepared initial messages.
    pub initial_messages: Vec<AgentLoopMessage>,
    /// Resolved model key.
    pub model_key: String,
    /// Completion token cap.
    pub max_completion_tokens: u32,
    /// Tool-call limit.
    pub tool_call_limit: u32,
}

/// Engine-local loop message wrapper.
#[derive(Debug, Clone)]
pub enum AgentLoopMessage {
    /// System prompt text.
    SystemPrompt(String),
    /// User message.
    UserMessage(Message),
    /// Assistant message.
    AssistantMessage(Message),
}

/// Terminal loop outcome envelope.
#[derive(Debug, Clone)]
pub struct AgentLoopOutcome {
    /// Outcome kind.
    pub kind: AgentLoopOutcomeKind,
    /// Final loop transcript.
    pub final_conversation_messages: Vec<AgentLoopMessage>,
    /// Total provider token count when known.
    pub total_token_count: Option<i64>,
}

/// Narrow loop outcome kinds for this migration.
#[derive(Debug, Clone)]
pub enum AgentLoopOutcomeKind {
    /// A terminal tool submitted successfully.
    TerminalToolSubmitted {
        /// Terminal tool result.
        outcome: ToolResult,
    },
    /// The loop failed or exited without a valid terminal submission.
    LoopFailed {
        /// Human-readable error summary.
        error_summary: String,
    },
}

/// Convert a terminal tool result into the persisted JSON payload shape.
#[must_use]
pub fn tool_result_payload(result: &ToolResult) -> JsonObject {
    let mut payload = JsonObject::new();
    payload.insert("output".to_owned(), json!(result.output));
    payload.insert("is_error".to_owned(), json!(result.is_error));
    payload.insert("metadata".to_owned(), json!(result.metadata));
    payload.insert("is_terminal".to_owned(), json!(result.is_terminal));
    payload
}

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

/// Read-only service for current agent-run facts and per-call metadata.
#[async_trait]
pub trait AgentExecutionMetadataService: Send + Sync {
    /// Load the current runtime snapshot for one agent run.
    async fn agent_state(&self, agent_run_id: &AgentRunId) -> Result<AgentState, EngineError>;

    /// Render per-tool-call execution metadata from the current agent state.
    async fn build_execution_metadata(
        &self,
        input: ExecutionMetadataBuildInput,
    ) -> Result<ExecutionMetadata, EngineError>;
}

/// Factory input for building one loop's concrete tool registry.
#[derive(Clone)]
pub struct AgentLoopToolRegistryBuildInput {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Agent-run lifecycle API for nested launches from this loop.
    pub agent_run_api: Arc<dyn AgentRunApi>,
    /// Engine-owned background aggregate for the run.
    pub background: Option<BackgroundManagers>,
}

impl std::fmt::Debug for AgentLoopToolRegistryBuildInput {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopToolRegistryBuildInput")
            .field("agent_run_id", &self.agent_run_id)
            .field("background", &self.background)
            .finish_non_exhaustive()
    }
}

/// Runtime-provided factory for concrete tool registries.
pub trait AgentLoopToolRegistryFactory: Send + Sync {
    /// Build a concrete tool registry for one loop.
    fn build_tool_registry(
        &self,
        input: AgentLoopToolRegistryBuildInput,
    ) -> Result<ToolRegistry, EngineError>;
}

/// Runtime-supplied stores used by engine-owned tool-call hooks.
#[derive(Clone)]
pub struct AgentLoopHookDependencies {
    /// Task rows used for workflow ancestry checks.
    pub(crate) task_store: Arc<dyn TaskStore>,
    /// Agent-run rows used to resolve a task when call metadata is incomplete.
    pub(crate) agent_run_store: Arc<dyn AgentRunStore>,
    /// Workflow rows used to walk parent-task ancestry.
    pub(crate) workflow_store: Arc<dyn WorkflowStore>,
}

impl std::fmt::Debug for AgentLoopHookDependencies {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopHookDependencies")
            .finish_non_exhaustive()
    }
}

impl AgentLoopHookDependencies {
    /// Build hook dependencies from runtime-owned stores.
    #[must_use]
    pub fn new(
        task_store: Arc<dyn TaskStore>,
        agent_run_store: Arc<dyn AgentRunStore>,
        workflow_store: Arc<dyn WorkflowStore>,
    ) -> Self {
        Self {
            task_store,
            agent_run_store,
            workflow_store,
        }
    }
}

/// Runtime-supplied ports needed by engine-owned background managers.
#[derive(Clone)]
pub struct AgentLoopBackgroundDependencies {
    command_service: Arc<dyn SandboxCommandApi>,
    completion_poll_interval: Duration,
    workflow_service: Arc<dyn WorkflowApi>,
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
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        workflow_service: Arc<dyn WorkflowApi>,
    ) -> Self {
        Self {
            command_service,
            completion_poll_interval,
            workflow_service,
        }
    }

    pub(crate) fn build_managers(
        &self,
        agent_run_id: AgentRunId,
        agent_run_api: Arc<dyn AgentRunApi>,
        notifications: EngineNotificationQueue,
    ) -> BackgroundManagers {
        BackgroundManagers::new(
            agent_run_id,
            agent_run_api,
            self.command_service.clone(),
            self.completion_poll_interval,
            notifications,
            self.workflow_service.clone(),
        )
    }
}

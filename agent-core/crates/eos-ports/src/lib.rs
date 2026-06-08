//! Shared tool/runtime port contracts and DTOs.
//!
//! `eos-tools` owns model-facing tool behavior, but downstream runtime crates
//! implement several narrow async contracts. This crate keeps those contracts out
//! of `eos-tools` so the tool layer can depend on ports without creating a
//! reverse dependency edge from runtime code back into tool implementation
//! modules.

#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use eos_llm_client::Message;
use eos_sandbox_port::SandboxPortError;
use eos_state::{GeneratorSubmission, PlanDisposition, PlanNodeId, ReducerSubmission};
use eos_types::{
    AgentRunId, AttemptId, CoreError, InvocationId, JsonObject, RequestId, SandboxId, TaskId,
    ToolUseId, WorkflowId,
};

pub mod agent_run;
pub mod command;
pub mod workflow;

pub use agent_run::*;
pub use command::*;
pub use workflow::*;

/// A framework fault during tool execution. Tool-domain failures are in-band
/// [`ToolResult`]s, not variants here.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ToolError {
    /// The dispatched tool name is not registered.
    #[error("unknown tool: {0}")]
    UnknownTool(String),

    /// A required execution-context value was absent where the tool requires it.
    #[error("missing required execution context: {0}")]
    MissingContext(&'static str),

    /// A required downstream-state port was not wired at the composition root.
    #[error("required port not wired: {0}")]
    MissingPort(&'static str),

    /// An upstream `Store` operation failed.
    #[error("store error: {0}")]
    Store(#[from] CoreError),

    /// A sandbox transport / daemon RPC failed at the framework level.
    #[error("sandbox error: {0}")]
    Sandbox(#[from] SandboxPortError),

    /// An internal invariant broke.
    #[error("internal tool error: {0}")]
    Internal(String),
}

/// A normalized in-band tool result. Both success and tool-domain failure are
/// values of this type; only framework faults are `Err(ToolError)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolResult {
    /// The model-facing output text.
    pub output: String,
    /// Whether this is an in-band tool-domain error.
    pub is_error: bool,
    /// Heterogeneous result metadata.
    pub metadata: JsonObject,
    /// Set by the tool pipeline when a terminal tool succeeds.
    pub is_terminal: bool,
}

impl ToolResult {
    /// A successful plain result.
    #[must_use]
    pub fn ok(output: impl Into<String>) -> Self {
        Self {
            output: output.into(),
            is_error: false,
            metadata: JsonObject::new(),
            is_terminal: false,
        }
    }

    /// An in-band tool-domain error result.
    #[must_use]
    pub fn error(output: impl Into<String>) -> Self {
        Self {
            output: output.into(),
            is_error: true,
            metadata: JsonObject::new(),
            is_terminal: false,
        }
    }

    /// Attach result metadata.
    #[must_use]
    pub fn with_metadata(mut self, metadata: JsonObject) -> Self {
        self.metadata = metadata;
        self
    }

    /// Insert one metadata key.
    #[must_use]
    pub fn meta(mut self, key: impl Into<String>, value: serde_json::Value) -> Self {
        self.metadata.insert(key.into(), value);
        self
    }
}

/// The typed facts a tool executor reads. Built per tool call and owned by the
/// call; no shared mutable service state is stored here.
#[derive(Clone)]
pub struct ExecutionMetadata {
    /// Bound agent profile name.
    pub agent_name: String,
    /// Agent-run id.
    pub agent_run_id: Option<AgentRunId>,
    /// Owning request, when set.
    pub request_id: Option<RequestId>,
    /// Owning task, when set.
    pub task_id: Option<TaskId>,
    /// Owning attempt, when set.
    pub attempt_id: Option<AttemptId>,
    /// Owning workflow, when set.
    pub workflow_id: Option<WorkflowId>,
    /// Per-call tool-use id.
    pub tool_use_id: Option<ToolUseId>,
    /// In-flight sandbox correlation id, when set.
    pub sandbox_invocation_id: Option<InvocationId>,
    /// Provisioned sandbox, when the agent is sandbox-bound.
    pub sandbox_id: Option<SandboxId>,
    /// Whether this agent currently has an open isolated workspace.
    pub is_isolated_workspace_mode: bool,
    /// Request-visible workspace root.
    pub workspace_root: String,
    /// Per-turn snapshot of the live conversation transcript.
    pub conversation: Arc<[Message]>,
}

impl std::fmt::Debug for ExecutionMetadata {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExecutionMetadata")
            .field("agent_name", &self.agent_name)
            .field("agent_run_id", &self.agent_run_id)
            .field("request_id", &self.request_id)
            .field("task_id", &self.task_id)
            .field("attempt_id", &self.attempt_id)
            .field("workflow_id", &self.workflow_id)
            .field("tool_use_id", &self.tool_use_id)
            .field("sandbox_id", &self.sandbox_id)
            .field(
                "is_isolated_workspace_mode",
                &self.is_isolated_workspace_mode,
            )
            .finish_non_exhaustive()
    }
}

impl ExecutionMetadata {
    /// The calling agent's sandbox id as a string, or `""` when unbound.
    #[must_use]
    pub fn sandbox_id_str(&self) -> &str {
        self.sandbox_id.as_ref().map_or("", SandboxId::as_str)
    }

    /// Require the bound sandbox id, else a framework fault.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingContext`] when no sandbox is bound.
    pub fn require_sandbox_id(&self) -> Result<&SandboxId, ToolError> {
        self.sandbox_id
            .as_ref()
            .ok_or(ToolError::MissingContext("sandbox_id"))
    }

    /// Require the owning task id, else a framework fault.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingContext`] when no task id is set.
    pub fn require_task_id(&self) -> Result<&TaskId, ToolError> {
        self.task_id
            .as_ref()
            .ok_or(ToolError::MissingContext("task_id"))
    }

    /// Require the owning request id, else a framework fault.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingContext`] when no request id is set.
    pub fn require_request_id(&self) -> Result<&RequestId, ToolError> {
        self.request_id
            .as_ref()
            .ok_or(ToolError::MissingContext("request_id"))
    }

    /// Require the current agent-run id, else a framework fault.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingContext`] when no agent-run id is set.
    pub fn require_agent_run_id(&self) -> Result<&AgentRunId, ToolError> {
        self.agent_run_id
            .as_ref()
            .ok_or(ToolError::MissingContext("agent_run_id"))
    }

    /// Require the owning attempt id, else a framework fault.
    ///
    /// # Errors
    /// Returns [`ToolError::MissingContext`] when no attempt id is set.
    pub fn require_attempt_id(&self) -> Result<&AttemptId, ToolError> {
        self.attempt_id
            .as_ref()
            .ok_or(ToolError::MissingContext("attempt_id"))
    }
}

/// Friend-seal for agent-core port traits.
#[doc(hidden)]
pub trait Sealed {}

/// One planner-authored generator task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanTask {
    /// Caller-assigned task id.
    pub id: PlanNodeId,
    /// Bound subagent profile name.
    pub agent_name: String,
    /// Ids this task depends on.
    pub needs: Vec<PlanNodeId>,
}

/// One planner-authored reducer task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanReducer {
    /// Caller-assigned reducer id.
    pub id: PlanNodeId,
    /// Ids this reducer depends on.
    pub needs: Vec<PlanNodeId>,
    /// The reducer's instruction prompt.
    pub prompt: String,
}

/// A validated planner DAG submission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannerPlan {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The planner's own task.
    pub planner_task_id: TaskId,
    /// Whether the plan completes the attempt or defers a goal.
    pub disposition: PlanDisposition,
    /// The generator tasks, in submission order.
    pub tasks: Vec<PlanTask>,
    /// Per-task instruction specs, keyed by task id.
    pub task_specs: BTreeMap<PlanNodeId, String>,
    /// The reducer tasks, in submission order.
    pub reducers: Vec<PlanReducer>,
}

/// The result of applying a terminal submission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubmissionAck {
    /// The submission was accepted by the orchestrator.
    Accepted,
    /// The submission was rejected with a model-facing message.
    Rejected(String),
}

/// Per-attempt submission application for terminal tools.
#[async_trait]
pub trait AttemptSubmissionPort: Sealed + Send + Sync {
    /// Apply a validated planner DAG.
    async fn apply_plan(&self, plan: PlannerPlan) -> Result<SubmissionAck, ToolError>;

    /// Record one generator task's terminal outcome.
    async fn submit_generator(
        &self,
        submission: GeneratorSubmission,
    ) -> Result<SubmissionAck, ToolError>;

    /// Record one reducer task's terminal outcome.
    async fn apply_reducer(
        &self,
        submission: ReducerSubmission,
    ) -> Result<SubmissionAck, ToolError>;
}

/// A system notification a tool/hook asks the engine to surface.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SystemNotification {
    /// The notification event key.
    pub event: String,
    /// Free-text body.
    pub message: String,
}

/// The engine notification service.
#[async_trait]
pub trait NotificationSink: Sealed + Send + Sync {
    /// Surface one system notification.
    async fn notify_system(&self, notification: SystemNotification) -> Result<(), ToolError>;
}

/// A non-leaf effect a tool creates that must be torn down on cancellation.
#[async_trait]
pub trait CancelableResource: Send + Sync {
    /// Tear down the spawned effect.
    async fn teardown(&self, reason: &str) -> Result<(), ToolError>;
}

/// Recursive agent-core cancellation primitives.
#[async_trait]
pub trait CancelPort: Send + Sync {
    /// Cancel a persisted task.
    async fn cancel_task(&self, task_id: &TaskId, reason: &str) -> Result<(), ToolError>;

    /// Cancel a live agent run.
    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), ToolError>;
}

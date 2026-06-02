//! [`AuditNode`] — the correlation envelope carried by every audit event.
//!
//! Producers populate only the identifiers they already know; the collector
//! never back-fills a missing id from payload text (the preserved invariant
//! from `audit/base.py`). All fields are `Option` and omitted from the wire form
//! when `None`, matching Python's omit-when-`None` shape.

use eos_types::{
    AgentRunId, AttemptId, IterationId, RequestId, SandboxId, TaskId, ToolUseId, WorkflowId,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Correlation envelope for an [`AuditEvent`](crate::AuditEvent).
///
/// Every field defaults to `None`; build one with [`AuditNode::builder`] or
/// [`AuditNode::default`] plus field assignment. Typed ids come from `eos-types`;
/// `agent_name` is a human label and `tool_name` is owned downstream
/// (`eos-tools`), so both stay `String` to keep this crate's dependency set at
/// exactly `eos-types`.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[non_exhaustive]
pub struct AuditNode {
    /// Owning request id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<RequestId>,
    /// Owning delegated-workflow id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_id: Option<WorkflowId>,
    /// Owning iteration id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub iteration_id: Option<IterationId>,
    /// Owning attempt id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub attempt_id: Option<AttemptId>,
    /// Owning task id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<TaskId>,
    /// Agent label (not an id; a free-form profile name).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_name: Option<String>,
    /// Owning agent-run id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_run_id: Option<AgentRunId>,
    /// Owning sandbox id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sandbox_id: Option<SandboxId>,
    /// Tool name (a downstream-owned label; kept as `String` here).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    /// Owning tool-use id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_use_id: Option<ToolUseId>,
}

impl AuditNode {
    /// Start a fresh [`AuditNodeBuilder`].
    pub fn builder() -> AuditNodeBuilder {
        AuditNodeBuilder::default()
    }
}

/// Fluent builder for [`AuditNode`]; set only the ids a producer knows.
#[derive(Debug, Clone, Default)]
#[must_use]
pub struct AuditNodeBuilder {
    node: AuditNode,
}

impl AuditNodeBuilder {
    /// Set the request id.
    pub fn request_id(mut self, id: RequestId) -> Self {
        self.node.request_id = Some(id);
        self
    }

    /// Set the delegated-workflow id.
    pub fn workflow_id(mut self, id: WorkflowId) -> Self {
        self.node.workflow_id = Some(id);
        self
    }

    /// Set the iteration id.
    pub fn iteration_id(mut self, id: IterationId) -> Self {
        self.node.iteration_id = Some(id);
        self
    }

    /// Set the attempt id.
    pub fn attempt_id(mut self, id: AttemptId) -> Self {
        self.node.attempt_id = Some(id);
        self
    }

    /// Set the task id.
    pub fn task_id(mut self, id: TaskId) -> Self {
        self.node.task_id = Some(id);
        self
    }

    /// Set the agent label.
    pub fn agent_name(mut self, name: impl Into<String>) -> Self {
        self.node.agent_name = Some(name.into());
        self
    }

    /// Set the agent-run id.
    pub fn agent_run_id(mut self, id: AgentRunId) -> Self {
        self.node.agent_run_id = Some(id);
        self
    }

    /// Set the sandbox id.
    pub fn sandbox_id(mut self, id: SandboxId) -> Self {
        self.node.sandbox_id = Some(id);
        self
    }

    /// Set the tool name.
    pub fn tool_name(mut self, name: impl Into<String>) -> Self {
        self.node.tool_name = Some(name.into());
        self
    }

    /// Set the tool-use id.
    pub fn tool_use_id(mut self, id: ToolUseId) -> Self {
        self.node.tool_use_id = Some(id);
        self
    }

    /// Finish building the [`AuditNode`].
    #[must_use]
    pub fn build(self) -> AuditNode {
        self.node
    }
}

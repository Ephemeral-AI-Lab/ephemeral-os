//! Execution-lineage record contracts.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{AgentRunId, AttemptId, IterationId, RequestId, WorkflowId};

/// Workflow coordinates used by workflow task-agent-runs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WorkflowCoordinates {
    /// Owning workflow id.
    pub workflow_id: WorkflowId,
    /// Owning iteration id.
    pub iteration_id: IterationId,
    /// Owning attempt id.
    pub attempt_id: AttemptId,
}

/// Workflow task role used for agent-run path labels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowTaskRole {
    /// Planner task.
    Planner,
    /// Worker task.
    Worker,
}

impl WorkflowTaskRole {
    /// The canonical record/task path label.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Planner => "planner",
            Self::Worker => "worker",
        }
    }

    /// The run path segment prefix for this workflow role.
    #[must_use]
    pub const fn run_segment_prefix(self) -> &'static str {
        match self {
            Self::Planner => "planner-run",
            Self::Worker => "worker-run",
        }
    }
}

/// Input to record-dir resolution for an agent run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentRunRecordIndex {
    /// Owning request.
    pub request_id: RequestId,
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
}

/// Request-rooted record directory for one agent run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentRunRecordDir(String);

impl AgentRunRecordDir {
    /// Construct from a normalized request-rooted path string.
    #[must_use]
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// Borrow the path string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Consume and return the path string.
    #[must_use]
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for AgentRunRecordDir {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

/// Passive engine-facing record target.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentRunRecordTarget {
    /// Owning request.
    pub request_id: RequestId,
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Resolved request-rooted record directory.
    pub record_dir: AgentRunRecordDir,
}

/// Row-creation-local agent-run result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreatedAgentRun {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Pre-resolved record target for the engine loop.
    pub record_target: AgentRunRecordTarget,
}

/// Format a request-rooted record directory from a resolved record index.
///
/// The formatter is intentionally pure and owns the path-segment literals.
#[must_use]
pub fn format_record_dir(index: &AgentRunRecordIndex) -> AgentRunRecordDir {
    let request_root = format!("requests/{}", index.request_id.as_str());
    let agent_run_segment = prefixed("agent-run", index.agent_run_id.as_str());
    AgentRunRecordDir::new(format!(
        "{request_root}/agent-runs/{agent_run_segment}"
    ))
}

fn prefixed(prefix: &str, id: &str) -> String {
    format!("{prefix}-{id}")
}

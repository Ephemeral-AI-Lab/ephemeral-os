//! Agent spawn request DTO.

use eos_agent_def::AgentName;
use eos_agent_message_records::AgentRunRecordKind;
use eos_llm_client::Message;
use eos_types::{AgentRunId, AttemptId, RequestId, SandboxId, TaskId, WorkflowId};

/// Request to spawn any agent kind.
#[derive(Debug, Clone)]
pub struct SpawnAgentRequest {
    /// Agent profile name to launch.
    pub agent_name: AgentName,
    /// Optional caller-provided run id; one is minted when absent.
    pub agent_run_id: Option<AgentRunId>,
    /// Initial transcript.
    pub initial_messages: Vec<Message>,
    /// Parent agent-run id, for helper/subagent lineage.
    pub parent_agent_run_id: Option<AgentRunId>,
    /// Owning request id.
    pub request_id: Option<RequestId>,
    /// Owning task id.
    pub task_id: Option<TaskId>,
    /// Owning attempt id.
    pub attempt_id: Option<AttemptId>,
    /// Owning workflow id.
    pub workflow_id: Option<WorkflowId>,
    /// Bound sandbox id.
    pub sandbox_id: Option<SandboxId>,
    /// Request-visible workspace root.
    pub workspace_root: String,
    /// Whether the caller is in isolated-workspace mode.
    pub is_isolated_workspace_mode: bool,
    /// Whether to persist the run row.
    pub persist: bool,
    /// Message-record kind.
    pub record_kind: AgentRunRecordKind,
}

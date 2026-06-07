//! Runtime-owned artifact service handle.

use eos_agent_message_records::AgentMessageRecords;

/// Optional file-backed agent-node artifacts.
#[derive(Clone, Debug, Default)]
pub(crate) struct ArtifactService {
    pub(crate) artifacts: Option<AgentMessageRecords>,
}

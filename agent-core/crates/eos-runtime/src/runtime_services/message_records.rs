//! Runtime-owned agent message-record service handle.

use eos_agent_runner::AgentMessageRecords;

/// Optional file-backed agent-node message records.
#[derive(Clone, Debug, Default)]
pub(crate) struct MessageRecordService {
    pub(crate) message_records: Option<AgentMessageRecords>,
}

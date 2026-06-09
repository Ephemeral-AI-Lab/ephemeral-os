//! Runtime-owned agent message-record service handle.

use eos_engine::records::AgentRecordWriter;

/// Optional file-backed agent-node record writer.
#[derive(Clone, Debug, Default)]
pub(crate) struct MessageRecordService {
    pub(crate) record_writer: Option<AgentRecordWriter>,
}

//! Runtime-owned agent record writer handle.

use eos_engine::records::AgentRecordWriter;

/// Optional file-backed agent-node record writer.
#[derive(Clone, Debug, Default)]
pub(crate) struct RecordWriterRuntime {
    pub(crate) record_writer: Option<AgentRecordWriter>,
}

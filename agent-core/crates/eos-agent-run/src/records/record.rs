use eos_types::{JsonObject, UtcDateTime};
use serde::{Deserialize, Serialize};

/// Stable identity columns written on every node-local record row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RecordIdentity {
    pub request_id: String,
    pub task_id: String,
    pub agent_run_id: String,
}

/// Byte range produced by a message append.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MessageAppendRange {
    /// Number of message rows appended.
    pub count: usize,
    /// Starting byte offset before the append.
    pub start_byte: u64,
    /// Ending byte offset after the append.
    pub end_byte: u64,
}

/// Raw message-record bytes plus the next tail offset.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordBytes {
    /// Raw JSONL bytes.
    pub bytes: Vec<u8>,
    /// Byte offset after `bytes`.
    pub next_byte_offset: u64,
}

/// One node-local event row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeEvent {
    /// Owning request id.
    #[serde(default)]
    pub request_id: String,
    /// Owning task id.
    #[serde(default)]
    pub task_id: String,
    /// Agent-run id.
    #[serde(default)]
    pub agent_run_id: String,
    /// Node-local sequence, starting at 1.
    pub seq: u64,
    /// Stable event category.
    pub kind: String,
    /// Small routing/status payload.
    pub payload: JsonObject,
    /// Event creation timestamp.
    pub created_at: UtcDateTime,
}

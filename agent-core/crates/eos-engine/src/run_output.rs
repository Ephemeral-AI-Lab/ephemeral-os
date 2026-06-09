//! Agent-run output surfaces.
//!
//! The record root is supplied by the backend composition root, but durable
//! message/event contents are started and finished by the engine loop where
//! provider-visible messages and tool events are observed in order. Live stream
//! observations share this owner because they are emitted by the same run.

mod error;
mod layout;
mod record_store;
mod stream;

pub use error::AgentRunRecordError;
pub use record_store::{
    AgentRunRecordEvent, AgentRunRecordFinishStatus, AgentRunRecordHandle, AgentRunRecordStore,
    MessageAppendRange, MessageBytes,
};
pub use stream::{
    stamp_identity, AgentRunStreamEvent, AgentRunStreamSink, AgentRunStreamSinkFactory,
    AssistantMessageComplete,
};

/// Output aggregate for live stream observations and durable run records.
#[derive(Clone, Default)]
pub struct AgentRunOutputs {
    stream: Option<AgentRunStreamSink>,
    record: Option<AgentRunRecordStore>,
}

impl std::fmt::Debug for AgentRunOutputs {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunOutputs")
            .field("has_stream", &self.stream.is_some())
            .field("has_record", &self.record.is_some())
            .finish()
    }
}

impl AgentRunOutputs {
    /// Create an empty output aggregate.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Attach live stream observation.
    #[must_use]
    pub fn with_stream(mut self, stream: Option<AgentRunStreamSink>) -> Self {
        self.stream = stream;
        self
    }

    /// Attach durable agent-run record writing.
    #[must_use]
    pub fn with_record(mut self, record: Option<AgentRunRecordStore>) -> Self {
        self.record = record;
        self
    }

    pub(crate) fn observe(&self, event: &AgentRunStreamEvent) {
        if let Some(stream) = &self.stream {
            stream(event);
        }
    }

    pub(crate) fn record_store(&self) -> Option<&AgentRunRecordStore> {
        self.record.as_ref()
    }
}

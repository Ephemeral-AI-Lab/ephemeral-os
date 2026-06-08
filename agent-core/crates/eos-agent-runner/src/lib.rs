//! Agent-run lifecycle API.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod error;
mod outcome;
mod request;
mod service;

pub use error::AgentRunError;
pub use eos_agent_message_records::{
    AgentMessageRecords, AgentRunRecordHandle, AgentRunRecordKind, AgentRunRecordStart,
    MessageRecordError, NodeFinishStatus, WorkflowTaskRole,
};
pub use outcome::{AgentRunOutcome, AgentRunStatus};
pub use request::SpawnAgentRequest;
pub use service::AgentRunApi;

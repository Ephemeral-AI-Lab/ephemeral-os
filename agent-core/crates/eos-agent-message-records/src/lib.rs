//! File-backed agent-node message records.
//!
//! The message-record root is supplied by the backend composition root, but the
//! message/event contents are written by agent-core at the engine boundary where
//! request, task, agent-run, and provider-visible message facts are available.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod error;
mod handle;
mod io;
mod kind;
mod layout;
mod record;
mod service;

pub use error::{MessageRecordError, Result};
pub use handle::{AgentRunRecordHandle, NodeFinishStatus};
pub use kind::{AgentRunRecordKind, AgentRunRecordStart, WorkflowTaskRole};
pub use record::{MessageAppendRange, NodeEvent, RecordBytes};
pub use service::AgentMessageRecords;

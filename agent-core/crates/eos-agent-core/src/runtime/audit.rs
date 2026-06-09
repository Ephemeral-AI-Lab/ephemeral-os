//! Runtime-owned audit side channel.
//!
//! This module owns the audit event envelope, write-only sink seam, normalized
//! collector row helpers, and append-only JSONL sinks.

mod error;
mod event;
mod jsonl;
mod node;
mod obs;
mod sink;

pub use error::AuditError;
pub use event::{AuditEvent, AuditSource, SCHEMA_VERSION};
pub use jsonl::{BufferedAuditShutdown, BufferedJsonlSink, JsonlSink};
pub use node::{AuditNode, AuditNodeBuilder};
pub use obs::{
    canonical_event_type, from_jsonl_line, to_jsonl_line, JsonObject, ObsEnvelope, ObsIds,
    ObsSource, AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED, SCHEMA, TOOL_CALL_COMPLETED,
};
pub use sink::{AuditSink, NoopAuditSink};

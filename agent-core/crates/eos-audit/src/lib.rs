//! `eos-audit` — the write-only audit side channel.
//!
//! This crate owns the structured event envelope ([`AuditEvent`] +
//! correlation [`AuditNode`]), the [`AuditSink`] seam and the synchronous
//! in-process [`AuditEventBus`], the append-only `JSONL` writers ([`JsonlSink`]
//! and the production [`BufferedJsonlSink`] + [`BufferedAuditShutdown`]), the
//! deterministic redaction helpers ([`digest`]/[`encoded_size`]), and the
//! neutral constructors that turn tool-lifecycle data into engine-sourced rows
//! ([`tool_started`]/[`tool_completed`]) and plugin rows ([`plugin_event`]).
//!
//! It depends only on `eos-types`. It does **not** own lifecycle policy (when
//! events fire is producer/engine policy), does not import any downstream
//! crate's stream types, and does no buffering/lane-routing beyond the single
//! bounded writer thread. The bus is synchronous: no `tokio`, no `async`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod bus;
mod engine_stream;
mod error;
mod event;
mod jsonl;
mod node;
mod plugin;
mod redaction;
mod sink;

pub use bus::{AuditDispatchError, AuditEventBus};
pub use engine_stream::{tool_completed, tool_started, TOOL_COMPLETED, TOOL_FAILED, TOOL_STARTED};
pub use error::AuditError;
pub use event::{AuditEvent, AuditSource, SCHEMA_VERSION};
pub use jsonl::{BufferedAuditShutdown, BufferedJsonlSink, JsonlSink};
pub use node::{AuditNode, AuditNodeBuilder};
pub use plugin::{
    plugin_event, PluginSection, PLUGIN_ERROR, PLUGIN_TOOL_COMPLETED, PLUGIN_TOOL_INVOKED,
};
pub use redaction::{digest, encoded_size};
pub use sink::{AuditSink, NoopAuditSink};

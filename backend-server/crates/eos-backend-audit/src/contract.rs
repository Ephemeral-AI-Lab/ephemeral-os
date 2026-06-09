//! Backend audit contract re-exports.
//!
//! The canonical passive audit and normalized observability contracts live in
//! `eos-types`; this module keeps the backend-audit public surface stable while
//! avoiding a dependency on the removed request lifecycle facade.

pub use eos_types::{
    canonical_event_type, from_jsonl_line, to_jsonl_line, AuditError, AuditEvent, AuditNode,
    AuditNodeBuilder, AuditSink, AuditSource, JsonObject, NoopAuditSink, ObsEnvelope, ObsIds,
    ObsSource, AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED, SCHEMA, SCHEMA_VERSION,
    TOOL_CALL_COMPLETED,
};

//! Wire contract for embedding a trace batch as a response sidecar.
//!
//! The daemon (producer) attaches an encoded trace batch under these keys and
//! the host (consumer) strips and decodes it. Both sides MUST agree on these
//! values; owning them here keeps the two ends of the wire from drifting.

/// Response-object key under which the base64+protobuf trace batch is attached.
pub const TRACE_SIDECAR_FIELD: &str = "_trace_events";
/// Schema identifier stamped on the sidecar envelope.
pub const TRACE_SIDECAR_SCHEMA: &str = "eos.trace.v1.TraceBatch";
/// Encoding identifier stamped on the sidecar envelope.
pub const TRACE_SIDECAR_ENCODING: &str = "base64+protobuf";

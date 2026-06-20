#![forbid(unsafe_code)]

//! Shared host<->daemon wire contract: the response-envelope `meta` shape and
//! the daemon error-`kind` vocabulary. This crate stays a pure contract leaf so
//! the gateway, host, daemon, and operation crates agree on these types without
//! any of them depending on the engine.

pub mod envelope;
pub mod error;
pub mod fault;

pub use envelope::{
    OperationEnvelope, OperationStatus, OperationWarning, ResourceSummary, ResponseMeta,
    ENVELOPE_VERSION,
};
pub use error::{HostGatewayErrorKind, ProtocolErrorKind};
pub use fault::{FaultDetails, OperationFault, SourceError};

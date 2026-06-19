#![forbid(unsafe_code)]

#[path = "../src/envelope.rs"]
pub mod envelope;
#[path = "../src/error.rs"]
pub mod error;
#[path = "../src/fault.rs"]
pub mod fault;

pub use envelope::{
    OperationEnvelope, OperationStatus, OperationWarning, ResourceSummary, ResponseMeta,
    StepSummary, TraceRef, WorkspaceRouteRef, ENVELOPE_VERSION,
};
pub use error::{HostGatewayErrorKind, ProtocolErrorKind};
pub use fault::{FaultDetails, OperationFault, SourceError};

#[path = "unit/envelope.rs"]
mod envelope_tests;
#[path = "unit/error.rs"]
mod error_tests;

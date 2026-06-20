//! Public daemon operation protocol boundary.
//!
//! Operation families define their domain-specific payloads locally. The
//! generic request/response carrier is shared through `daemon_rpc_protocol`.

pub use daemon_rpc_protocol::{
    OwnedRequest, Request as OperationRequest, Response as OperationResponse,
};

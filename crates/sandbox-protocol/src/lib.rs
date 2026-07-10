//! JSON-line wire encoding, framing, authentication fields, limits, malformed
//! input errors, and the private daemon-readiness handshake.
//!
//! Semantic operation vocabulary and application envelopes live in
//! `sandbox-operation-contract`, not this transport package.
#![forbid(unsafe_code)]

pub mod auth;
pub mod codec;
pub mod error;
mod framing;
pub mod handshake;
pub mod limits;

pub use auth::{DAEMON_AUTH_FIELD, GATEWAY_AUTH_FIELD};
pub use codec::{
    decode_request_value, decode_response_line, encode_authenticated_request_line,
    encode_request_line, response_line,
};
pub use error::RequestDecodeError;
pub use handshake::{
    daemon_readiness_request_line, DAEMON_READINESS_OPERATION, DAEMON_READINESS_REQUEST_ID,
};
pub use limits::ProtocolLimits;

//! Shared daemon RPC protocol primitives.
//!
//! This crate defines generic request and response types for daemon operations.
//! It does not open sockets, dispatch operations, or know command/workspace
//! semantics.

#![forbid(unsafe_code)]

pub mod auth;
pub mod error_kind;
mod framing;
pub mod limits;
pub mod request;
pub mod response;

pub use auth::DAEMON_AUTH_FIELD;
pub use limits::{MAX_REQUEST_BYTES, REQUEST_READ_TIMEOUT_S};
pub use request::{decode_request_object, ArgsPresence, OwnedRequest, Request, RpcRequest};
pub use response::{error_response_with_details, response_line, Response};

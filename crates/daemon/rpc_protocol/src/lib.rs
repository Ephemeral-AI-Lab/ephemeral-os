//! Shared daemon RPC protocol primitives.
//!
//! This crate defines generic request and response types for daemon operations.
//! It does not open sockets, dispatch operations, or know command/workspace
//! semantics.

#![forbid(unsafe_code)]

pub mod auth;
pub mod error_kind;
pub mod framing;
pub mod limits;
pub mod request;
pub mod response;

pub use auth::{DaemonRpcAuth, DAEMON_AUTH_FIELD, DAEMON_FORWARD_AUTH_FIELD};
pub use framing::{encode_json_line, push_json_line_delimiter};
pub use limits::{MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, REQUEST_READ_TIMEOUT_S};
pub use request::{
    decode_request_object, encode_request, request_object, ArgsPresence, OwnedRequest, Request,
    RpcRequest,
};
pub use response::{
    error_response, error_response_with_details, error_response_with_meta, response_fault_kind,
    response_is_accepted, response_line, response_meta, response_result_status, response_status,
    Response,
};

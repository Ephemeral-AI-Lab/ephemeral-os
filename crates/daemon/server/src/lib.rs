//! Daemon RPC server: `AF_UNIX` plus optional loopback TCP, one framed request
//! per connection, dispatch through daemon operations, and token-driven
//! shutdown.
#![forbid(unsafe_code)]

pub(crate) mod connection;
pub(crate) mod dispatch;
pub(crate) mod error;
mod lifecycle;
mod runtime;

pub(crate) use runtime::{error_response, MAX_REQUEST_BYTES, REQUEST_READ_TIMEOUT_S};
pub use runtime::{DaemonServer, ServerConfig};

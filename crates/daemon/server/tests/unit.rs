#![forbid(unsafe_code)]

#[path = "../src/connection.rs"]
pub(crate) mod connection;
#[path = "../src/dispatch.rs"]
pub(crate) mod dispatch;
#[path = "../src/error.rs"]
pub(crate) mod error;
#[path = "../src/lifecycle.rs"]
mod lifecycle;
#[path = "../src/runtime.rs"]
pub(crate) mod runtime;

pub(crate) use runtime::{error_response, MAX_REQUEST_BYTES, REQUEST_READ_TIMEOUT_S};
pub use runtime::{DaemonServer, ServerConfig};

#[path = "unit/dependency_guard.rs"]
mod dependency_guard_tests;

mod connection_tests {
    pub(crate) use crate::connection::read_request_line_with_timeout;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/connection.rs"
    ));
}

mod dispatch_tests {
    pub(crate) use crate::dispatch::parse_request;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/dispatch.rs"
    ));
}

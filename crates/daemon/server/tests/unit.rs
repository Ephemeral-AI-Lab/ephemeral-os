#![forbid(unsafe_code)]
#![allow(dead_code)]

#[path = "../src/error.rs"]
pub(crate) mod error;
#[path = "../src/server/mod.rs"]
pub(crate) mod server;

pub use server::{DaemonServer, ServerConfig};

#[path = "unit/dependency_guard.rs"]
mod dependency_guard_tests;

mod server_connection_tests {
    pub(crate) use crate::server::connection::read_request_line_with_timeout;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/server/connection.rs"
    ));
}

mod server_dispatch_tests {
    pub(crate) use crate::server::dispatch::parse_request;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/server/dispatch.rs"
    ));
}

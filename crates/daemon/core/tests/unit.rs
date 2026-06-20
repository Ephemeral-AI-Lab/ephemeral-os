#![forbid(unsafe_code)]
#![allow(dead_code)]

#[path = "../src/error.rs"]
pub(crate) mod error;
#[path = "../src/transport/mod.rs"]
pub(crate) mod transport;

pub(crate) use transport::server;

pub use server::{DaemonServer, ServerConfig};

#[path = "unit/dependency_guard.rs"]
mod dependency_guard_tests;

mod transport_connection_tests {
    pub(crate) use crate::transport::server::connection::read_request_line_with_timeout;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/transport/server/connection.rs"
    ));
}

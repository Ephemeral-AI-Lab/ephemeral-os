#![forbid(unsafe_code)]
#![allow(dead_code)]

#[path = "../src/dispatch/mod.rs"]
pub(crate) mod dispatch;
#[path = "../src/error.rs"]
pub(crate) mod error;
#[path = "../src/invocation_registry.rs"]
pub(crate) mod invocation_registry;
#[path = "../src/response.rs"]
pub(crate) mod response;
#[path = "../src/trace/mod.rs"]
pub(crate) mod trace;
#[path = "../src/transport/mod.rs"]
pub(crate) mod transport;
#[path = "../src/wire/mod.rs"]
pub mod wire;

pub(crate) use dispatch::dispatcher;
pub(crate) use invocation_registry::{
    InFlightRegistry, InvocationCancelResult, DEFAULT_REAPER_INTERVAL_S, DEFAULT_TTL_S,
};
pub(crate) use serde_json::Value;
pub(crate) use trace::sidecar::{build, events};
pub(crate) use transport::server;
pub(crate) use wire::{decode, encode, ErrorKind, Request, WireMessage};

pub use dispatcher::dispatch;
pub use server::{DaemonServer, ServerConfig};

#[path = "unit/dependency_guard.rs"]
mod dependency_guard_tests;
#[path = "unit/invocation_registry/mod.rs"]
mod invocation_registry_tests;
#[path = "unit/wire/message.rs"]
mod wire_message_tests;

mod transport_connection_tests {
    pub(crate) use crate::transport::server::connection::read_request_line_with_timeout;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/transport/server/connection.rs"
    ));
}

mod transport_dispatch_tests {
    pub(crate) use crate::transport::server::dispatch::protocol_version_error;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/transport/server/dispatch.rs"
    ));
}

mod trace_sidecar_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/trace/sidecar.rs"
    ));
}

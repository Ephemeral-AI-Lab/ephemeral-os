//! Daemon RPC server: owns transport, dispatch, and in-flight tracking while
//! delegating operation ownership to sibling crates.
//!
#![forbid(unsafe_code)]

pub(crate) mod dispatch;
pub(crate) mod error;
pub(crate) mod invocation_registry;
pub(crate) mod response;
pub(crate) mod transport;
pub mod wire;

pub(crate) use dispatch::dispatcher;
pub(crate) use transport::server;

pub use dispatcher::dispatch;

pub use invocation_registry::InFlightRegistry;
pub(crate) use invocation_registry::{DEFAULT_REAPER_INTERVAL_S, DEFAULT_TTL_S};
pub use server::{DaemonServer, ServerConfig};

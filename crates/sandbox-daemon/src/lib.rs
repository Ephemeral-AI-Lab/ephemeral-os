//! Sandbox daemon server: an `AF_UNIX` plus optional loopback TCP JSON-line RPC
//! transport and a separate loopback HTTP transport, each owning its own
//! listener with no sniffing or multiplexing between them.
#![forbid(unsafe_code)]

#[cfg(feature = "jemalloc")]
#[global_allocator]
static GLOBAL_ALLOCATOR: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

mod http;
pub(crate) mod observability;
mod rpc;

pub use rpc::{SandboxDaemonError, SandboxDaemonServer, ServerConfig};

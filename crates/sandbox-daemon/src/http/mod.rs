//! Daemon HTTP transport: health and `/forward` reverse proxying over a
//! loopback listener that is separate from the JSON-line RPC transport.

mod forward;
mod health;
mod response;
mod router;
mod server;

pub(crate) use server::spawn;

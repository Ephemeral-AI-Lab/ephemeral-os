//! Daemon HTTP transport: liveness, `/forward` reverse proxying, and the
//! read-only `/files/list` exception over a listener that is separate from
//! the authenticated JSON-line RPC transport.

mod api;
mod forward;
pub(crate) mod health;
mod response;
mod router;
mod server;

pub(crate) use server::spawn;

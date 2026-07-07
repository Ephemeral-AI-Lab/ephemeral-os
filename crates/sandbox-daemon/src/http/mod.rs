//! Daemon HTTP transport: health, `/forward` reverse proxying, and the
//! token-gated `/export` spool stream over a loopback listener that is
//! separate from the JSON-line RPC transport.

mod api;
mod export;
mod forward;
mod health;
mod response;
mod router;
mod server;

pub(crate) use server::spawn;

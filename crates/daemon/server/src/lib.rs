//! Daemon RPC server: owns transport and dispatch while delegating operation
//! ownership to sibling crates.
//!
#![forbid(unsafe_code)]

pub(crate) mod error;
pub(crate) mod server;

pub use server::{DaemonServer, ServerConfig};

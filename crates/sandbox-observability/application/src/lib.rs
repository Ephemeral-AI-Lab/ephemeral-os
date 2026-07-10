//! Structured observability queries over adapter-neutral input ports.
//!
//! This application consumes observability declarations from the semantic
//! catalog; the daemon adapter supplies sandbox-scoped runtime data. Aggregate
//! snapshots remain manager-owned.
#![forbid(unsafe_code)]

pub mod ports;

mod query;
mod registry;
mod response;

pub use registry::{dispatch_operation, observability_handler_keys};

//! The single semantic operation catalog and route manifest.
//!
//! Domain features select public manager, runtime, and observability
//! declarations and routes; canonical internal identifiers and routes are
//! always compiled. Presentation metadata and business handlers remain with
//! their adapters and applications.
#![forbid(unsafe_code)]

pub mod internal;
pub mod routed;
pub mod routes;

#[cfg(feature = "manager")]
pub mod manager;
#[cfg(feature = "observability")]
pub mod observability;
#[cfg(feature = "runtime")]
pub mod runtime;

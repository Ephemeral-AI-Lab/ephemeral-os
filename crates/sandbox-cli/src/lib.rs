//! Shared sandbox CLI client core and feature-gated executable adapters.
#![forbid(unsafe_code)]

pub mod core;
pub mod help;
pub mod projection;

#[cfg(feature = "manager")]
pub mod manager;
#[cfg(feature = "observability")]
pub mod observability;
#[cfg(feature = "runtime")]
pub mod runtime;

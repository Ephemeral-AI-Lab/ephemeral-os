//! CLI-owned projection, input, output, help, and feature-gated executables.
#![forbid(unsafe_code)]

pub mod help;
pub mod input;
pub mod output;
pub mod projection;

#[cfg(feature = "manager")]
pub mod manager;
#[cfg(feature = "observability")]
pub mod observability;
#[cfg(feature = "runtime")]
pub mod runtime;

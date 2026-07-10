//! Shared presentation core for the sandbox CLI binaries.
//!
//! Owns catalog-driven argv parsing and response, error, and help rendering.
#![forbid(unsafe_code)]

pub mod output;
pub mod request_builder;

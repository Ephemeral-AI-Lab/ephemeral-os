//! Shared workspace runtime primitives plus concrete workspace modes.
//!
//! `ephemeral_workspace` owns one-operation overlay transactions that publish
//! captured upperdir changes. `isolated_workspace` owns caller-keyed private
//! namespaces whose upperdir is discarded on exit. Common filesystem and
//! telemetry contracts live in `shared` so the two modes expose the same core
//! operation vocabulary without hiding their different lifecycle rules.
#![forbid(unsafe_code)]

pub mod ephemeral_workspace;
pub mod isolated_workspace;
pub mod shared;

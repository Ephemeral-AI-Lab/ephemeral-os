//! Workspace command runtime.
//!
//! This crate contains the shared workspace contracts, the caller-keyed run
//! manager, and the closed-set ephemeral/isolated workspace execution modes.
//! The PTY substrate lives in `eos-command-session`.
#![forbid(unsafe_code)]

pub mod contract;
pub mod ephemeral;
pub mod isolated;
pub mod run;

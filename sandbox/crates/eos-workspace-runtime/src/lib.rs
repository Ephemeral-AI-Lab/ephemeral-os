//! Workspace command runtime.
//!
//! This crate contains the PTY command-session substrate, caller-keyed run
//! manager, and the closed-set ephemeral/isolated workspace execution modes.
#![forbid(unsafe_code)]

pub mod command_session;
pub mod ephemeral;
pub mod isolated;
pub mod run;

pub use run::{IsolatedCommandHandle, WorkspaceRunHostPorts};

#[cfg(target_os = "linux")]
pub use run::{StartTarget, WorkspaceRunManager};

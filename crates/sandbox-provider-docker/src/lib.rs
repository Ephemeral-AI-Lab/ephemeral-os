//! Docker-backed implementations of the `sandbox-manager` provider traits.
//!
//! This crate owns only Docker mechanics behind the manager ports: it creates
//! and removes Linux containers, uploads the Linux `sandbox-daemon` binary plus
//! config, starts containers, inspects published ports and labels, recovers
//! existing containers after a gateway restart, and maps Docker failures into
//! [`sandbox_manager::ManagerError`]. The generic lifecycle, rollback, and
//! forwarding stay in `sandbox-manager`; this crate never depends on
//! `sandbox-daemon`.

#![forbid(unsafe_code)]

mod archive;
mod engine;
mod installer;
mod labels;
mod launch;
mod runtime;

pub use installer::DockerSandboxDaemonInstaller;
pub use launch::daemon_launch_argv;
pub use runtime::DockerSandboxRuntime;
pub use sandbox_config::configs::manager::DockerRuntimeConfig;

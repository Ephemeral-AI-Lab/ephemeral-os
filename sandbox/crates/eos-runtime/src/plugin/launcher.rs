//! The ns-runner launch seam.
//!
//! The "current binary has an `ns-runner` subcommand" contract belongs to the
//! `eosd` binary, so this crate never assumes it: [`crate::PluginRuntime`]
//! receives an [`NsRunnerLauncher`] and the daemon implements the three launch
//! shapes (oneshot run, detached service spawn, in-namespace remount).

use std::process::Child;
use std::time::Duration;

use eos_namespace::protocol::{RunRequest, RunResult};

/// Launches `ns-runner` children for the plugin runtime. The implementor owns
/// the binary identity and process mechanics; requests are fully built by the
/// caller. Exactly the three launch shapes the runtime uses — this is not a
/// generic process abstraction.
pub trait NsRunnerLauncher: Send + Sync {
    /// Run one ns-runner request to completion (oneshot overlay).
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the request cannot be encoded, the child
    /// cannot be spawned/fed, or it exits unsuccessfully.
    fn run(&self, request: &RunRequest) -> Result<RunResult, LaunchError>;

    /// Spawn a long-lived ns-runner child (connected service with overlay).
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the request cannot be encoded or the
    /// child cannot be spawned/fed.
    fn spawn_detached(&self, request: &RunRequest) -> Result<Child, LaunchError>;

    /// Re-run a remount request inside an existing child's namespaces.
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the remount helper cannot be launched,
    /// times out, or exits unsuccessfully.
    fn remount_in(
        &self,
        target_pid: u32,
        request: &RunRequest,
        timeout: Duration,
    ) -> Result<(), LaunchError>;
}

/// Failures raised by an [`NsRunnerLauncher`]. Message text is preserved
/// verbatim through the daemon error mapping so wire responses do not drift.
#[derive(Debug, thiserror::Error)]
pub enum LaunchError {
    /// The request could not be encoded / fed to the child.
    #[error("{0}")]
    InvalidRequest(String),

    /// A process / pipe I/O operation failed.
    #[error(transparent)]
    Io(#[from] std::io::Error),

    /// The launch pipeline failed (spawn refusal, bad exit, timeout, output).
    #[error("{0}")]
    Failed(String),
}

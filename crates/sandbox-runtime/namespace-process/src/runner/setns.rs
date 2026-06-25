//! Setns mode: join holder namespaces, optionally mount overlay/DNS, run a command.

use std::path::PathBuf;

use super::RunnerError;
use crate::runner::protocol::{NamespaceRunnerRequest, RunResult};

#[cfg(target_os = "linux")]
mod mount_overlay;
#[cfg(target_os = "linux")]
mod namespaces;
#[cfg(target_os = "linux")]
mod shell;

#[cfg(all(target_os = "linux", test))]
pub(crate) use namespaces::namespace_fd_order_with_types;

#[cfg(target_os = "linux")]
pub(crate) fn run_setns(request: &NamespaceRunnerRequest) -> Result<RunResult, RunnerError> {
    shell::run_setns(request)
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn run_setns(_request: &NamespaceRunnerRequest) -> Result<RunResult, RunnerError> {
    Err(RunnerError::Unsupported)
}

/// Mount the overlay inside an existing workspace mount namespace.
#[cfg(target_os = "linux")]
pub fn setns_overlay_mount(
    request: &NamespaceRunnerRequest,
    hidden_paths: &[PathBuf],
) -> Result<(), RunnerError> {
    mount_overlay::setns_overlay_mount(request, hidden_paths)
}

#[cfg(not(target_os = "linux"))]
pub fn setns_overlay_mount(
    _request: &NamespaceRunnerRequest,
    _hidden_paths: &[PathBuf],
) -> Result<(), RunnerError> {
    Err(RunnerError::Unsupported)
}

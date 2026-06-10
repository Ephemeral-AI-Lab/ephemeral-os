//! Shared overlay ns-runner helpers and daemon adapters.

mod convert;

use std::io::Write;
#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};

use eos_layerstack::LayerChange;
use eos_namespace::protocol::{RunRequest, RunResult};
use eos_overlay::overlay_writable_root;
use eos_workspace_runtime::contract::{InvocationId, SnapshotLease};
use eos_workspace_runtime::ephemeral::{
    EphemeralDirAllocator, EphemeralRunDirs, EphemeralWorkspaceError, LayerStackRoot, PathChange,
    PublishOutcome, WorkspacePublisherPort,
};

use crate::error::DaemonError;
use crate::invocation_registry::InFlightRegistry;

pub(crate) use convert::{changeset_from_publish_outcome, ephemeral_daemon_error};
use convert::{overlay_daemon_error, publish_outcome_from_changeset};

pub(crate) use eos_workspace_runtime::ephemeral::RunDirCleanup;

/// Wrap any displayable error as an `EphemeralWorkspaceError::PublishFailed`.
pub(crate) fn publish_failed(error: impl std::fmt::Display) -> EphemeralWorkspaceError {
    EphemeralWorkspaceError::PublishFailed {
        reason: error.to_string(),
    }
}

pub(crate) struct DaemonPublisherPort<'a> {
    root: &'a Path,
}

impl<'a> DaemonPublisherPort<'a> {
    pub(crate) const fn new(root: &'a Path) -> Self {
        Self { root }
    }
}

impl WorkspacePublisherPort for DaemonPublisherPort<'_> {
    fn publish_upperdir_changes(
        &self,
        _root: &LayerStackRoot,
        snapshot: &SnapshotLease,
        changes: &[LayerChange],
        _path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError> {
        let changeset = eos_layerstack::service::publish_capture(
            self.root,
            snapshot.manifest_version,
            &snapshot.layer_paths,
            changes,
        )
        .map_err(publish_failed)?;
        Ok(publish_outcome_from_changeset(&changeset))
    }
}

fn ephemeral_dir_allocator() -> Result<EphemeralDirAllocator, DaemonError> {
    Ok(EphemeralDirAllocator::new(
        overlay_writable_root()
            .map_err(|err| overlay_daemon_error("overlay writable root", &err))?
            .join("runtime"),
    ))
}

pub(crate) fn overlay_run_dirs(
    kind: &str,
    invocation_id: &str,
) -> Result<EphemeralRunDirs, DaemonError> {
    ephemeral_dir_allocator()?
        .allocate(kind, &InvocationId(invocation_id.to_owned()))
        .map_err(ephemeral_daemon_error)
}

pub(crate) fn run_ns_runner_child(
    request: &RunRequest,
    invocation_registry: Option<&InFlightRegistry>,
) -> Result<RunResult, DaemonError> {
    let payload =
        serde_json::to_vec(request).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
    let mut command = Command::new(std::env::current_exe()?);
    command
        .arg("ns-runner")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(target_os = "linux")]
    command.process_group(0);
    let mut child = command.spawn()?;
    if let Some(registry) = invocation_registry {
        if let Ok(pgid) = i32::try_from(child.id()) {
            registry.register_process_group(&request.tool_call.invocation_id, pgid);
        }
    }
    child
        .stdin
        .as_mut()
        .ok_or_else(|| DaemonError::OverlayPipeline("ns-runner stdin unavailable".to_owned()))?
        .write_all(&payload)?;
    let output = child.wait_with_output()?;
    if let Some(registry) = invocation_registry {
        registry.clear_process_group(&request.tool_call.invocation_id);
    }
    if !output.status.success() {
        return Err(DaemonError::OverlayPipeline(format!(
            "ns-runner exited with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    serde_json::from_slice::<RunResult>(&output.stdout)
        .map_err(|err| DaemonError::OverlayPipeline(format!("invalid ns-runner output: {err}")))
}

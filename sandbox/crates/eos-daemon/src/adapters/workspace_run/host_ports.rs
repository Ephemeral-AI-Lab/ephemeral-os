//! Daemon implementation of the run-host injected seams.
//!
//! These are the three responsibilities the `eos-workspace-run-host` crate
//! deliberately does NOT take on (they would pull the `eos-occ` edge or reach
//! daemon-global state): publishing through the per-root OCC single writer,
//! sampling daemon-process resource telemetry, and recording an isolated
//! command's audit into the caller's daemon-global isolated session.

use std::path::Path;

use eos_ephemeral_workspace::{finalize_ephemeral_command, EphemeralWorkspace};
use eos_layerstack::LayerStack;
use eos_workspace_api::{
    FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome, WorkspaceTimings,
};
use eos_workspace_run_host::WorkspaceRunHostPorts;
use serde_json::Value;

use crate::response_timings::{resource_timings, timing_map};
use crate::adapters::overlay::DaemonPublisherPort;
use crate::adapters::workspace_run::isolated::record_tool_call;

/// Zero-sized: each call resolves its daemon resources (the per-root OCC writer,
/// the process-global isolated session) freshly, so there is no captured state.
pub(crate) struct DaemonRunHostPorts;

impl WorkspaceRunHostPorts for DaemonRunHostPorts {
    fn base_timings(&self, root: &Path) -> Result<WorkspaceTimings, WorkspaceApiError> {
        let manifest = LayerStack::open(root.to_path_buf())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        Ok(timing_map(resource_timings(&manifest, 0)))
    }

    fn finalize_ephemeral(
        &self,
        root: &Path,
        workspace: EphemeralWorkspace,
        base_timings: WorkspaceTimings,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        finalize_ephemeral_command(&DaemonPublisherPort::new(root), workspace, base_timings, request)
    }

    fn record_tool_call(&self, caller_id: &str, audit: Value) {
        record_tool_call(caller_id, audit);
    }
}

fn workspace_api_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("daemon_command_workspace_error", error.to_string())
}

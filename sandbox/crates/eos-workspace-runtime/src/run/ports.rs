use std::path::Path;

use crate::ephemeral::EphemeralWorkspace;
use crate::contract::{
    FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome, WorkspaceTimings,
};
use serde_json::Value;

/// Daemon-provided services the run lifecycle depends on but that must stay in
/// the daemon process. Injected so this crate keeps no `eos-occ` edge (the
/// build-time no-publish guard) and no daemon-global state:
///
/// * [`base_timings`](Self::base_timings) — daemon `/proc` + cgroup resource
///   telemetry, spliced onto each finalize.
/// * [`finalize_ephemeral`](Self::finalize_ephemeral) — publish a completed
///   ephemeral run's captured upperdir through the daemon's per-root OCC single
///   writer.
/// * [`record_tool_call`](Self::record_tool_call) — record an isolated command's
///   captured audit into the caller's daemon-global isolated session.
pub trait WorkspaceRunHostPorts: Send + Sync {
    fn base_timings(&self, root: &Path) -> Result<WorkspaceTimings, WorkspaceApiError>;

    fn finalize_ephemeral(
        &self,
        root: &Path,
        workspace: EphemeralWorkspace,
        base_timings: WorkspaceTimings,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>;

    fn record_tool_call(&self, caller_id: &str, audit: Value);
}

//! Inverted port traits the ephemeral pipeline depends on.
//!
//! These keep the dependency graph leaf->root: the lower crate (here) *defines*
//! the trait; `eos-daemon` *implements and injects* it. The named deliverable is
//! [`ChangesetProjectionPort`] (the daemon-side projection + drain-gate accessor).
//!
//! The remaining traits are LOCAL PLACEHOLDERS for surfaces owned by sibling
//! crates whose skeletons are being written concurrently and export nothing yet
//! (layerstack snapshot/lease, occ runtime services, overlay lifecycle, runner
//! namespace exec). Each carries a `// PORT` note naming its eventual owner; when
//! the sibling crate stabilizes its public type, these placeholders collapse onto
//! it. They are intentionally as thin as the orchestrator signatures require.

use std::path::PathBuf;

use eos_protocol::{Intent, LayerChange};

use crate::error::Result;

/// A leased snapshot of the active layer stack: the lowerdir set an overlay
/// mount is built from, plus the lease that pins those layers on disk.
///
/// PLACEHOLDER mirror of the layerstack snapshot type. eos-layerstack will own
/// the concrete value (it is the HINGE owner of the snapshot/lease port).
/// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:48-53 — _PreparedOverlaySnapshot`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeasedSnapshot {
    /// The lease id pinning the snapshot's layers (released on drop of the op).
    pub lease_id: String,
    /// Active manifest version this snapshot was taken at.
    pub manifest_version: u64,
    /// Lowerdir layer paths, newest-first (overlay mount ordering invariant).
    pub layer_paths: Vec<PathBuf>,
}

/// The persisted workspace binding: which `workspace_root` a `layer_stack_root`
/// is mounted at, and the absolute<->layer-relative path mapping.
///
/// PLACEHOLDER mirror of `layer_stack/workspace_binding.py`. eos-layerstack owns
/// the concrete value.
/// `// PORT backend/src/sandbox/layer_stack/workspace_binding.py — WorkspaceBinding`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceBinding {
    /// Absolute mount point inside the sandbox (e.g. `/testbed`).
    pub workspace_root: String,
}

/// Snapshot/lease + active-manifest access the pipeline drives directly.
///
/// LOCAL PLACEHOLDER for the eos-layerstack snapshot port (the HINGE that lives
/// in layerstack, NOT occ — that placement is what keeps the no-publish crates
/// off the occ edge). The pipeline links eos-layerstack directly for exactly
/// this surface.
/// `// PORT backend/src/sandbox/shared/layer_stack_port.py — LayerStackSnapshotPort`
pub trait LayerStackSnapshotPort {
    /// Lease the latest snapshot and return its lowerdir layer paths + version.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:402-417 — _lease_overlay_snapshot`
    fn acquire_snapshot(&self, request_id: &str) -> Result<LeasedSnapshot>;

    /// Release a previously acquired lease (idempotent at the guard layer).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:419-424 — _release_lease`
    fn release_lease(&self, lease_id: &str) -> Result<()>;

    /// Active manifest version, used to detect foreign publishes for remount.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:318-339 — ensure_current`
    fn active_manifest_version(&self) -> Result<u64>;

    /// Read a file's text directly from the layer stack (read fast path).
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:306 — read_text`
    fn read_text(&self, path: &str) -> Result<(String, bool)>;
}

/// The per-`layer_stack_root` OCC runtime services bundle the daemon injects:
/// the single-writer OCC mutation client + the bound layer-stack snapshot port.
///
/// LOCAL PLACEHOLDER for severing #2 — eos-daemon implements this and keys it on
/// `layer_stack_root` so the WRITE_ALLOWED publish path always routes through the
/// ONE `occ-commit-queue` writer per root (MF-1 single-writer).
/// `// PORT backend/src/sandbox/daemon/occ_runtime_services.py:48 — get_occ_runtime_services`
pub trait OccRuntimeServicesPort {
    /// Apply a write/edit changeset through the single OCC writer for this root.
    /// Returns published path results that downstream projection consumes.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:349 — occ_service.apply_changeset`
    fn apply_changeset(&self, changes: &[LayerChange]) -> Result<Vec<PublishedFile>>;
}

/// Published-file outcome of an OCC changeset apply (path + commit status).
///
/// PLACEHOLDER mirror of eos-occ `changeset.FileResult`. eos-occ owns the
/// concrete value and the `is_*_status` predicates.
/// `// PORT backend/src/sandbox/occ/changeset.py — FileResult`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedFile {
    /// Layer-relative path that was published.
    pub path: String,
    /// Commit status string (e.g. `committed`, `aborted_overlap`).
    pub status: String,
    /// Human-readable detail surfaced as a conflict message on failure.
    pub message: String,
}

/// Daemon-side accessor that projects an OCC changeset onto the guarded
/// operation result shape AND owns the per-agent dispatch drain-gate.
///
/// This is the NAMED inverted port (severing #4). eos-daemon implements it; the
/// pipeline/dispatch in this crate call it without linking the daemon. It folds
/// two daemon concerns the Rust dispatch layer consolidates:
///
/// * projection — turning published `FileResult`s into `changed_paths` /
///   `conflict` / `status` (the `changeset_projection.py` helpers).
/// * the drain-gate — the short-held entry-lock + inflight bookkeeping that lets
///   `exit_isolated_workspace` quiesce in-flight dispatches before mutating
///   routing state (`dispatch.py` `AgentQuiesceState` / `acquire_dispatch_slot`).
///
/// `// PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:16-60`
/// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:108-134 — acquire_dispatch_slot`
pub trait ChangesetProjectionPort {
    /// Paths of every published file (success + published status).
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:16-18 — published_paths`
    fn published_paths(&self, files: &[PublishedFile]) -> Vec<String>;

    /// Surface the first non-committed file as a `(conflict, status)` pair.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:21-38 — conflict_and_status`
    fn conflict_and_status(
        &self,
        files: &[PublishedFile],
    ) -> (Option<eos_protocol::ConflictInfo>, String);

    /// Open a dispatch slot for `agent_id`: probe the exit-pending flag and bump
    /// the inflight counter under the short-held entry lock. Returns a guard
    /// whose drop decrements the counter (RAII drain bookkeeping). Errors with
    /// [`crate::error::EphemeralError::LifecycleInProgress`] when exit is
    /// draining.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:108-134 — acquire_dispatch_slot`
    fn acquire_dispatch_slot(&self, agent_id: &str) -> Result<DispatchSlot>;
}

/// RAII guard returned by [`ChangesetProjectionPort::acquire_dispatch_slot`].
///
/// Holding it counts the dispatch as in-flight; dropping it decrements the
/// per-agent inflight counter so `exit_isolated_workspace` can drain.
/// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:126-133 — finally decrement`
#[derive(Debug)]
#[non_exhaustive]
pub struct DispatchSlot {
    /// The agent this slot belongs to (decremented against on drop).
    pub agent_id: String,
}

/// The overlay lifecycle the shell/glob/grep path drives: acquire a private
/// upperdir over the leased snapshot, capture upper-dir changes, release.
///
/// LOCAL PLACEHOLDER for eos-overlay's `lifecycle` + `OverlayHandle`. eos-overlay
/// owns the concrete handle (and the raw `mount_overlay`/`umount` syscalls).
/// `// PORT backend/src/sandbox/overlay/lifecycle.py — acquire / capture_changes / release_overlay`
pub trait OverlayLifecyclePort {
    /// Lease the latest snapshot and allocate a private overlay upperdir.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/operation_overlay.py:40-54 — acquire_operation_overlay`
    fn acquire(&self, invocation_id: &str, workspace_root: &str) -> Result<OverlayHandle>;

    /// Capture upper-dir changes after a WRITE_ALLOWED tool call.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:149 — capture_changes`
    fn capture_changes(&self, handle: &OverlayHandle) -> Result<Vec<LayerChange>>;

    /// Tear down the per-op overlay mount and scratch dirs, releasing the lease.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:202 — release_overlay`
    fn release(&self, handle: OverlayHandle) -> Result<()>;
}

/// Opaque handle to one per-operation overlay mount.
///
/// PLACEHOLDER mirror of eos-overlay `OverlayHandle`. The concrete handle owns
/// its mount/upperdir lifetime via `Drop` (RAII) in eos-overlay.
/// `// PORT backend/src/sandbox/overlay/handle.py — OverlayHandle`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayHandle {
    /// Operation id stamped on the handle for audit correlation.
    pub operation_id: String,
    /// The lease pinning the lowerdir snapshot for this op.
    pub lease_id: String,
    /// Private upperdir capturing this op's writes.
    pub upperdir: PathBuf,
}

/// The raw verb-result payload a namespaced tool call produces before the
/// WRITE_ALLOWED capture/publish step rewrites it into a guarded result.
///
/// Modeled as opaque owned JSON text to avoid pulling `serde_json` onto this
/// crate's dep edge; the daemon decodes it with the protocol result models.
/// `// PORT backend/src/sandbox/shared/models.py — ToolCallResult (a recursive asdict mapping)`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawToolPayload(pub String);

/// Runs one tool call inside a fresh (or setns) mount namespace over the leased
/// overlay, returning the verb result payload.
///
/// LOCAL PLACEHOLDER for eos-runner's `run_in_namespace`. eos-runner owns the
/// single-threaded `unshare`/`setns` syscall path (NO tokio there).
/// `// PORT backend/src/sandbox/overlay/namespace_runner.py:48 — run_in_namespace`
pub trait NamespaceRunnerPort {
    /// Execute the request's tool call inside the namespaced overlay.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:146 — run_in_namespace(handle, req)`
    fn run_in_namespace(
        &self,
        handle: &OverlayHandle,
        intent: Intent,
        verb: &str,
        args_json: &str,
    ) -> Result<RawToolPayload>;
}

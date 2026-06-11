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

pub use ephemeral_workspace::{overlay_run_dirs, EphemeralWorkspace, EphemeralWorkspaceError};
pub use isolated_workspace::{
    CurrentExeNsRunnerLauncher, ExitOutcome, IsolatedError, IsolatedManager, IsolatedSnapshot,
    IsolatedWorkspaceId, LaunchError, NsRunnerLauncher, ResourceCaps, Rfc1918Egress,
    WorkspaceHandle,
};
pub use shared::{
    capture_upperdir, path_changes_to_wire, CaptureError, CapturedChanges, DirAllocationError,
    OverlayDirs, OverlayDirsGuard, TreeResourceStats,
};

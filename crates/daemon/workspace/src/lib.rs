//! Shared workspace runtime primitives plus concrete workspace modes.
//!
//! `ephemeral_workspace` owns one-operation overlay transactions that publish
//! captured upperdir changes. `isolated_workspace` owns caller-keyed private
//! namespaces whose upperdir is discarded on exit. The `capture`, `dirs`, and
//! `tree` modules hold the filesystem and telemetry contracts both modes share
//! so they expose the same core operation vocabulary without hiding their
//! different lifecycle rules.
#![forbid(unsafe_code)]

pub mod capture;
pub mod dirs;
pub mod ephemeral_workspace;
pub mod isolated_workspace;
pub mod tree;

pub use capture::{
    capture_upperdir, capture_upperdir_for_snapshot_with_options, CaptureError, CapturedChanges,
    RoutedCapturedChanges,
};
pub use dirs::{DirAllocationError, OverlayDirs, OverlayDirsGuard};
pub use ephemeral_workspace::{overlay_run_dirs, EphemeralWorkspace, EphemeralWorkspaceError};
pub use isolated_workspace::{
    DnsConfiguration, ExitOutcome, IsolatedError, IsolatedManager, IsolatedSnapshot,
    IsolatedWorkspaceBinding, IsolatedWorkspaceId, RemountOverlayReport, RemountProbe,
    RemountedWorkspace, ResourceCaps, Rfc1918Egress, WorkspaceHandle, WorkspaceRemountState,
};
pub use tree::TreeResourceStats;

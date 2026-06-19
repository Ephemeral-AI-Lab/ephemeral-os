//! Shared workspace runtime primitives plus concrete network modes.
//!
//! Every mode creates a private mounted workspace: fresh overlay directories
//! plus the holder-owned namespace stack used to run and remount commands.
//! `NetworkMode` only selects the workspace's network topology; higher layers
//! decide when a workspace is created, destroyed, captured, or published.
//!
//! `network_mode::host` shares the host network namespace while keeping the
//! private workspace overlay and holder namespace stack.
//! `network_mode::isolated_network` adds a dedicated network namespace with
//! veth, DNS, policy, and cgroup resources. `overlay` holds the filesystem and
//! telemetry contracts both modes share.
#![forbid(unsafe_code)]

pub mod error;
mod isolated_network_setup;
mod lifecycle;
pub mod model;
mod namespace;
pub mod network_mode;
pub mod overlay;
pub mod service;

pub use error::WorkspaceError;
pub use model::{
    BaseRevision, CallerId, CaptureChangesRequest, CaptureChangesResult, CapturedWorkspaceChanges,
    ChangedPathKind, CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult,
    LatestSnapshotRequest, LayerStackSnapshotRef, LeaseId, NetworkMode, ProtectedPathDrop,
    ProtectedPathDropReason, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceHandle, WorkspaceId, WorkspaceLaunchContext,
    WorkspaceLaunchNamespaceFds,
};
pub use service::WorkspaceService;

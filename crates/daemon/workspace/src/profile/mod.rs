//! Workspace isolation profiles and shared profile lifecycle.
//!
//! Profile-specific modules describe what differs about the workspace
//! environment. Shared holder, namespace, overlay, handle, and teardown
//! mechanics live in `common` and `handle`.

pub(crate) mod common;
pub mod handle;
pub mod host_compatible;
pub(crate) mod isolated;
pub mod manager;
pub(crate) mod resource_control;

pub use handle::{
    DnsConfiguration, WorkspaceModeContext, WorkspaceModeHandle, WorkspaceModeId,
    WorkspaceModeSnapshot,
};
pub(crate) use handle::{CGROUP_ROOT, HANDLE_PREFIX};
pub(crate) use manager::PERSISTED_HANDLES_SCHEMA_VERSION;
pub use manager::{
    ExitOutcome, IsolatedNetworkError, OrphanCleanupReport, RemountOverlayReport, RemountProbe,
    RemountedWorkspace, ResourceCaps, Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

//! Workspace isolation profiles and shared profile lifecycle.
//!
//! Profile-specific modules describe what differs about the workspace
//! environment. Shared holder, namespace, overlay, handle, and teardown
//! mechanics live in `common` and `handle`.

pub(crate) mod common;
pub mod handle;
pub mod host_compatible;
mod host_workspace;
pub(crate) mod isolated;
pub mod manager;
pub(crate) mod resource_control;

pub(crate) use handle::{workspace_namespace_fds_from_map, CGROUP_ROOT, HANDLE_PREFIX};
pub use handle::{
    DnsConfiguration, WorkspaceModeContext, WorkspaceModeHandle, WorkspaceModeId,
    WorkspaceModeSnapshot, WorkspaceNamespaceFds,
};
pub(crate) use manager::PERSISTED_HANDLES_SCHEMA_VERSION;
pub use manager::{
    ExitOutcome, IsolatedNetworkError, OrphanCleanupReport, RemountOverlayReport, RemountProbe,
    RemountedWorkspace, ResourceCaps, Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

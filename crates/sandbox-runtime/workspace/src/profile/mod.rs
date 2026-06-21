//! Workspace isolation profiles and shared profile lifecycle.
//!
//! Profile-specific behavior is selected directly by the workspace lifecycle.
//! Shared handle and resource-control types live here.

pub mod handle;
pub mod manager;

#[cfg(target_os = "linux")]
pub(crate) use handle::CGROUP_ROOT;
pub(crate) use handle::HANDLE_PREFIX;
pub use handle::{WorkspaceModeFds, WorkspaceModeHandle, WorkspaceModeId, WorkspaceModeSnapshot};
pub(crate) use manager::validate_workspace_root;
pub use manager::{
    ExitOutcome, IsolatedNetworkError, RemountOverlayReport, RemountProbe, ResourceCaps,
    Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

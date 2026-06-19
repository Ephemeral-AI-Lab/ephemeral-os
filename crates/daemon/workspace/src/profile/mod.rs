//! Workspace isolation profiles and shared profile lifecycle.
//!
//! Profile-specific behavior is selected directly by the workspace lifecycle.
//! Shared handle and resource-control types live here.

pub mod handle;
pub mod manager;
pub(crate) mod resource_control;

pub use handle::{DnsConfiguration, WorkspaceModeHandle, WorkspaceModeId, WorkspaceModeSnapshot};
pub(crate) use handle::{CGROUP_ROOT, HANDLE_PREFIX};
pub use manager::{
    ExitOutcome, IsolatedNetworkError, RemountOverlayReport, RemountProbe, ResourceCaps,
    Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

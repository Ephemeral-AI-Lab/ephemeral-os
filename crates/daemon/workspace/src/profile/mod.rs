//! Workspace isolation profiles and shared profile lifecycle.
//!
//! Profile-specific behavior is selected directly by the workspace lifecycle.
//! Shared handle and resource-control types live here.

pub mod handle;
pub mod manager;

pub use handle::{
    DnsConfiguration, WorkspaceModeFds, WorkspaceModeHandle, WorkspaceModeId, WorkspaceModeSnapshot,
};
pub(crate) use handle::{CGROUP_ROOT, HANDLE_PREFIX};
pub use manager::{
    ExitOutcome, IsolatedNetworkError, RemountOverlayReport, RemountProbe, ResourceCaps,
    Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

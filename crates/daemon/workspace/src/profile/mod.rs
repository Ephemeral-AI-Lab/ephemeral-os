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

pub use handle::{DnsConfiguration, WorkspaceModeHandle, WorkspaceModeId, WorkspaceModeSnapshot};
pub(crate) use handle::{CGROUP_ROOT, HANDLE_PREFIX};
pub use manager::{
    ExitOutcome, IsolatedNetworkError, RemountOverlayReport, RemountProbe, ResourceCaps,
    Rfc1918Egress, WorkspaceModeManager, WorkspaceRemountState,
};

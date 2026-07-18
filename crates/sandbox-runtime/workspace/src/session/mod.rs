//! Workspace session management for both network modes.
//!
//! Network-mode-specific behavior is selected directly by the workspace
//! lifecycle. The mounted-workspace state and resource-control types live here.

pub mod manager;
pub mod state;

#[doc(hidden)]
pub use crate::namespace::holder::HolderRegistration;
pub(crate) use manager::validate_workspace_root;
pub use manager::{
    ExitOutcome, ResourceCaps, Rfc1918Egress, WorkspaceManager, WorkspaceManagerError,
};
pub use state::{HolderNsFds, MountedWorkspace};

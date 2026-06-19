//! Compatibility exports for the previous `network_mode` module path.
//!
//! New implementation code lives under `crate::profile`.

pub mod host {
    pub use crate::profile::host_compatible::*;
}

pub mod isolated_network {
    pub use crate::profile::{
        DnsConfiguration, ExitOutcome, IsolatedNetworkError, OrphanCleanupReport,
        RemountOverlayReport, RemountProbe, RemountedWorkspace, ResourceCaps, Rfc1918Egress,
        WorkspaceModeContext, WorkspaceModeHandle, WorkspaceModeId, WorkspaceModeManager,
        WorkspaceModeSnapshot, WorkspaceRemountState,
    };
}

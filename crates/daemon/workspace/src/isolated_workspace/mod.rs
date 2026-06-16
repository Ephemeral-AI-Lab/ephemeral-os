mod binding;
mod caps;
mod error;
mod manager;
pub(crate) mod namespace;
mod network;
mod remount;

pub use binding::IsolatedWorkspaceBinding;
pub use caps::{ResourceCaps, Rfc1918Egress};
pub use error::IsolatedError;
pub use manager::{
    DnsConfiguration, ExitOutcome, IsolatedManager, IsolatedSnapshot, IsolatedWorkspaceId,
    WorkspaceHandle, WorkspaceRemountState,
};
pub use remount::{RemountOverlayReport, RemountProbe, RemountedWorkspace};

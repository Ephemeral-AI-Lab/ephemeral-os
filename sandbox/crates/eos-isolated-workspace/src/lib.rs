#![forbid(unsafe_code)]

mod caps;
mod error;
mod manager;
pub(crate) mod namespace;
mod network;

pub use caps::{ResourceCaps, Rfc1918Egress};
pub use error::IsolatedError;
pub use manager::{
    ExitOutcome, IsolatedManager, IsolatedSnapshot, IsolatedWorkspaceId, WorkspaceHandle,
};

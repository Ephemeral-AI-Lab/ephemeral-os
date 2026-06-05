//! Fresh per-operation workspace policy.
//!
//! This crate owns the publish-capable ephemeral workspace lifecycle pieces that
//! are unique to fresh overlay operations: allocate scratch, capture upperdir
//! changes, classify path/resource data, and call an injected publisher. Daemon
//! RPC routing, namespace-runner request construction, process supervision,
//! command-session registry state, public JSON envelopes, and generic OCC
//! publisher ownership stay outside this crate.

pub mod capture;
pub mod command_session;
pub mod dirs;
pub mod error;
pub mod file_ops;
pub mod finalize;
pub mod ops;
pub mod ports;
pub mod timings;
pub mod types;

pub use capture::{capture_for_publish, CapturedUpperdir};
pub use dirs::{EphemeralDirAllocator, RunDirCleanup};
pub use error::EphemeralWorkspaceError;
pub use finalize::{finalize_publishable_workspace, FinalizeOutcome, FinalizeRequest};
pub use ops::EphemeralWorkspaceOps;
pub use ports::WorkspacePublisherPort;
pub use timings::{EphemeralTimings, TreeResourceStats};
pub use types::{
    AgentId, EphemeralRunDirs, EphemeralSnapshot, EphemeralWorkspace, InvocationId, PathChange,
    PathChangeKind, PublishOutcome, PublishStatus, WorkspaceRoot,
};

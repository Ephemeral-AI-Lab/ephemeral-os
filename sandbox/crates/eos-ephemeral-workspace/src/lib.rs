//! Fresh per-operation workspace policy.
//!
//! This crate owns the publish-capable ephemeral workspace lifecycle pieces that
//! are unique to fresh overlay operations: allocate scratch, build a fresh
//! namespace runner request, capture the upperdir, call an injected publisher,
//! and clean up the lease and run directory. Daemon RPC routing, command-session
//! registry state, public JSON envelopes, and generic OCC publisher ownership
//! stay outside this crate.

pub mod capture;
pub mod cleanup;
pub mod dirs;
pub mod error;
pub mod finalize;
pub mod ports;
pub mod read_tool;
pub mod runner;
pub mod timings;
pub mod types;

pub use capture::{capture_for_publish, CapturedUpperdir};
pub use cleanup::{cleanup_ephemeral_workspace, CleanupOutcome};
pub use dirs::{EphemeralDirAllocator, RunDirCleanup};
pub use error::EphemeralWorkspaceError;
pub use finalize::{finalize_publishable_workspace, FinalizeOutcome, FinalizeRequest};
pub use ports::{EphemeralSnapshotPort, FreshNamespaceRunnerPort, WorkspacePublisherPort};
pub use read_tool::{run_read_tool, ReadToolOutcome, ReadToolRequest};
pub use runner::{run_fresh_namespace, FreshRunRequestBuilder};
pub use timings::{EphemeralTimings, TreeResourceStats};
pub use types::{
    AgentId, EphemeralCommandFinalizeSpec, EphemeralRunDirs, EphemeralRunOutcome,
    EphemeralSnapshot, EphemeralToolSpec, EphemeralWorkspace, InvocationId, PathChange,
    PathChangeKind, PublishOutcome, PublishStatus, WorkspaceRoot,
};

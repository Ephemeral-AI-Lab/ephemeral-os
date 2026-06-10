//! Neutral workspace-mode contracts shared by daemon adapters and the
//! concrete workspace-runtime modules in this crate.
//!
//! This module deliberately references no daemon, LayerStack, or OCC types. It
//! is the common typed boundary for symmetric file and command workspace
//! capabilities; concrete publish, read, audit, and runtime mechanics stay
//! injected by the daemon or the owning runtime module.

pub(crate) mod command;
pub(crate) mod file_ops;
pub(crate) mod lease;
pub(crate) mod mode;
pub(crate) mod mutation;
pub(crate) mod read_view;
pub(crate) mod response;

pub use command::{
    FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace,
    WorkspaceCommandOutcome,
};
pub use file_ops::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, SearchReplaceEdit,
    WorkspaceFileOps, WriteFileOutcome, WriteFileRequest,
};
pub use lease::SnapshotLease;
pub use mode::WorkspaceMode;
pub use mutation::{
    WorkspaceMutationKind, WorkspaceMutationOutcome, WorkspaceMutationRequest,
    WorkspaceMutationSink,
};
pub use read_view::{ResolvedWorkspacePath, WorkspaceReadBytes, WorkspaceReadView};
pub use response::{
    u64_to_f64_saturating, usize_to_f64_saturating, ChangedPathKinds, WorkspaceApiError,
    WorkspaceConflict, WorkspaceTimings,
};

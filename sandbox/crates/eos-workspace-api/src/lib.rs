//! Neutral workspace-mode contracts shared by daemon adapters and concrete
//! workspace-mode crates.
//!
//! This crate deliberately owns no daemon, LayerStack, or OCC dependency. It is
//! the common typed boundary for symmetric file and command workspace
//! capabilities; concrete publish, read, audit, and runtime mechanics stay
//! injected by the daemon or the owning workspace crate.

pub mod command_session;
pub mod file_ops;
pub mod mode;
pub mod mutation;
pub mod read_view;
pub mod response;

pub use command_session::{
    CommandWorkspaceOps, FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace,
    WorkspaceCommandOutcome,
};
pub use file_ops::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, SearchReplaceEdit,
    SearchReplaceError, WorkspaceFileOps, WriteFileOutcome, WriteFileRequest,
};
pub use mode::WorkspaceMode;
pub use mutation::{
    WorkspaceMutationKind, WorkspaceMutationOutcome, WorkspaceMutationRequest,
    WorkspaceMutationSink,
};
pub use read_view::{ResolvedWorkspacePath, WorkspaceReadBytes, WorkspaceReadView};
pub use response::{ChangedPathKinds, WorkspaceApiError, WorkspaceConflict, WorkspaceTimings};

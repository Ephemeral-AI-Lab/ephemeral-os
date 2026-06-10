//! Neutral file-operation contracts shared by the daemon adapters and the
//! per-mode `WorkspaceFileOps` implementations in this crate.
//!
//! This module deliberately references no daemon or storage types. Everything
//! command-shaped moved to `eos-command-ops`; what remains is the file tool
//! family's typed boundary until `eos-file-ops` takes it over.

pub(crate) mod file_ops;
pub(crate) mod mode;
pub(crate) mod mutation;
pub(crate) mod read_view;
pub(crate) mod response;

pub use file_ops::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, SearchReplaceEdit,
    WorkspaceFileOps, WriteFileOutcome, WriteFileRequest,
};
pub use mode::WorkspaceMode;
pub use mutation::{
    WorkspaceMutationKind, WorkspaceMutationOutcome, WorkspaceMutationRequest,
    WorkspaceMutationSink,
};
pub use read_view::{ResolvedWorkspacePath, WorkspaceReadBytes, WorkspaceReadView};
pub use response::{
    u64_to_f64_saturating, usize_to_f64_saturating, ChangedPathKinds, WorkspaceApiError, WorkspaceConflict,
    WorkspaceTimings,
};

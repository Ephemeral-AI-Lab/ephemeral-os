//! The `file` runtime operation domain.
//!
//! Phase 1 ships `blame`: a pure read over the append-only file-auditability log
//! (the C3 spec §7 store) that returns each line's owner as an opaque string.
//! `read`/`write`/`edit` plug into the same [`FileService`] and store later.

mod audit;
mod error;
mod service;

pub use error::{FileEntryKind, FileError, FileOperationError};
pub use service::{
    BlameRange, EditInput, EditOp, EditOutput, FileService, ReadInput, ReadOutput, WriteInput,
    WriteKind, WriteOutput,
};

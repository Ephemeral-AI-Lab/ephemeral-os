//! [`FileService`] core: the struct over the file-auditability store, its
//! constructor, and the store accessor the operation impls share. The read
//! operation (`blame`) lives in `service/impls/`; publish audit writes live in
//! `file/audit.rs`.

use std::path::PathBuf;

use super::store::FileAuditabilityStore;
use crate::services::FileRuntimeConfig;

pub struct FileService {
    store: FileAuditabilityStore,
    caps: FileRuntimeConfig,
}

/// One run of consecutive lines that share an owner. `owner` is opaque — the
/// `file` domain never parses `workspace_session:` / `operation:` / `original`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlameRange {
    pub start_line: u64,
    pub line_count: u64,
    pub owner: String,
}

impl FileService {
    /// Open the store under `dir` (created if absent), rebuilding its in-memory
    /// index from the NDJSON segments.
    ///
    /// # Errors
    /// Returns an I/O error if the directory or its segments cannot be read.
    pub fn open(dir: PathBuf, caps: FileRuntimeConfig) -> std::io::Result<Self> {
        Ok(Self {
            store: FileAuditabilityStore::open(dir)?,
            caps,
        })
    }

    pub(crate) fn store(&self) -> &FileAuditabilityStore {
        &self.store
    }

    pub(crate) fn caps(&self) -> FileRuntimeConfig {
        self.caps
    }
}

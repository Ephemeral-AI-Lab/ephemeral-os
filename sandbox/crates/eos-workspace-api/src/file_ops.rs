use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::mode::WorkspaceMode;
use crate::mutation::WorkspaceMutationOutcome;
use crate::response::{ChangedPathKinds, WorkspaceApiError, WorkspaceConflict, WorkspaceTimings};

/// Read one text file from a workspace mode.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadFileRequest {
    pub path: String,
    pub max_read_bytes: usize,
}

/// Write one file into a workspace mode.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteFileRequest {
    pub path: String,
    pub content: Vec<u8>,
    pub overwrite: bool,
    pub max_file_bytes: usize,
}

/// One exact-match replacement for edit_file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SearchReplaceEdit {
    pub old_text: String,
    pub new_text: String,
    #[serde(default)]
    pub replace_all: bool,
}

/// Apply search/replace edits to one file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EditFileRequest {
    pub path: String,
    pub edits: Vec<SearchReplaceEdit>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReadFileOutcome {
    pub mode: WorkspaceMode,
    pub success: bool,
    pub content: String,
    pub exists: bool,
    pub encoding: String,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WriteFileOutcome {
    pub mode: WorkspaceMode,
    pub success: bool,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict: Option<WorkspaceConflict>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_reason: Option<String>,
    #[serde(default)]
    pub changed_paths: Vec<String>,
    #[serde(default)]
    pub changed_path_kinds: ChangedPathKinds,
    #[serde(default)]
    pub mutation_source: String,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

impl From<WorkspaceMutationOutcome> for WriteFileOutcome {
    fn from(outcome: WorkspaceMutationOutcome) -> Self {
        Self {
            mode: outcome.mode,
            success: outcome.success,
            status: outcome.status,
            conflict: outcome.conflict,
            conflict_reason: outcome.conflict_reason,
            changed_paths: outcome.changed_paths,
            changed_path_kinds: outcome.changed_path_kinds,
            mutation_source: outcome.mutation_source,
            timings: outcome.timings,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EditFileOutcome {
    pub mode: WorkspaceMode,
    pub success: bool,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict: Option<WorkspaceConflict>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_reason: Option<String>,
    #[serde(default)]
    pub changed_paths: Vec<String>,
    #[serde(default)]
    pub changed_path_kinds: ChangedPathKinds,
    #[serde(default)]
    pub mutation_source: String,
    #[serde(default)]
    pub timings: WorkspaceTimings,
    pub applied_edits: i64,
}

impl EditFileOutcome {
    #[must_use]
    pub fn from_mutation(outcome: WorkspaceMutationOutcome, applied_edits: i64) -> Self {
        Self {
            mode: outcome.mode,
            success: outcome.success,
            status: outcome.status,
            conflict: outcome.conflict,
            conflict_reason: outcome.conflict_reason,
            changed_paths: outcome.changed_paths,
            changed_path_kinds: outcome.changed_path_kinds,
            mutation_source: outcome.mutation_source,
            timings: outcome.timings,
            applied_edits,
        }
    }
}

/// Shared direct file API.
pub trait WorkspaceFileOps {
    fn read_file(&self, request: ReadFileRequest) -> Result<ReadFileOutcome, WorkspaceApiError>;
    fn write_file(&self, request: WriteFileRequest) -> Result<WriteFileOutcome, WorkspaceApiError>;
    fn edit_file(&self, request: EditFileRequest) -> Result<EditFileOutcome, WorkspaceApiError>;
}

/// Search/replace failure. Message strings are part of the public conflict
/// contract and match `eos-protocol`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[non_exhaustive]
pub enum SearchReplaceError {
    #[error("edit anchor old_text must be non-empty")]
    EmptyAnchor,
    #[error("anchor not found")]
    NotFound,
    #[error("anchor occurrence count mismatch")]
    CountMismatch,
}

/// Apply one search/replace edit with Python `str.count` semantics.
///
/// # Errors
///
/// Returns [`SearchReplaceError`] when the anchor is empty, absent, or ambiguous
/// with `replace_all=false`.
pub fn apply_search_replace(
    text: &str,
    old: &str,
    new: &str,
    replace_all: bool,
) -> Result<String, SearchReplaceError> {
    if old.is_empty() {
        return Err(SearchReplaceError::EmptyAnchor);
    }
    let count = text.matches(old).count();
    if replace_all {
        if count == 0 {
            return Err(SearchReplaceError::NotFound);
        }
        Ok(text.replace(old, new))
    } else {
        match count {
            0 => Err(SearchReplaceError::NotFound),
            1 => Ok(text.replacen(old, new, 1)),
            _ => Err(SearchReplaceError::CountMismatch),
        }
    }
}

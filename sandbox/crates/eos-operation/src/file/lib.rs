#![forbid(unsafe_code)]

use std::time::Instant;

pub use crate::{
    ChangedPathKind, ChangedPathKinds, MutationCore, MutationSource, MutationStatus,
    OpError as FileOpsError, WorkspaceConflict, WorkspaceKind,
    WorkspaceMutationOutcome as MutationOutcome, WorkspaceTimings,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use thiserror::Error;

pub mod contract;

mod direct;
mod isolated;

pub use direct::DirectBackend;
pub use isolated::IsolatedBackend;

impl FileOpsError {
    #[must_use]
    pub fn invalid_request(message: impl Into<String>) -> Self {
        Self::new("invalid_request", message.into())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResolvedWorkspacePath {
    pub path: String,
}

impl ResolvedWorkspacePath {
    #[must_use]
    pub fn new(path: impl Into<String>) -> Self {
        Self { path: path.into() }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReadBytes {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes: Option<Vec<u8>>,
    pub exists: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_version: Option<i64>,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationKind {
    Write,
    Edit,
}

impl MutationKind {
    #[must_use]
    pub const fn verb(self) -> &'static str {
        match self {
            Self::Write => "write",
            Self::Edit => "edit",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Mutation {
    pub kind: MutationKind,
    pub path: ResolvedWorkspacePath,
    pub content: Vec<u8>,
    pub base: ReadBytes,
}

pub trait FileBackend {
    fn workspace_kind(&self) -> WorkspaceKind;

    fn mutation_source(&self, kind: MutationKind) -> MutationSource;

    fn resolve_path(&self, request_path: &str) -> Result<ResolvedWorkspacePath, FileOpsError>;

    fn read_bytes(&self, path: &ResolvedWorkspacePath) -> Result<ReadBytes, FileOpsError>;

    fn apply(&self, mutation: Mutation) -> Result<MutationOutcome, FileOpsError>;
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadFileRequest {
    pub path: String,
    pub max_read_bytes: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteFileRequest {
    pub path: String,
    pub content: Vec<u8>,
    pub overwrite: bool,
    pub max_file_bytes: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SearchReplaceEdit {
    pub old_text: String,
    pub new_text: String,
    #[serde(default)]
    pub replace_all: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EditFileRequest {
    pub path: String,
    pub edits: Vec<SearchReplaceEdit>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReadFileOutcome {
    pub workspace_kind: WorkspaceKind,
    pub success: bool,
    pub content: String,
    pub exists: bool,
    pub encoding: String,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

pub type WriteFileOutcome = MutationOutcome;
pub type EditFileOutcome = MutationOutcome;

pub fn read_file<B: FileBackend>(
    backend: &B,
    request: ReadFileRequest,
) -> Result<ReadFileOutcome, FileOpsError> {
    let total_start = Instant::now();
    let path = backend.resolve_path(&request.path)?;
    let read = backend.read_bytes(&path)?;
    let content = if read.exists {
        let bytes = read.bytes.unwrap_or_default();
        if bytes.len() > request.max_read_bytes {
            return Err(FileOpsError::invalid_request(format!(
                "file too large: {} > {} bytes",
                bytes.len(),
                request.max_read_bytes
            )));
        }
        String::from_utf8_lossy(&bytes).into_owned()
    } else {
        String::new()
    };
    let mut timings = read.timings;
    insert_total(&mut timings, "read", total_start);
    Ok(ReadFileOutcome {
        workspace_kind: backend.workspace_kind().to_owned(),
        success: true,
        content,
        exists: read.exists,
        encoding: "utf-8".to_owned(),
        timings,
    })
}

pub fn write_file<B: FileBackend>(
    backend: &B,
    request: WriteFileRequest,
) -> Result<WriteFileOutcome, FileOpsError> {
    let total_start = Instant::now();
    if request.content.len() > request.max_file_bytes {
        return Err(FileOpsError::invalid_request(format!(
            "file too large: {} > {} bytes",
            request.content.len(),
            request.max_file_bytes
        )));
    }
    let path = backend.resolve_path(&request.path)?;
    let base = backend.read_bytes(&path)?;
    if !request.overwrite && base.exists {
        let mut timings = base.timings;
        insert_total(&mut timings, "write", total_start);
        return Ok(conflict_outcome(
            backend,
            MutationKind::Write,
            &path.path,
            MutationStatus::Rejected,
            "create_only_existing",
            "file already exists",
            timings,
        ));
    }
    let mut outcome = backend.apply(Mutation {
        kind: MutationKind::Write,
        path,
        content: request.content,
        base,
    })?;
    insert_total(&mut outcome.core.timings, "write", total_start);
    Ok(outcome)
}

pub fn edit_file<B: FileBackend>(
    backend: &B,
    request: EditFileRequest,
) -> Result<EditFileOutcome, FileOpsError> {
    let total_start = Instant::now();
    let path = backend.resolve_path(&request.path)?;
    let base = backend.read_bytes(&path)?;
    if !base.exists {
        let mut timings = base.timings;
        insert_total(&mut timings, "edit", total_start);
        return Ok(conflict_outcome(
            backend,
            MutationKind::Edit,
            &path.path,
            MutationStatus::AbortedVersion,
            "aborted_version",
            "file does not exist",
            timings,
        ));
    }
    let bytes = base.bytes.clone().unwrap_or_default();
    let mut content = String::from_utf8(bytes)
        .map_err(|err| FileOpsError::invalid_request(format!("file is not utf-8 text: {err}")))?;
    for edit in &request.edits {
        if edit.old_text.is_empty() {
            return Err(FileOpsError::invalid_request(
                "edit anchor old_text must be non-empty",
            ));
        }
        match apply_search_replace(&content, &edit.old_text, &edit.new_text, edit.replace_all) {
            Ok(next) => content = next,
            Err(err) => {
                let mut timings = base.timings;
                insert_total(&mut timings, "edit", total_start);
                return Ok(conflict_outcome(
                    backend,
                    MutationKind::Edit,
                    &path.path,
                    MutationStatus::AbortedOverlap,
                    "aborted_overlap",
                    search_replace_message(&err),
                    timings,
                ));
            }
        }
    }
    let mut outcome = backend.apply(Mutation {
        kind: MutationKind::Edit,
        path,
        content: content.into_bytes(),
        base,
    })?;
    insert_total(&mut outcome.core.timings, "edit", total_start);
    outcome.applied_edits = Some(i64::try_from(request.edits.len()).unwrap_or(i64::MAX));
    Ok(outcome)
}

fn conflict_outcome<B: FileBackend>(
    backend: &B,
    kind: MutationKind,
    path: &str,
    status: MutationStatus,
    reason: &str,
    message: &str,
    timings: WorkspaceTimings,
) -> MutationOutcome {
    MutationOutcome {
        core: MutationCore {
            success: false,
            conflict: Some(WorkspaceConflict::path(reason, path, message)),
            conflict_reason: Some(reason.to_owned()),
            changed_paths: Vec::new(),
            changed_path_kinds: ChangedPathKinds::new(),
            mutation_source: Some(backend.mutation_source(kind)),
            timings,
        },
        workspace_kind: backend.workspace_kind(),
        published: false,
        status,
        applied_edits: (kind == MutationKind::Edit).then_some(0),
        ..MutationOutcome::default()
    }
}

fn insert_total(timings: &mut WorkspaceTimings, verb: &str, start: Instant) {
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(start.elapsed().as_secs_f64()),
    );
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
enum SearchReplaceError {
    #[error("anchor not found")]
    NotFound,
    #[error("anchor occurrence count mismatch")]
    CountMismatch,
}

const fn search_replace_message(err: &SearchReplaceError) -> &'static str {
    match err {
        SearchReplaceError::NotFound => "anchor not found",
        SearchReplaceError::CountMismatch => "anchor occurrence count mismatch",
    }
}

fn apply_search_replace(
    text: &str,
    old: &str,
    new: &str,
    replace_all: bool,
) -> Result<String, SearchReplaceError> {
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

#[cfg(test)]
#[path = "tests.rs"]
mod tests;

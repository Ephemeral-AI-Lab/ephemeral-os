use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::mode::WorkspaceMode;
use crate::read_view::{ResolvedWorkspacePath, WorkspaceReadBytes};
use crate::response::{ChangedPathKinds, WorkspaceApiError, WorkspaceConflict, WorkspaceTimings};

/// Direct mutation kind produced by a file operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceMutationKind {
    Write,
    Edit,
}

impl WorkspaceMutationKind {
    #[must_use]
    pub const fn verb(self) -> &'static str {
        match self {
            Self::Write => "write",
            Self::Edit => "edit",
        }
    }
}

/// Mutation request passed to a mode-specific sink.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceMutationRequest {
    pub kind: WorkspaceMutationKind,
    pub path: ResolvedWorkspacePath,
    pub content: Vec<u8>,
    pub base: WorkspaceReadBytes,
}

/// Normalized mutation outcome before daemon JSON conversion.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceMutationOutcome {
    pub mode: WorkspaceMode,
    pub success: bool,
    /// True only when the mutation reached shared workspace truth.
    pub published: bool,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

/// Write/capture result sink. Ephemeral implementations publish; isolated
/// implementations record audit-only state and return `published: false`
/// metadata without linking a publish-capable dependency.
pub trait WorkspaceMutationSink {
    fn commit_or_record(
        &self,
        request: WorkspaceMutationRequest,
    ) -> Result<WorkspaceMutationOutcome, WorkspaceApiError>;
}

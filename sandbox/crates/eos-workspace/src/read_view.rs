use serde::{Deserialize, Serialize};

use crate::response::{WorkspaceApiError, WorkspaceTimings};

/// A workspace-relative path after mode-specific binding/root resolution.
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

/// Bytes read from the current mode's read view.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceReadBytes {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes: Option<Vec<u8>>,
    pub exists: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_version: Option<i64>,
    #[serde(default)]
    pub timings: WorkspaceTimings,
}

/// Read-side capability below direct file ops and command finalization.
pub trait WorkspaceReadView {
    fn resolve_path(&self, request_path: &str) -> Result<ResolvedWorkspacePath, WorkspaceApiError>;

    fn read_bytes(
        &self,
        path: &ResolvedWorkspacePath,
    ) -> Result<WorkspaceReadBytes, WorkspaceApiError>;
}

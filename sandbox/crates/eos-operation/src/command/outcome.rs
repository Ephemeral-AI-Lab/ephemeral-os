use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use crate::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceApiError(String);

impl WorkspaceApiError {
    #[must_use]
    pub fn new(_kind: &str, message: String) -> Self {
        Self(message)
    }
}

impl std::fmt::Display for WorkspaceApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for WorkspaceApiError {}

impl From<WorkspaceApiError> for eos_command_session::CommandSessionError {
    fn from(error: WorkspaceApiError) -> Self {
        Self::Workspace(error.to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FinalizeCommandRequest {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runner_result: Option<Value>,
    #[serde(default)]
    pub command_elapsed_s: f64,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub stdout: String,
    #[serde(default)]
    pub stderr: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_session_id: Option<String>,
}

impl FinalizeCommandRequest {
    #[must_use]
    pub fn command_succeeded(&self) -> bool {
        self.status == "ok" && self.exit_code == Some(0)
    }
}

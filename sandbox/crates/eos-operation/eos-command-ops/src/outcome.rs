use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use eos_operation_core::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};

#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg(target_os = "linux")]
pub struct WorkspaceApiError(String);

#[cfg(target_os = "linux")]
impl WorkspaceApiError {
    #[must_use]
    pub fn new(_kind: &str, message: String) -> Self {
        Self(message)
    }
}

#[cfg(target_os = "linux")]
impl std::fmt::Display for WorkspaceApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

#[cfg(target_os = "linux")]
impl std::error::Error for WorkspaceApiError {}

#[cfg(target_os = "linux")]
impl From<WorkspaceApiError> for eos_command_session::CommandSessionError {
    fn from(error: WorkspaceApiError) -> Self {
        Self::Workspace(error.to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg(target_os = "linux")]
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

#[cfg(target_os = "linux")]
impl FinalizeCommandRequest {
    #[must_use]
    pub fn command_succeeded(&self) -> bool {
        self.status == "ok" && self.exit_code == Some(0)
    }
}

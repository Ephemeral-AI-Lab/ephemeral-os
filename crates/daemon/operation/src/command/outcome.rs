use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::contract::CommandStatus;
pub use crate::{
    ChangedPathKinds, MutationSource, OpError as WorkspaceApiError, WorkspaceConflict,
    WorkspaceKind, WorkspaceTimings,
};

impl From<WorkspaceApiError> for command::CommandError {
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
    pub status: CommandStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub stdout: String,
    #[serde(default)]
    pub stderr: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_id: Option<String>,
}

impl FinalizeCommandRequest {
    #[must_use]
    pub fn command_succeeded(&self) -> bool {
        self.status == CommandStatus::Ok && self.exit_code == Some(0)
    }
}

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use eos_workspace_api::{WorkspaceCommandOutcome, WorkspaceMode};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandResponse {
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_mode: Option<WorkspaceMode>,
    #[serde(default)]
    pub metadata: Value,
}

impl CommandResponse {
    #[must_use]
    pub fn running(command_session_id: String, stdout: String) -> Self {
        Self {
            status: "running".to_owned(),
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_session_id: Some(command_session_id),
            workspace_mode: None,
            metadata: Value::Null,
        }
    }

    #[must_use]
    pub fn error(stderr: impl Into<String>) -> Self {
        Self {
            status: "error".to_owned(),
            exit_code: None,
            stdout: String::new(),
            stderr: stderr.into(),
            command_session_id: None,
            workspace_mode: None,
            metadata: Value::Null,
        }
    }

    #[must_use]
    pub fn from_workspace_outcome(outcome: WorkspaceCommandOutcome) -> Self {
        Self {
            status: outcome.status,
            exit_code: outcome.exit_code,
            stdout: outcome.stdout,
            stderr: outcome.stderr,
            command_session_id: outcome.command_session_id,
            workspace_mode: Some(outcome.mode),
            metadata: json!({
                "success": outcome.success,
                "changed_paths": outcome.changed_paths,
                "changed_path_kinds": outcome.changed_path_kinds,
                "mutation_source": outcome.mutation_source,
                "conflict": outcome.conflict,
                "conflict_reason": outcome.conflict_reason,
                "timings": outcome.timings,
                "metadata": outcome.metadata,
            }),
        }
    }

    #[must_use]
    pub fn with_stdout(mut self, stdout: String) -> Self {
        self.stdout = stdout;
        self
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectCompletedResponse {
    pub success: bool,
    pub completions: Vec<crate::CommandSessionCompletion>,
}

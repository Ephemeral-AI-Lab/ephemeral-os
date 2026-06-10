use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::contract::mode::WorkspaceMode;
use crate::contract::response::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};

/// Input needed for a workspace-mode module to prepare command execution.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PrepareCommandRequest {
    pub caller_id: String,
    pub command_session_id: String,
    pub invocation_id: String,
    pub cmd: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_seconds: Option<f64>,
}

/// Prepared workspace context returned to daemon-owned command-session control.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PreparedCommandWorkspace {
    pub run_request: Value,
    pub request_path: PathBuf,
    pub output_path: PathBuf,
    pub final_path: PathBuf,
    pub session_dir: PathBuf,
    pub transcript_path: PathBuf,
}

/// Input needed for mode-specific command workspace finalization.
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
    /// True only when the runner reports an explicitly successful command.
    #[must_use]
    pub fn command_succeeded(&self) -> bool {
        self.status == "ok" && self.exit_code == Some(0)
    }
}

/// Normalized command outcome before daemon persistence/parking.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceCommandOutcome {
    pub mode: WorkspaceMode,
    pub success: bool,
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub stdout: String,
    #[serde(default)]
    pub stderr: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_session_id: Option<String>,
    #[serde(default)]
    pub changed_paths: Vec<String>,
    #[serde(default)]
    pub changed_path_kinds: ChangedPathKinds,
    #[serde(default)]
    pub mutation_source: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict: Option<WorkspaceConflict>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_reason: Option<String>,
    #[serde(default)]
    pub timings: WorkspaceTimings,
    #[serde(default)]
    pub metadata: Value,
}

impl WorkspaceCommandOutcome {
    /// Outcome for a discarded (cancelled) command workspace: it carries the
    /// command's status and output but no published paths, because a cancelled
    /// command never merges into the shared workspace.
    #[must_use]
    pub fn discarded(mode: WorkspaceMode, request: FinalizeCommandRequest) -> Self {
        Self {
            mode,
            success: false,
            status: request.status,
            exit_code: request.exit_code,
            stdout: request.stdout,
            stderr: request.stderr,
            command_session_id: request.command_session_id,
            changed_paths: Vec::new(),
            changed_path_kinds: ChangedPathKinds::default(),
            mutation_source: String::new(),
            conflict: None,
            conflict_reason: None,
            timings: WorkspaceTimings::default(),
            metadata: Value::Null,
        }
    }
}

//! Settled-command vocabulary: the typed outcome a finished command session
//! produces before the daemon wire layer shapes the envelope.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Workspace mode that produced a result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceMode {
    /// Shared publish-capable workspace path.
    #[default]
    Ephemeral,
    /// Caller-private no-publish workspace path.
    Isolated,
}

impl WorkspaceMode {
    /// Stable daemon/API string for this mode.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ephemeral => "ephemeral",
            Self::Isolated => "isolated",
        }
    }
}

/// Timing/telemetry map keyed by stable wire strings.
pub type WorkspaceTimings = BTreeMap<String, Value>;

/// `path -> kind` map for captured changes (wire-stable kind strings).
pub type ChangedPathKinds = BTreeMap<String, String>;

/// A per-path publish conflict surfaced on the response envelope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceConflict {
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_file: Option<String>,
    pub message: String,
}

impl WorkspaceConflict {
    #[must_use]
    pub fn path(reason: &str, conflict_file: &str, message: &str) -> Self {
        Self {
            reason: reason.to_owned(),
            conflict_file: Some(conflict_file.to_owned()),
            message: message.to_owned(),
        }
    }
}

/// Command-tier API error carrying a stable wire kind.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceApiError {
    pub kind: String,
    pub message: String,
}

impl WorkspaceApiError {
    #[must_use]
    pub fn new(kind: &str, message: String) -> Self {
        Self {
            kind: kind.to_owned(),
            message,
        }
    }
}

impl std::fmt::Display for WorkspaceApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for WorkspaceApiError {}

impl From<WorkspaceApiError> for eos_command_session::CommandSessionError {
    fn from(error: WorkspaceApiError) -> Self {
        Self::Workspace(error.to_string())
    }
}

/// Input needed for mode-specific command settle.
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

//! The op contract: request DTOs in, response DTOs out, and the error type
//! that joins them.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum CommandSessionError {
    /// A workspace-tier failure surfaced through the session lifecycle; the
    /// substrate carries only the rendered message.
    #[error("{0}")]
    Workspace(String),
    #[error("command session not found: {0}")]
    NotFound(String),
    #[error("invalid command session request: {0}")]
    InvalidRequest(String),
    #[error("unsupported command session operation: {0}")]
    Unsupported(String),
    #[error("command session io error: {0}")]
    Io(String),
}

impl From<std::io::Error> for CommandSessionError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error.to_string())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct StartCommandSession {
    pub invocation_id: String,
    pub caller_id: String,
    pub cmd: String,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteStdin {
    pub command_session_id: String,
    pub chars: String,
    pub yield_time_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCommandProgress {
    pub command_session_id: String,
    pub last_n_lines: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelCommandSession {
    pub command_session_id: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CollectCompleted {
    pub command_session_ids: Option<Vec<String>>,
    pub caller_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandResponse {
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command_session_id: Option<String>,
    /// Stable mode string (`"ephemeral"` / `"isolated"`) when the response
    /// settled a workspace-bound command; the substrate carries it opaquely.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_mode: Option<String>,
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
    pub fn cancelled(stdout: String) -> Self {
        Self {
            status: "cancelled".to_owned(),
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_session_id: None,
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
    pub fn with_last_lines(mut self, last_n_lines: usize) -> Self {
        self.stdout = tail_lines(&self.stdout, last_n_lines);
        self
    }

    #[must_use]
    pub fn to_wire_value(&self) -> Value {
        let mut response = json!({
            "status": self.status,
            "exit_code": self.exit_code,
            "output": {
                "stdout": self.stdout,
                "stderr": self.stderr,
            },
        });
        if let Some(command_session_id) = self.command_session_id.as_ref() {
            response["command_session_id"] = json!(command_session_id);
        }
        let Some(mode) = self.workspace_mode.as_deref() else {
            return response;
        };
        response["success"] = self
            .metadata
            .get("success")
            .cloned()
            .unwrap_or_else(|| json!(self.status == "ok"));
        response["workspace"] = json!(mode);
        response["workspace_mode"] = json!(mode);
        response["stdout"] = json!(self.stdout);
        response["stderr"] = json!(self.stderr);
        response["conflict"] = self
            .metadata
            .get("conflict")
            .cloned()
            .unwrap_or(Value::Null);
        response["conflict_reason"] = self
            .metadata
            .get("conflict_reason")
            .cloned()
            .unwrap_or(Value::Null);
        response["changed_paths"] = self
            .metadata
            .get("changed_paths")
            .cloned()
            .unwrap_or_else(|| json!([]));
        response["changed_path_kinds"] = self
            .metadata
            .get("changed_path_kinds")
            .cloned()
            .unwrap_or_else(|| json!({}));
        response["mutation_source"] = self
            .metadata
            .get("mutation_source")
            .cloned()
            .unwrap_or_else(|| json!(""));
        response["error"] = Value::Null;
        response["timings"] = self
            .metadata
            .get("timings")
            .cloned()
            .unwrap_or_else(|| json!({}));
        if let Some(metadata) = self.metadata.get("metadata").and_then(Value::as_object) {
            for (key, value) in metadata {
                response[key] = value.clone();
            }
        }
        response
    }
}

/// A settled command session parked for the agent-core heartbeat to drain. The
/// daemon's workspace-run registry owns the completion queue; this is the shared
/// DTO it stores and the wire layer serializes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandSessionCompletion {
    pub command_session_id: String,
    pub caller_id: String,
    pub command: String,
    pub result: CommandResponse,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectCompletedResponse {
    pub success: bool,
    pub completions: Vec<CommandSessionCompletion>,
}

/// Last `last_n_lines` lines of `text`, also used by the transcript reader
/// for progress tails.
#[must_use]
pub(crate) fn tail_lines(text: &str, last_n_lines: usize) -> String {
    if text.is_empty() || last_n_lines == 0 {
        return String::new();
    }
    let mut line_starts = vec![0_usize];
    for (idx, byte) in text.bytes().enumerate() {
        if byte == b'\n' && idx + 1 < text.len() {
            line_starts.push(idx + 1);
        }
    }
    let start_idx = line_starts
        .len()
        .saturating_sub(last_n_lines)
        .min(line_starts.len().saturating_sub(1));
    text[line_starts[start_idx]..].to_owned()
}

#[cfg(test)]
#[path = "../tests/unit/contract.rs"]
mod tests;

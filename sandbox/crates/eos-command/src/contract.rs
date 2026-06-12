//! Command-session substrate request DTOs and error type.

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
    pub command_id: String,
    pub chars: String,
    pub yield_time_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCommandProgress {
    pub command_id: String,
    pub last_n_lines: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelCommandSession {
    pub command_id: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CollectCompleted {
    pub command_ids: Option<Vec<String>>,
    pub caller_id: Option<String>,
}

/// Last `last_n_lines` lines of `text`, also used by the transcript reader
/// for progress tails.
#[must_use]
pub fn tail_lines(text: &str, last_n_lines: usize) -> String {
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

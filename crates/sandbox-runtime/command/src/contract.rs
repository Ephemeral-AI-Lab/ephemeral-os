//! Command substrate request DTOs and error type.

use std::path::{Path, PathBuf};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum CommandError {
    /// A workspace-tier failure surfaced through the command lifecycle; the
    /// substrate carries only the rendered message.
    #[error("{0}")]
    Workspace(String),
    #[error("command not found: {0}")]
    NotFound(String),
    #[error("invalid command request: {0}")]
    InvalidRequest(String),
    #[error("command io error: {0}")]
    Io(String),
    #[error("command artifact write failed for {artifact} at {}: {error}", path.display())]
    ArtifactWrite {
        artifact: &'static str,
        path: PathBuf,
        error: String,
    },
}

impl From<std::io::Error> for CommandError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error.to_string())
    }
}

impl CommandError {
    #[must_use]
    pub fn artifact_write(
        artifact: &'static str,
        path: impl AsRef<Path>,
        error: impl std::fmt::Display,
    ) -> Self {
        Self::ArtifactWrite {
            artifact,
            path: path.as_ref().to_path_buf(),
            error: error.to_string(),
        }
    }
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

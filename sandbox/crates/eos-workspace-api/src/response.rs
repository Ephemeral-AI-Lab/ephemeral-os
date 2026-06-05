use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

/// JSON-valued timing map. Daemon adapters keep their exact timing key names.
pub type WorkspaceTimings = BTreeMap<String, Value>;

/// Per-path mutation kind map.
pub type ChangedPathKinds = BTreeMap<String, String>;

/// Guarded-operation conflict detail.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceConflict {
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_file: Option<String>,
    pub message: String,
}

impl WorkspaceConflict {
    #[must_use]
    pub fn path(
        reason: impl Into<String>,
        path: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            reason: reason.into(),
            conflict_file: Some(path.into()),
            message: message.into(),
        }
    }
}

/// Error at the workspace capability boundary.
#[derive(Debug, Clone, PartialEq, Eq, Error, Serialize, Deserialize)]
#[error("{code}: {message}")]
pub struct WorkspaceApiError {
    pub code: String,
    pub message: String,
}

impl WorkspaceApiError {
    #[must_use]
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }

    #[must_use]
    pub fn invalid_request(message: impl Into<String>) -> Self {
        Self::new("invalid_request", message)
    }

    #[must_use]
    pub fn io(message: impl Into<String>) -> Self {
        Self::new("io_error", message)
    }
}

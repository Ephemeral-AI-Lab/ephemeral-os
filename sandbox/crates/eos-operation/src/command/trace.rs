use std::path::Path;

use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq)]
pub struct CommandTraceEvent {
    pub name: &'static str,
    pub details: Value,
}

impl CommandTraceEvent {
    #[must_use]
    pub fn new(name: &'static str, details: Value) -> Self {
        Self { name, details }
    }

    #[must_use]
    pub fn artifact_written(artifact: &'static str, path: &Path, bytes: usize) -> Self {
        Self::new(
            "artifact_written",
            json!({
                "artifact": artifact,
                "path": path.display().to_string(),
                "bytes": bytes,
            }),
        )
    }
}

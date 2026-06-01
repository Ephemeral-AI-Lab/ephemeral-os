//! Append-only JSONL audit sink for isolated-workspace lifecycle events.
//!
//! AUDIT ONLY. This sink records enter/exit/teardown events for forensic and
//! test consumption; it NEVER feeds an OCC publish path (the no-publish
//! invariant — see the crate root). Each `emit` writes one line shaped
//! `{"ts": <float>, "type": <event_type>, "payload": <payload>}`.
//! `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:55-70 — _JsonlAuditSink`

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::error::IsolatedError;

/// Default JSONL path when `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` is unset.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:32`
pub const DEFAULT_AUDIT_JSONL_PATH: &str = "/tmp/sandbox_isolated_workspace_events.jsonl";

/// Environment variable selecting the audit JSONL path.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:104`
pub const AUDIT_PATH_ENV: &str = "EOS_ISOLATED_WORKSPACE_AUDIT_PATH";

/// Sink for isolated-workspace audit events.
///
/// The only production implementation is the JSONL sink; the trait exists so
/// tests can substitute a recording double without touching the filesystem.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:99-100 — IsolatedWorkspaceAuditSink Protocol`
pub trait AuditSink {
    /// Record one lifecycle event with its structured payload.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError::AuditWrite`] when the sink cannot persist the
    /// event.
    fn emit(&self, event_type: &str, payload: Value) -> Result<(), IsolatedError>;
}

/// Append-only JSONL audit sink. Audit-only; no OCC linkage.
#[derive(Debug, Clone)]
pub struct JsonlAuditSink {
    path: PathBuf,
}

impl JsonlAuditSink {
    /// Build a sink writing to `path`.
    #[must_use]
    pub fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }

    /// Build a sink from `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`, falling back to
    /// [`DEFAULT_AUDIT_JSONL_PATH`] when unset or blank.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:103-106 — audit path env resolution
    #[must_use]
    pub fn from_env() -> Self {
        let path = std::env::var(AUDIT_PATH_ENV)
            .unwrap_or_default()
            .trim()
            .to_owned();
        if path.is_empty() {
            Self::new(DEFAULT_AUDIT_JSONL_PATH)
        } else {
            Self::new(path)
        }
    }
}

impl AuditSink for JsonlAuditSink {
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:69-70 — append one {"ts","type","payload"} JSONL line
    fn emit(&self, event_type: &str, payload: Value) -> Result<(), IsolatedError> {
        if let Some(parent) = self
            .path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent).map_err(|source| IsolatedError::AuditWrite {
                path: self.path.clone(),
                source,
            })?;
        }
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0.0, |duration| duration.as_secs_f64());
        let line = serde_json::json!({
            "ts": ts,
            "type": event_type,
            "payload": payload,
        });
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|source| IsolatedError::AuditWrite {
                path: self.path.clone(),
                source,
            })?;
        serde_json::to_writer(&mut file, &line).map_err(|source| IsolatedError::AuditWrite {
            path: self.path.clone(),
            source: std::io::Error::other(source),
        })?;
        file.write_all(b"\n")
            .map_err(|source| IsolatedError::AuditWrite {
                path: self.path.clone(),
                source,
            })
    }
}

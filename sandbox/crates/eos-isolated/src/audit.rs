//! Append-only JSONL audit sink for isolated-workspace lifecycle events.
//!
//! AUDIT ONLY. This sink records enter/exit/teardown events for forensic and
//! test consumption; it NEVER feeds an OCC publish path (the no-publish
//! invariant — see the crate root). Each `emit` writes one line shaped
//! `{"ts": <float>, "type": <event_type>, "payload": <payload>}`.
//! `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:55-70 — _JsonlAuditSink`

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::error::IsolatedError;

/// Default JSONL path when `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` is unset.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:32`
pub const DEFAULT_AUDIT_JSONL_PATH: &str = "/tmp/sandbox_isolated_workspace_events.jsonl";

/// Environment variable selecting the audit JSONL path.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:104`
pub const AUDIT_PATH_ENV: &str = "EOS_ISOLATED_WORKSPACE_AUDIT_PATH";

/// Sink for isolated-workspace audit events. The only implementation is the
/// JSONL sink; the trait exists so tests can substitute a recording double
/// without touching the filesystem.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:99-100 — IsolatedWorkspaceAuditSink Protocol`
pub trait AuditSink {
    /// Record one lifecycle event with its structured payload.
    fn emit(&self, event_type: &str, payload: Value) -> Result<(), IsolatedError>;
}

/// Append-only JSONL audit sink. Audit-only; no OCC linkage.
#[derive(Debug, Clone)]
pub struct JsonlAuditSink {
    path: PathBuf,
}

impl JsonlAuditSink {
    /// Build a sink writing to `path`.
    pub fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }

    /// Build a sink from `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`, falling back to
    /// [`DEFAULT_AUDIT_JSONL_PATH`] when unset or blank.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:103-106 — audit path env resolution
    pub fn from_env() -> Self {
        todo!("PORT pipeline_registry.py:103-106 — read AUDIT_PATH_ENV or DEFAULT_AUDIT_JSONL_PATH")
    }
}

impl AuditSink for JsonlAuditSink {
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py:69-70 — append one {"ts","type","payload"} JSONL line
    fn emit(&self, _event_type: &str, _payload: Value) -> Result<(), IsolatedError> {
        let _ = &self.path;
        todo!("PORT pipeline_registry.py:69-70 — append_jsonl_event with ts/type/payload")
    }
}

use std::path::Path;

use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::Value;

use crate::error::NamespaceExecutionError;
use crate::status::NamespaceExecutionTerminalStatus;

/// One wire result for both families (newtype over the runner's `RunResult`),
/// plus the engine-side `cancelled` knowledge. When the execution was cancelled,
/// `status()`/`exit_code()` override the wire parse to `Cancelled`/`130`, so a
/// signal-killed child reports a clean cancel rather than a raw error.
pub struct RunnerOutcome {
    result: RunResult,
    cancelled: bool,
}

impl RunnerOutcome {
    pub fn new(result: RunResult) -> Self {
        Self {
            result,
            cancelled: false,
        }
    }

    /// Mark this outcome as cancelled (the engine-side cancel knowledge), so
    /// `status()`/`exit_code()` override to `Cancelled`/`130`. Kept off the
    /// constructor so the mount family's `RunnerOutcome::new` stays unchanged.
    #[must_use]
    pub fn with_cancelled(mut self, cancelled: bool) -> Self {
        self.cancelled = cancelled;
        self
    }

    pub fn exit_code(&self) -> i64 {
        if self.cancelled {
            130
        } else {
            i64::from(self.result.exit_code)
        }
    }

    /// Project the wire `payload.status` string onto the terminal status enum,
    /// defaulting to `Error` when absent or unrecognized. A cancelled execution
    /// overrides to `Cancelled` (the engine knows the cancel; the wire does not).
    pub fn status(&self) -> NamespaceExecutionTerminalStatus {
        if self.cancelled {
            return NamespaceExecutionTerminalStatus::Cancelled;
        }
        match self.result.payload.get("status").and_then(Value::as_str) {
            Some("ok") => NamespaceExecutionTerminalStatus::Ok,
            Some("error") => NamespaceExecutionTerminalStatus::Error,
            Some("timed_out") => NamespaceExecutionTerminalStatus::TimedOut,
            Some("cancelled") => NamespaceExecutionTerminalStatus::Cancelled,
            _ => NamespaceExecutionTerminalStatus::Error,
        }
    }

    pub fn payload(&self) -> &Value {
        &self.result.payload
    }
}

/// Shell family (`Run` mode → `shell_exec`). Each shell op is a strategy.
pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn timeout_seconds(&self) -> Option<f64>;
    /// Optional file sink for the PTY transcript. `Some(path)` routes the reader
    /// thread to append timestamp-prefixed bytes to `path` (the command's
    /// file-backed transcript); `None` keeps the in-memory buffer.
    fn transcript_path(&self) -> Option<&Path> {
        None
    }
    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<Self::Output, NamespaceExecutionError>;
}

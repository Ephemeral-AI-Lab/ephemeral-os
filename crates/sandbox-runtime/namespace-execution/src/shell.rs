use std::path::Path;

use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::Value;

use crate::error::NamespaceExecutionError;
use crate::status::NamespaceExecutionTerminalStatus;

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

pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn timeout_seconds(&self) -> Option<f64>;
    fn transcript_path(&self) -> Option<&Path> {
        None
    }
    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<Self::Output, NamespaceExecutionError>;
}

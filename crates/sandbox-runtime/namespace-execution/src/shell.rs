use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::Value;

use crate::error::NamespaceExecutionError;
use crate::status::NamespaceExecutionTerminalStatus;

/// One wire result for both families (newtype over the runner's `RunResult`).
pub struct RunnerOutcome(RunResult);

impl RunnerOutcome {
    pub(crate) fn new(result: RunResult) -> Self {
        Self(result)
    }

    pub fn exit_code(&self) -> i64 {
        i64::from(self.0.exit_code)
    }

    /// Project the wire `payload.status` string onto the terminal status enum,
    /// defaulting to `Error` when absent or unrecognized. Pure wire parse — the
    /// cancel→`"cancelled"` override is a command-`finalize` concern (Phase 3).
    pub fn status(&self) -> NamespaceExecutionTerminalStatus {
        match self.0.payload.get("status").and_then(Value::as_str) {
            Some("ok") => NamespaceExecutionTerminalStatus::Ok,
            Some("error") => NamespaceExecutionTerminalStatus::Error,
            Some("timed_out") => NamespaceExecutionTerminalStatus::TimedOut,
            Some("cancelled") => NamespaceExecutionTerminalStatus::Cancelled,
            _ => NamespaceExecutionTerminalStatus::Error,
        }
    }

    pub fn payload(&self) -> &Value {
        &self.0.payload
    }
}

/// Shell family (`Run` mode → `shell_exec`). Each shell op is a strategy.
pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn timeout_seconds(&self) -> Option<f64>;
    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<Self::Output, NamespaceExecutionError>;
}

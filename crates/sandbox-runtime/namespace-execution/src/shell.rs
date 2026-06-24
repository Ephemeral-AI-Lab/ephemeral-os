use sandbox_runtime_namespace_process::runner::protocol::RunResult;

use crate::error::NamespaceExecutionError;

/// One wire result for both families (newtype over the runner's `RunResult`).
pub struct RunnerOutcome(RunResult);

impl RunnerOutcome {
    pub fn exit_code(&self) -> i64 {
        i64::from(self.0.exit_code)
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

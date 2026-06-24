use crate::id::NamespaceExecutionId;
use crate::status::NamespaceExecutionTerminalStatus;

pub trait ExecutionObserver: Send + Sync {
    fn on_running(&self, id: &NamespaceExecutionId);
    fn on_terminal(
        &self,
        id: &NamespaceExecutionId,
        status: NamespaceExecutionTerminalStatus,
        exit_code: Option<i64>,
    );
}

#[derive(Debug, Default)]
pub struct NoopObserver;

impl ExecutionObserver for NoopObserver {
    fn on_running(&self, _id: &NamespaceExecutionId) {}

    fn on_terminal(
        &self,
        _id: &NamespaceExecutionId,
        _status: NamespaceExecutionTerminalStatus,
        _exit_code: Option<i64>,
    ) {
    }
}

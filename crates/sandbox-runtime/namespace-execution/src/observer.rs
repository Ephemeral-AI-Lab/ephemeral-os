use crate::id::NamespaceExecutionId;

/// Drives running/terminal lifecycle by id. `begin` stays in the operation layer
/// (it owns the `WorkspaceSessionId`), so the engine needs no workspace knowledge.
pub trait ExecutionObserver: Send + Sync {
    fn on_running(&self, id: &NamespaceExecutionId);
}

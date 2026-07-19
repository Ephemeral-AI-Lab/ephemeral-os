use crate::workspace_crate::WorkspaceSessionId;

pub use sandbox_runtime_namespace_execution::{
    NamespaceExecutionId, NamespaceExecutionTerminalStatus,
};

/// Narrow ownership port used by workspace teardown. The workspace service
/// knows only which admitted command ids must be drained; command execution
/// owns the concrete engine handles, cancellation, and bounded joins.
pub(crate) trait WorkspaceCommandTeardown: Send + Sync {
    fn cancel_and_join(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        command_ids: &[NamespaceExecutionId],
    ) -> Result<(), String>;

    fn release_terminal(&self, workspace_session_id: &WorkspaceSessionId) -> usize;
}

#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeNamespaceExecutionSnapshot {
    pub namespace_execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub operation_name: String,
    pub command: Option<String>,
}

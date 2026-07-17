use crate::workspace_crate::WorkspaceSessionId;

pub use sandbox_runtime_namespace_execution::{
    NamespaceExecutionId, NamespaceExecutionTerminalStatus,
};

#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeNamespaceExecutionSnapshot {
    pub namespace_execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub operation_name: String,
    pub command: Option<String>,
}

use crate::command::CommandSessionId;
use crate::workspace_crate::WorkspaceSessionId;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InspectCgroupMonitorInput {
    pub workspace_session_id: WorkspaceSessionId,
    pub command_session_id: Option<CommandSessionId>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCgroupMonitorSamplesInput {
    pub workspace_session_id: WorkspaceSessionId,
    pub command_session_id: Option<CommandSessionId>,
    pub limit: Option<usize>,
}

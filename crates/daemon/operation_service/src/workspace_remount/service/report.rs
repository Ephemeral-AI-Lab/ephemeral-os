use crate::workspace_crate::WorkspaceId;
use crate::workspace_remount::CommandRemountInspection;
use crate::workspace_session::WorkspaceSessionHandler;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountReport {
    pub workspace_session_id: WorkspaceId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

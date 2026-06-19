use crate::workspace_crate::WorkspaceId;
use crate::workspace_remount::CommandRemountQuiesce;

pub trait CommandRemountCoordinator: Send + Sync {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> CommandRemountQuiesce;
}

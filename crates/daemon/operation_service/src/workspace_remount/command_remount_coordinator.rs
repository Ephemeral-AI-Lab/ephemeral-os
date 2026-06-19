use crate::command::CommandOperationService;
use crate::workspace_crate::WorkspaceId;
use crate::workspace_remount::{CommandRemountCoordinator, CommandRemountQuiesce};

impl CommandRemountCoordinator for CommandOperationService {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> CommandRemountQuiesce {
        self.begin_remount_quiesce_for_workspace_session(workspace_session_id)
    }
}

use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_remount::{
    RemountBlockReason, RemountSwitchState, WorkspaceRemountError, WorkspaceRemountOutcome,
    WorkspaceRemountService,
};

impl WorkspaceRemountService {
    pub fn remount_workspace_session(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<WorkspaceRemountOutcome, WorkspaceRemountError> {
        let handler = self.workspace.begin_remount(workspace_session_id.clone())?;
        let mut quiesce = self
            .command
            .begin_workspace_remount_quiesce(&workspace_session_id);

        let blocked_reason = quiesce.inspection().blocked_reason.clone().or_else(|| {
            quiesce
                .cancellation_requested()
                .then(|| RemountBlockReason::RemountCancelledBeforeSwitch.to_string())
        });
        if let Some(reason) = blocked_reason {
            self.workspace.block_remount(workspace_session_id.clone())?;
            let inspection = quiesce.finish();
            return Ok(WorkspaceRemountOutcome {
                workspace_session_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }

        quiesce.set_switch_state(RemountSwitchState::CriticalSwitch);
        let request = RemountWorkspaceRequest {
            layer_paths: handler.handle.snapshot.layer_paths.clone(),
        };
        let remount_result = self.workspace.apply_and_finish_remount(&handler, request);
        quiesce.set_switch_state(RemountSwitchState::Resuming);

        match remount_result {
            Ok(updated_handler) => {
                let inspection = quiesce.finish();
                Ok(WorkspaceRemountOutcome {
                    workspace_session_id,
                    remounted: true,
                    blocked_reason: None,
                    command_inspection: inspection,
                    updated_handler: Some(updated_handler),
                })
            }
            Err(error) => {
                let _ = quiesce.finish();
                Err(WorkspaceRemountError::WorkspaceSession(error))
            }
        }
    }
}

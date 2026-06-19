use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_remount::{
    RemountSwitchState, WorkspaceRemountError, WorkspaceRemountReport, WorkspaceRemountService,
};

impl WorkspaceRemountService {
    pub fn remount_workspace_session(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<WorkspaceRemountReport, WorkspaceRemountError> {
        let handler = self
            .workspace()
            .begin_remount(workspace_session_id.clone())?;
        let mut quiesce = self
            .command()
            .begin_workspace_remount_quiesce(&workspace_session_id);

        if let Some(reason) = quiesce.inspection().blocked_reason.clone() {
            self.workspace()
                .finish_or_block_remount(workspace_session_id.clone(), Some(reason.clone()))?;
            let inspection = quiesce.finish();
            return Ok(WorkspaceRemountReport {
                workspace_session_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }

        if quiesce.cancellation_requested() {
            let reason = "remount_cancelled_before_switch".to_owned();
            self.workspace()
                .finish_or_block_remount(workspace_session_id.clone(), Some(reason.clone()))?;
            let inspection = quiesce.finish();
            return Ok(WorkspaceRemountReport {
                workspace_session_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }

        quiesce.set_switch_state(RemountSwitchState::CriticalSwitch);
        let request = RemountWorkspaceRequest {
            layer_paths: handler.layer_paths.clone(),
        };
        let remount_result = self.workspace().apply_remount(&handler, request);
        quiesce.set_switch_state(RemountSwitchState::Resuming);

        match remount_result {
            Ok(updated_handler) => {
                self.workspace()
                    .finish_remount(workspace_session_id.clone())?;
                let inspection = quiesce.finish();
                Ok(WorkspaceRemountReport {
                    workspace_session_id,
                    remounted: true,
                    blocked_reason: None,
                    command_inspection: inspection,
                    updated_handler: Some(updated_handler),
                })
            }
            Err(error) => {
                let reason = error.to_string();
                if self.workspace().is_remount_pending(&workspace_session_id) {
                    self.workspace()
                        .finish_or_block_remount(workspace_session_id.clone(), Some(reason))?;
                }
                let _inspection = quiesce.finish();
                Err(WorkspaceRemountError::WorkspaceSession(error))
            }
        }
    }
}

use std::sync::Arc;

use crate::command::{CommandOperationService, CommandRemountInspection, RemountSwitchState};
use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_manager::{WorkspaceManagerService, WorkspaceSessionHandler};
use crate::workspace_remount::WorkspaceRemountError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkspaceRemountOptions {
    pub live_quiesce_timeout_ms: u64,
}

impl Default for WorkspaceRemountOptions {
    fn default() -> Self {
        Self {
            live_quiesce_timeout_ms: 30_000,
        }
    }
}

pub struct WorkspaceRemountService {
    workspace: Arc<WorkspaceManagerService>,
    command: Arc<CommandOperationService>,
    options: WorkspaceRemountOptions,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountReport {
    pub workspace_id: WorkspaceId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

impl WorkspaceRemountService {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceManagerService>,
        command: Arc<CommandOperationService>,
        options: WorkspaceRemountOptions,
    ) -> Self {
        Self {
            workspace,
            command,
            options,
        }
    }

    #[must_use]
    pub const fn options(&self) -> WorkspaceRemountOptions {
        self.options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<WorkspaceManagerService> {
        &self.workspace
    }

    #[must_use]
    pub fn command(&self) -> &Arc<CommandOperationService> {
        &self.command
    }

    pub fn compact_or_remount_session(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<WorkspaceRemountReport, WorkspaceRemountError> {
        let handler = self.workspace.begin_remount(workspace_id.clone())?;
        let mut quiesce = self.command.begin_workspace_remount_quiesce(&workspace_id);

        if let Some(reason) = quiesce.inspection().blocked_reason.clone() {
            let inspection = quiesce.finish();
            self.workspace
                .finish_or_block_remount(workspace_id.clone(), Some(reason.clone()))?;
            return Ok(WorkspaceRemountReport {
                workspace_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }

        if quiesce.cancellation_requested() {
            let reason = "remount_cancelled_before_switch".to_owned();
            let inspection = quiesce.finish();
            self.workspace
                .finish_or_block_remount(workspace_id.clone(), Some(reason.clone()))?;
            return Ok(WorkspaceRemountReport {
                workspace_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }

        quiesce.set_switch_state(RemountSwitchState::CriticalSwitch);
        if quiesce.cancellation_requested() {
            let reason = "remount_cancelled_before_switch".to_owned();
            let inspection = quiesce.finish();
            self.workspace
                .finish_or_block_remount(workspace_id.clone(), Some(reason.clone()))?;
            return Ok(WorkspaceRemountReport {
                workspace_id,
                remounted: false,
                blocked_reason: Some(reason),
                command_inspection: inspection,
                updated_handler: None,
            });
        }
        let request = RemountWorkspaceRequest {
            layer_paths: handler.layer_paths.clone(),
        };
        let remount_result = self.workspace.apply_remount(&handler, request);
        quiesce.set_switch_state(RemountSwitchState::Resuming);

        match remount_result {
            Ok(updated_handler) => {
                let inspection = quiesce.finish();
                self.workspace.finish_remount(workspace_id.clone())?;
                Ok(WorkspaceRemountReport {
                    workspace_id,
                    remounted: true,
                    blocked_reason: None,
                    command_inspection: inspection,
                    updated_handler: Some(updated_handler),
                })
            }
            Err(error) => {
                let reason = error.to_string();
                let _inspection = quiesce.finish();
                self.workspace
                    .finish_or_block_remount(workspace_id, Some(reason))?;
                Err(WorkspaceRemountError::WorkspaceManager(error))
            }
        }
    }
}

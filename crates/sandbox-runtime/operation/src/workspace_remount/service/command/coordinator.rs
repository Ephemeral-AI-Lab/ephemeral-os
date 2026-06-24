use std::sync::Arc;

use sandbox_runtime_namespace_execution::NamespaceExecutionId;

use super::quiesce::merge_report;
use super::{
    CommandRemountInspection, CommandRemountQuiesce, RemountBlockReason, RemountCancellationToken,
    RemountSwitchState,
};
use crate::command::CommandOperationService;
use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_remount::CommandRemountCoordinator;

impl CommandRemountCoordinator for CommandOperationService {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> CommandRemountQuiesce {
        let _admission_guard = self.begin_workspace_lifecycle_admission();
        let command_session_ids = self.live_command_session_ids_for_workspace(workspace_session_id);
        let cancellation = RemountCancellationToken::new();
        let mut quiesce = CommandRemountQuiesce {
            inspection: CommandRemountInspection {
                active_commands: command_session_ids.len(),
                ..CommandRemountInspection::default()
            },
            held_process_group_ids: Vec::new(),
            command_session_ids: Vec::new(),
            engine: Arc::clone(self.engine()),
            cancellation,
            switch_state: RemountSwitchState::Quiescing,
            controller: self.remount_controller(),
        };
        if command_session_ids.is_empty() {
            quiesce.switch_state = RemountSwitchState::ReadyToSwitch;
            return quiesce;
        }

        for command_session_id in command_session_ids {
            quiesce
                .inspection
                .command_session_ids
                .push(command_session_id.clone());
            let id = NamespaceExecutionId(command_session_id.0.clone());
            let Some((pgid, workspace_root)) = self.engine().with_value(&id, |command| {
                (command.pgid(), command.workspace_root().to_path_buf())
            }) else {
                quiesce
                    .inspection
                    .block_if_clear(RemountBlockReason::ActiveCommandMissing);
                continue;
            };
            let Some(pgid) = pgid else {
                quiesce
                    .inspection
                    .block_if_clear(RemountBlockReason::ProcessGroupUnavailable);
                continue;
            };
            quiesce.inspection.process_group_ids.push(pgid);
            quiesce.command_session_ids.push(command_session_id.clone());
            let command_report = quiesce
                .controller
                .inspect_command_process_group(pgid, &workspace_root);
            let held = command_report.blocked_reason.is_none();
            merge_report(&mut quiesce.inspection, command_report);
            if held {
                quiesce.held_process_group_ids.push(pgid);
            }
        }

        quiesce.inspection.command_session_ids.sort();
        quiesce.inspection.command_session_ids.dedup();
        quiesce.inspection.process_group_ids.sort_unstable();
        quiesce.inspection.process_group_ids.dedup();
        quiesce.set_switch_state(RemountSwitchState::ReadyToSwitch);
        if !quiesce.inspection.can_live_remount() {
            quiesce.resume();
        }
        quiesce
    }
}

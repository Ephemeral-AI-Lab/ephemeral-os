use std::sync::Arc;

use sandbox_runtime_namespace_execution::NamespaceExecutionId;

use super::{
    CommandRemountInspection, CommandRemountQuiesce, RemountBlockReason, RemountCancellationToken,
    RemountSwitchState,
};
use crate::command::{CommandOperationService, CommandSessionId};
use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_remount::CommandRemountCoordinator;

impl CommandRemountCoordinator for CommandOperationService {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> CommandRemountQuiesce {
        let _admission_guard = self.begin_workspace_lifecycle_admission();
        let workspace_root = self
            .resolve_workspace_session(workspace_session_id.clone())
            .map(|handler| handler.handle.workspace_root)
            .ok();
        let mut live = self.engine().live_values(|command| {
            (command.workspace_session_id() == workspace_session_id)
                .then(|| (command.id().clone(), command.pgid()))
        });
        live.sort_by(|left, right| left.0.cmp(&right.0));
        let cancellation = RemountCancellationToken::new();
        let mut quiesce = CommandRemountQuiesce {
            inspection: CommandRemountInspection {
                active_commands: live.len(),
                ..CommandRemountInspection::default()
            },
            held_process_group_ids: Vec::new(),
            affected: Vec::new(),
            engine: Arc::clone(self.engine()),
            cancellation,
            switch_state: RemountSwitchState::Quiescing,
            controller: self.remount_controller(),
        };
        if live.is_empty() {
            quiesce.switch_state = RemountSwitchState::ReadyToSwitch;
            return quiesce;
        }

        let Some(workspace_root) = workspace_root.as_deref() else {
            quiesce
                .inspection
                .block_if_clear(RemountBlockReason::ActiveCommandMissing);
            for (id, _) in live {
                quiesce
                    .inspection
                    .command_session_ids
                    .push(CommandSessionId(id.0));
            }
            quiesce.inspection.command_session_ids.sort();
            quiesce.inspection.command_session_ids.dedup();
            quiesce.set_switch_state(RemountSwitchState::ReadyToSwitch);
            quiesce.resume();
            return quiesce;
        };

        for (id, process_group_id) in live {
            quiesce
                .inspection
                .command_session_ids
                .push(CommandSessionId(id.0.clone()));
            let Some(pgid) = process_group_id else {
                quiesce
                    .inspection
                    .block_if_clear(RemountBlockReason::ProcessGroupUnavailable);
                continue;
            };
            quiesce.inspection.process_group_ids.push(pgid);
            quiesce.affected.push(NamespaceExecutionId(id.0.clone()));
            let command_report = quiesce
                .controller
                .inspect_command_process_group(pgid, workspace_root);
            let held = command_report.blocked_reason.is_none();
            quiesce.inspection.accumulate(command_report);
            if held {
                quiesce.held_process_group_ids.push(pgid);
            }
        }

        quiesce.inspection.command_session_ids.sort();
        quiesce.inspection.command_session_ids.dedup();
        quiesce.inspection.process_group_ids.sort_unstable();
        quiesce.inspection.process_group_ids.dedup();
        quiesce.affected.sort();
        quiesce.affected.dedup();
        quiesce.held_process_group_ids.sort_unstable();
        quiesce.held_process_group_ids.dedup();
        quiesce.set_switch_state(RemountSwitchState::ReadyToSwitch);
        if !quiesce.inspection.can_live_remount() {
            quiesce.resume();
        }
        quiesce
    }
}

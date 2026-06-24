use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use sandbox_runtime_command::process_group::{ProcessGroupController, ProcessGroupInspection};
use sandbox_runtime_command::CommandExecution;
use sandbox_runtime_namespace_execution::{NamespaceExecutionEngine, NamespaceExecutionId};

use crate::command::CommandSessionId;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountInspection {
    pub active_commands: usize,
    pub command_session_ids: Vec<CommandSessionId>,
    pub process_group_ids: Vec<i32>,
    pub process_group: ProcessGroupInspection,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemountBlockReason {
    ActiveCommandMissing,
    ProcessGroupUnavailable,
    RemountCancelledBeforeSwitch,
}

impl RemountBlockReason {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::ActiveCommandMissing => "active_command_missing",
            Self::ProcessGroupUnavailable => "process_group_unavailable",
            Self::RemountCancelledBeforeSwitch => "remount_cancelled_before_switch",
        }
    }
}

impl std::fmt::Display for RemountBlockReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl CommandRemountInspection {
    #[must_use]
    pub fn can_live_remount(&self) -> bool {
        self.active_commands > 0
            && self.process_group.blocked_reason.is_none()
            && self.process_group.inspected
            && self.process_group.quiesce_attempted
            && self.process_group.quiesced_process_count == self.process_group.process_count
    }

    #[must_use]
    pub fn blocked_reason(&self) -> Option<String> {
        self.process_group.blocked_reason.clone()
    }

    pub(crate) fn block_if_clear(&mut self, reason: RemountBlockReason) {
        self.process_group
            .blocked_reason
            .get_or_insert_with(|| reason.to_string());
    }

    pub(crate) fn accumulate(&mut self, report: ProcessGroupInspection) {
        let process_group = &mut self.process_group;
        process_group.process_count += report.process_count;
        process_group.quiesced_process_count += report.quiesced_process_count;
        process_group.pinned_cwd_count += report.pinned_cwd_count;
        process_group.pinned_root_count += report.pinned_root_count;
        process_group.pinned_fd_count += report.pinned_fd_count;
        process_group.pinned_mapped_file_count += report.pinned_mapped_file_count;
        process_group.mountinfo_checked_count += report.mountinfo_checked_count;
        process_group.inspected |= report.inspected;
        process_group.quiesce_attempted |= report.quiesce_attempted;
        process_group.resumed |= report.resumed;
        if process_group.blocked_reason.is_none() {
            process_group.blocked_reason = report.blocked_reason;
        }
        if process_group.detail.is_none() {
            process_group.detail = report.detail;
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemountSwitchState {
    Quiescing,
    ReadyToSwitch,
    CriticalSwitch,
    Resuming,
    Finished,
}

#[derive(Debug, Clone, Default)]
pub struct RemountCancellationToken {
    cancelled: Arc<AtomicBool>,
}

impl RemountCancellationToken {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn request_cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    #[must_use]
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
}

pub struct CommandRemountQuiesce {
    pub(crate) inspection: CommandRemountInspection,
    pub(crate) held_process_group_ids: Vec<i32>,
    pub(crate) affected: Vec<NamespaceExecutionId>,
    pub(crate) engine: Arc<NamespaceExecutionEngine<CommandExecution>>,
    pub(crate) cancellation: RemountCancellationToken,
    pub(crate) switch_state: RemountSwitchState,
    pub(crate) controller: Arc<dyn ProcessGroupController>,
}

impl std::fmt::Debug for CommandRemountQuiesce {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandRemountQuiesce")
            .field("inspection", &self.inspection)
            .field("held_process_group_ids", &self.held_process_group_ids)
            .field("affected", &self.affected)
            .field("cancellation", &self.cancellation)
            .field("switch_state", &self.switch_state)
            .finish_non_exhaustive()
    }
}

impl CommandRemountQuiesce {
    #[must_use]
    pub const fn inspection(&self) -> &CommandRemountInspection {
        &self.inspection
    }

    #[must_use]
    pub fn cancellation(&self) -> RemountCancellationToken {
        self.cancellation.clone()
    }

    #[must_use]
    pub const fn switch_state(&self) -> RemountSwitchState {
        self.switch_state
    }

    pub fn set_switch_state(&mut self, state: RemountSwitchState) {
        self.switch_state = state;
    }

    #[must_use]
    pub fn cancellation_requested(&self) -> bool {
        self.cancellation.is_cancelled()
    }

    pub fn finish(mut self) -> CommandRemountInspection {
        self.resume();
        self.inspection.clone()
    }

    pub fn resume(&mut self) -> bool {
        if self.switch_state == RemountSwitchState::Finished {
            return self.inspection.process_group.resumed;
        }
        self.set_switch_state(RemountSwitchState::Resuming);
        let had_held_process_groups = !self.held_process_group_ids.is_empty();
        let mut all_resumed = true;
        for pgid in self.held_process_group_ids.drain(..) {
            all_resumed &= self.controller.resume_process_group_id(pgid);
        }
        self.resume_affected_commands();
        self.switch_state = RemountSwitchState::Finished;
        self.inspection.process_group.resumed |= had_held_process_groups && all_resumed;
        all_resumed
    }

    fn resume_affected_commands(&self) {
        if !self.cancellation.is_cancelled() {
            return;
        }
        for id in &self.affected {
            self.engine.with_value(id, CommandExecution::cancel);
        }
    }
}

impl Drop for CommandRemountQuiesce {
    fn drop(&mut self) {
        let _ = self.resume();
    }
}

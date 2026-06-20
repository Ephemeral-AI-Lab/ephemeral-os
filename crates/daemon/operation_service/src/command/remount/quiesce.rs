use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use crate::command::{CommandId, CommandLifecycleState, CommandProcessStore};
use ::command::process_group::{ProcessGroupController, ProcessGroupInspection};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountInspection {
    pub active_commands: usize,
    pub command_ids: Vec<CommandId>,
    pub process_group_ids: Vec<i32>,
    pub process_count: usize,
    pub quiesced_process_count: usize,
    pub pinned_cwd_count: usize,
    pub pinned_root_count: usize,
    pub pinned_fd_count: usize,
    pub pinned_mapped_file_count: usize,
    pub mountinfo_checked_count: usize,
    pub blocked_reason: Option<String>,
    pub inspected: bool,
    pub quiesce_attempted: bool,
    pub resumed: bool,
    pub detail: Option<String>,
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
            && self.blocked_reason.is_none()
            && self.inspected
            && self.quiesce_attempted
            && self.quiesced_process_count == self.process_count
    }

    pub(crate) fn block_if_clear(&mut self, reason: RemountBlockReason) {
        self.blocked_reason
            .get_or_insert_with(|| reason.to_string());
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

    #[must_use]
    pub fn same_token(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.cancelled, &other.cancelled)
    }
}

pub struct CommandRemountQuiesce {
    pub(crate) inspection: CommandRemountInspection,
    pub(crate) held_process_group_ids: Vec<i32>,
    pub(crate) command_ids: Vec<CommandId>,
    pub(crate) process_store: Arc<CommandProcessStore>,
    pub(crate) cancellation: RemountCancellationToken,
    pub(crate) switch_state: RemountSwitchState,
    pub(crate) controller: Arc<dyn ProcessGroupController>,
}

impl std::fmt::Debug for CommandRemountQuiesce {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandRemountQuiesce")
            .field("inspection", &self.inspection)
            .field("held_process_group_ids", &self.held_process_group_ids)
            .field("command_ids", &self.command_ids)
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
        for command_id in &self.command_ids {
            let cancellation = self.cancellation.clone();
            self.process_store.update_active(command_id, |active| {
                if active
                    .remount_cancellation
                    .as_ref()
                    .is_some_and(|token| token.same_token(&cancellation))
                {
                    active.remount_switch_state = Some(state);
                }
            });
        }
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
            return self.inspection.resumed;
        }
        self.set_switch_state(RemountSwitchState::Resuming);
        let had_held_process_groups = !self.held_process_group_ids.is_empty();
        let mut all_resumed = true;
        for pgid in self.held_process_group_ids.drain(..) {
            all_resumed &= self.controller.resume_process_group_id(pgid);
        }
        self.resume_command_records();
        self.switch_state = RemountSwitchState::Finished;
        self.inspection.resumed |= had_held_process_groups && all_resumed;
        all_resumed
    }

    fn resume_command_records(&self) {
        for command_id in &self.command_ids {
            let cancellation = self.cancellation.clone();
            self.process_store.update_active(command_id, |active| {
                if !active
                    .remount_cancellation
                    .as_ref()
                    .is_some_and(|token| token.same_token(&cancellation))
                {
                    return;
                }
                active.remount_cancellation = None;
                active.remount_switch_state = None;
                if cancellation.is_cancelled() {
                    active.process.cancel_process();
                    active.lifecycle_state = CommandLifecycleState::Cancelled;
                } else {
                    active.lifecycle_state = CommandLifecycleState::Running;
                }
            });
        }
    }
}

impl Drop for CommandRemountQuiesce {
    fn drop(&mut self) {
        let _ = self.resume();
    }
}

pub(crate) fn merge_report(target: &mut CommandRemountInspection, source: ProcessGroupInspection) {
    target.process_count += source.process_count;
    target.quiesced_process_count += source.quiesced_process_count;
    target.pinned_cwd_count += source.pinned_cwd_count;
    target.pinned_root_count += source.pinned_root_count;
    target.pinned_fd_count += source.pinned_fd_count;
    target.pinned_mapped_file_count += source.pinned_mapped_file_count;
    target.mountinfo_checked_count += source.mountinfo_checked_count;
    target.inspected |= source.inspected;
    target.quiesce_attempted |= source.quiesce_attempted;
    target.resumed |= source.resumed;
    if target.blocked_reason.is_none() {
        target.blocked_reason = source.blocked_reason;
    }
    if target.detail.is_none() {
        target.detail = source.detail;
    }
}

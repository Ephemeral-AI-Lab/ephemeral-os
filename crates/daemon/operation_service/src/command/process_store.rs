use std::collections::HashMap;
use std::fmt;
use std::ops::Deref;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::time::Instant;

use crate::command::{CommandId, CommandServiceError, CommandStatus};
use crate::workspace_crate::{CallerId, WorkspaceId};

pub const DEFAULT_MAX_ACTIVE_COMMANDS: usize = 256;

pub struct CommandProcessStore {
    active: Mutex<HashMap<CommandId, ActiveCommandProcess>>,
    completed: CommandCompletionStore,
    next_id: AtomicU64,
    active_count: AtomicUsize,
    max_active: usize,
}

impl CommandProcessStore {
    #[must_use]
    pub fn new() -> Self {
        Self::with_max_active(DEFAULT_MAX_ACTIVE_COMMANDS)
    }

    #[must_use]
    pub fn with_max_active(max_active: usize) -> Self {
        Self {
            active: Mutex::new(HashMap::new()),
            completed: CommandCompletionStore::new(),
            next_id: AtomicU64::new(1),
            active_count: AtomicUsize::new(0),
            max_active,
        }
    }

    #[must_use]
    pub fn allocate_command_id(&self) -> CommandId {
        let next_id = self.next_id.fetch_add(1, Ordering::Relaxed);
        CommandId(format!("cmd_{next_id}"))
    }

    pub fn try_reserve(&self) -> Result<CommandReservation<'_>, CommandServiceError> {
        loop {
            let active = self.active_count.load(Ordering::Acquire);
            if active >= self.max_active {
                return Err(CommandServiceError::CommandAdmissionLimit {
                    active,
                    max: self.max_active,
                });
            }

            if self
                .active_count
                .compare_exchange(active, active + 1, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                return Ok(CommandReservation {
                    store: self,
                    activated: false,
                });
            }
        }
    }

    pub fn insert_active(
        &self,
        reservation: CommandReservation<'_>,
        record: ActiveCommandProcess,
    ) -> Result<(), CommandServiceError> {
        reservation.ensure_store(self)?;
        let command_id = record.command_id.clone();
        let mut active = lock(&self.active);
        if active.contains_key(&command_id) {
            return Err(CommandServiceError::DuplicateCommandId { command_id });
        }

        active.insert(command_id, record);
        reservation.activate();
        Ok(())
    }

    #[must_use]
    pub fn active(&self, command_id: &CommandId) -> Option<ActiveCommandRef<'_>> {
        let active = lock(&self.active);
        if !active.contains_key(command_id) {
            return None;
        }

        Some(ActiveCommandRef {
            command_id: command_id.clone(),
            active,
        })
    }

    pub fn complete_active(
        &self,
        record: CompletedCommandRecord,
    ) -> Result<Option<ActiveCommandProcess>, CommandServiceError> {
        let command_id = record.command_id.clone();
        let mut active = lock(&self.active);
        if !active.contains_key(&command_id) {
            return Ok(None);
        }

        let mut completed = lock(&self.completed.completed);
        if completed.contains_key(&command_id) {
            return Err(CommandServiceError::DuplicateCommandId { command_id });
        }

        let removed = active
            .remove(&record.command_id)
            .expect("active command exists after contains_key");
        completed.insert(record.command_id.clone(), record);
        decrement_slot(&self.active_count);
        Ok(Some(removed))
    }

    #[must_use]
    pub fn completed(&self, command_id: &CommandId) -> Option<CompletedCommandRecord> {
        self.completed.get(command_id)
    }

    pub(crate) fn update_active<R>(
        &self,
        command_id: &CommandId,
        update: impl FnOnce(&mut ActiveCommandProcess) -> R,
    ) -> Option<R> {
        lock(&self.active).get_mut(command_id).map(update)
    }
}

impl Default for CommandProcessStore {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Debug for CommandProcessStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CommandProcessStore")
            .field("active_len", &lock(&self.active).len())
            .field("completed", &self.completed)
            .field("next_id", &self.next_id.load(Ordering::Relaxed))
            .field("active_count", &self.active_count.load(Ordering::Relaxed))
            .field("max_active", &self.max_active)
            .finish()
    }
}

#[derive(Debug)]
pub struct CommandReservation<'a> {
    store: &'a CommandProcessStore,
    activated: bool,
}

impl CommandReservation<'_> {
    fn ensure_store(&self, store: &CommandProcessStore) -> Result<(), CommandServiceError> {
        if std::ptr::eq(self.store, store) {
            Ok(())
        } else {
            Err(CommandServiceError::ReservationStoreMismatch)
        }
    }

    fn activate(mut self) {
        self.activated = true;
    }
}

impl Drop for CommandReservation<'_> {
    fn drop(&mut self) {
        if !self.activated {
            decrement_slot(&self.store.active_count);
        }
    }
}

pub struct ActiveCommandRef<'a> {
    command_id: CommandId,
    active: MutexGuard<'a, HashMap<CommandId, ActiveCommandProcess>>,
}

impl Deref for ActiveCommandRef<'_> {
    type Target = ActiveCommandProcess;

    fn deref(&self) -> &Self::Target {
        self.active
            .get(&self.command_id)
            .expect("active command disappeared while lock is held")
    }
}

pub struct ActiveCommandProcess {
    pub command_id: CommandId,
    pub caller_id: CallerId,
    pub workspace_id: WorkspaceId,
    pub process: ::command::CommandProcess,
    pub transcript: CommandTranscriptStore,
    pub finalize_policy: CommandFinalizePolicy,
    pub lifecycle_state: CommandLifecycleState,
    pub cancellation: CancellationState,
    pub finalization: FinalizationState,
    pub trace_origin: CommandTraceOrigin,
    pub started_at: Instant,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommandFinalizePolicy {
    Session { workspace_id: WorkspaceId },
    OneShotPublishThenDestroy { workspace_id: WorkspaceId },
}

impl CommandFinalizePolicy {
    #[must_use]
    pub const fn workspace_id(&self) -> &WorkspaceId {
        match self {
            Self::Session { workspace_id } | Self::OneShotPublishThenDestroy { workspace_id } => {
                workspace_id
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommandLifecycleState {
    Starting,
    Running,
    QuiescedForRemount,
    Finalizing,
    Completed,
    Cancelled,
    TimedOut,
    FinalizationFailed,
    DestroyPending,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CancellationState {
    None,
    Requested { requested_at: Instant },
    Sent { sent_at: Instant },
    Finalized,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FinalizationState {
    NotStarted,
    InProgress,
    ResponseBuffered,
    WorkspaceDestroyPending,
    Complete,
    Failed { error: String },
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandTraceOrigin;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandTranscriptStore {
    pub transcript_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RetainedCommandTranscript {
    pub transcript_path: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandTerminalResult {
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub stdout: String,
}

#[derive(Debug, Default)]
pub struct CommandCompletionStore {
    completed: Mutex<HashMap<CommandId, CompletedCommandRecord>>,
}

impl CommandCompletionStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&self, record: CompletedCommandRecord) -> Result<(), CommandServiceError> {
        let command_id = record.command_id.clone();
        let mut completed = lock(&self.completed);
        if completed.contains_key(&command_id) {
            return Err(CommandServiceError::DuplicateCommandId { command_id });
        }

        completed.insert(command_id, record);
        Ok(())
    }

    #[must_use]
    pub fn get(&self, command_id: &CommandId) -> Option<CompletedCommandRecord> {
        lock(&self.completed).get(command_id).cloned()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompletedCommandRecord {
    pub command_id: CommandId,
    pub caller_id: CallerId,
    pub workspace_id: WorkspaceId,
    pub result: CommandTerminalResult,
    pub transcript: RetainedCommandTranscript,
    pub finalization: FinalizationState,
    pub completed_at: Instant,
}

fn decrement_slot(active_count: &AtomicUsize) {
    let _ = active_count.fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
        Some(count.saturating_sub(1))
    });
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn command_id(id: &str) -> CommandId {
        CommandId(id.to_owned())
    }

    fn caller_id(id: &str) -> CallerId {
        CallerId(id.to_owned())
    }

    fn workspace_id(id: &str) -> WorkspaceId {
        WorkspaceId(id.to_owned())
    }

    fn inactive_process(command_id: &CommandId, caller_id: &CallerId) -> ::command::CommandProcess {
        ::command::CommandProcess::new(::command::CommandProcessSpec {
            id: command_id.0.clone(),
            caller_id: caller_id.0.clone(),
            command: "echo ok".to_owned(),
            timeout_seconds: None,
        })
    }

    fn active_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
    ) -> ActiveCommandProcess {
        ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id: caller_id.clone(),
            workspace_id: workspace_id.clone(),
            process: inactive_process(&command_id, &caller_id),
            transcript: CommandTranscriptStore::default(),
            finalize_policy: CommandFinalizePolicy::Session { workspace_id },
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            finalization: FinalizationState::NotStarted,
            trace_origin: CommandTraceOrigin,
            started_at: Instant::now(),
        }
    }

    fn completed_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
    ) -> CompletedCommandRecord {
        CompletedCommandRecord {
            command_id,
            caller_id,
            workspace_id,
            result: CommandTerminalResult {
                status: CommandStatus::Completed,
                exit_code: Some(0),
                stdout: "ok\n".to_owned(),
            },
            transcript: RetainedCommandTranscript::default(),
            finalization: FinalizationState::Complete,
            completed_at: Instant::now(),
        }
    }

    #[test]
    fn command_finalize_policy_returns_workspace_id() {
        let workspace_id = WorkspaceId("workspace-1".to_owned());
        let policy = CommandFinalizePolicy::Session {
            workspace_id: workspace_id.clone(),
        };

        assert_eq!(policy.workspace_id(), &workspace_id);
    }

    #[test]
    fn command_process_store_duplicate_completion_preserves_active_record_and_slot() {
        let store = CommandProcessStore::with_max_active(1);
        let command_id = command_id("cmd_completed");
        let caller_id = caller_id("caller-owner");
        let workspace_id = workspace_id("workspace-1");
        let reservation = store.try_reserve().expect("reservation succeeds");

        store
            .insert_active(
                reservation,
                active_record(command_id.clone(), caller_id.clone(), workspace_id.clone()),
            )
            .expect("active insert succeeds");
        store
            .completed
            .insert(completed_record(
                command_id.clone(),
                caller_id.clone(),
                workspace_id.clone(),
            ))
            .expect("preexisting completed record inserted");

        let error = match store.complete_active(completed_record(
            command_id.clone(),
            caller_id,
            workspace_id,
        )) {
            Err(error) => error,
            Ok(_) => panic!("duplicate completed id is rejected before active removal"),
        };

        assert!(matches!(
            error,
            CommandServiceError::DuplicateCommandId { command_id: duplicate }
                if duplicate == command_id
        ));
        assert!(store.active(&command_id).is_some());
        let error = store
            .try_reserve()
            .expect_err("failed completion keeps active slot consumed");
        assert!(matches!(
            error,
            CommandServiceError::CommandAdmissionLimit { active: 1, max: 1 }
        ));
    }
}

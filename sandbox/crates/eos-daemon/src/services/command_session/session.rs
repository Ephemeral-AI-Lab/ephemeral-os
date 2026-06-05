#[cfg(target_os = "linux")]
use std::collections::{HashMap, HashSet};
#[cfg(target_os = "linux")]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(target_os = "linux")]
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use serde_json::{json, Value};

#[cfg(target_os = "linux")]
use super::{
    command_result, command_session_config, finalize_command_session_policy, response_with_stdout,
    runtime_command_session_config,
};
#[cfg(target_os = "linux")]
use eos_command_session::process::{
    CommandCompletionStatus, CommandRunnerResult, CommandSessionProcess, ProcessReap,
};
#[cfg(target_os = "linux")]
use eos_command_session::{
    wait_for_yield as runtime_wait_for_yield, CommandSessionOutput, CommandSessionOutputCursor,
    CommandSessionWaitTarget, DynCommandWorkspacePolicy, WaitOutcome as RuntimeWaitOutcome,
};
#[cfg(target_os = "linux")]
use eos_workspace_api::WorkspaceMode;

#[cfg(any(target_os = "linux", test))]
pub(super) const fn should_publish_command_session_completion(
    publish_completion: bool,
    cancelled: bool,
    owned_live_session: bool,
) -> bool {
    publish_completion && !cancelled && owned_live_session
}

#[cfg(target_os = "linux")]
pub(super) fn lock_command_session_state<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(target_os = "linux")]
pub(super) type WaitOutcome = RuntimeWaitOutcome<Value>;

#[cfg(target_os = "linux")]
pub(super) struct CommandSession {
    pub(super) id: String,
    pub(super) caller_id: String,
    pub(super) command: String,
    pub(super) started_at: Instant,
    pub(super) process: CommandSessionProcess,
    pub(super) output: Arc<CommandSessionOutput>,
    pub(super) cancelled: Mutex<bool>,
    pub(super) interrupted: Mutex<bool>,
    pub(super) model_cursor: Mutex<CommandSessionOutputCursor>,
    pub(super) notification_cursor: Mutex<CommandSessionOutputCursor>,
    pub(super) workspace_mode: WorkspaceMode,
    pub(super) output_path: PathBuf,
    pub(super) final_path: PathBuf,
    pub(super) workspace_policy: Mutex<Option<DynCommandWorkspacePolicy>>,
    pub(super) finalize_context: Value,
    pub(super) finalized: Mutex<Option<Value>>,
    pub(super) timeout_deadline: Option<Instant>,
}

#[cfg(target_os = "linux")]
impl CommandSession {
    pub(super) fn read_model_output(&self, max_tokens: Option<u64>) -> String {
        let mut cursor = lock_command_session_state(&self.model_cursor);
        self.output.read_since(&mut cursor, max_tokens)
    }

    pub(super) fn read_notification_output(&self, max_tokens: Option<u64>) -> String {
        let mut cursor = lock_command_session_state(&self.notification_cursor);
        self.output.read_since(&mut cursor, max_tokens)
    }

    /// Sense-2 idempotent finalize: returns the terminal result once the child
    /// has exited (caching it under the `finalized` latch so exec / write_stdin /
    /// reaper are at-most-once), or `None` while still running. `publish` parks
    /// the completion for the heartbeat (set by the reaper for unpolled exits;
    /// `false` for the inline tool-return path, so a polled session is never
    /// double-delivered).
    ///
    /// This subsumes the two former per-session detached finalizer threads;
    /// prologue/epilogue are shared and workspace finalization runs through the
    /// stored policy object.
    pub(super) fn try_finalize(&self, publish: bool) -> Option<Value> {
        let mut latch = lock_command_session_state(&self.finalized);
        if let Some(cached) = latch.as_ref() {
            return Some(cached.clone());
        }
        // Reap the child without blocking; bail while it is still running.
        let process_exit = match self.process.try_reap() {
            ProcessReap::Running => return None,
            ProcessReap::Exited(exit) => exit,
        };
        self.process.terminate();
        let runner = CommandRunnerResult::read_from_path(&self.output_path);
        let cancelled = *lock_command_session_state(&self.cancelled);
        let interrupted = *lock_command_session_state(&self.interrupted);
        let completion = CommandCompletionStatus::from_process_and_runner(
            process_exit,
            runner.as_ref(),
            cancelled,
            interrupted,
        );
        let stdout = completed_session_stdout(self);
        let response = finalize_command_session_policy(
            self,
            runner.as_ref(),
            completion.status(),
            completion.exit_code(),
            &stdout,
            publish,
        )
        .unwrap_or_else(|err| {
            command_result(
                "error",
                Some(completion.exit_code()),
                &stdout,
                &err.to_string(),
                Some(self.id.clone()),
            )
        });
        let owned_live_session = command_session_registry().remove(&self.id).is_some();
        if matches!(self.workspace_mode, WorkspaceMode::Isolated) {
            crate::services::isolated_workspace::unregister_command_session(
                &self.caller_id,
                &self.id,
            );
        }
        if should_publish_command_session_completion(publish, cancelled, owned_live_session) {
            command_session_registry().push_completed(json!({
                "command_session_id": self.id,
                "caller_id": self.caller_id,
                "command": self.command,
                "result": response_with_stdout(response.clone(), self.read_model_output(None)),
                "notification_result": response_with_stdout(
                    response.clone(),
                    self.read_notification_output(None),
                ),
            }));
        }
        *latch = Some(response.clone());
        Some(response)
    }
}

#[cfg(target_os = "linux")]
impl CommandSessionWaitTarget<Value> for CommandSession {
    fn try_finalize(&self, publish_completion: bool) -> Option<Value> {
        Self::try_finalize(self, publish_completion)
    }

    fn next_output_byte_offset(&self) -> u64 {
        self.output.next_byte_offset()
    }

    fn read_model_output(&self, max_tokens: Option<u64>) -> String {
        Self::read_model_output(self, max_tokens)
    }
}

/// Sense-2 unified wait shared by `exec_command` and `write_stdin`: early-return
/// on completion (inline finalize) or on quiet-after-output, capped at the
/// caller's `yield_time_ms`.
#[cfg(target_os = "linux")]
pub(super) fn wait_for_yield(
    session: &Arc<CommandSession>,
    yield_time_ms: u64,
    max_tokens: Option<u64>,
) -> WaitOutcome {
    runtime_wait_for_yield(
        session.as_ref(),
        &runtime_command_session_config(),
        yield_time_ms,
        max_tokens,
    )
}

#[cfg(target_os = "linux")]
pub(super) struct CommandSessionRegistry {
    sessions: Mutex<HashMap<String, Arc<CommandSession>>>,
    /// Parked terminal completions awaiting heartbeat collection or a late
    /// `write_stdin` poll. Entries are removed on first delivery (by
    /// `collect_completed`) or claim (by `take_completed_result`), so the map
    /// stays bounded.
    completed: Mutex<HashMap<String, Value>>,
    counter: AtomicU64,
}

#[cfg(target_os = "linux")]
impl CommandSessionRegistry {
    pub(super) fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            completed: Mutex::new(HashMap::new()),
            counter: AtomicU64::new(1),
        }
    }

    pub(super) fn next_id(&self) -> String {
        format!("cmd_{}", self.counter.fetch_add(1, Ordering::Relaxed))
    }

    pub(super) fn insert(&self, session: Arc<CommandSession>) {
        lock_command_session_state(&self.sessions).insert(session.id.clone(), session);
    }

    pub(super) fn get(&self, id: &str) -> Option<Arc<CommandSession>> {
        lock_command_session_state(&self.sessions).get(id).cloned()
    }

    pub(super) fn remove(&self, id: &str) -> Option<Arc<CommandSession>> {
        lock_command_session_state(&self.sessions).remove(id)
    }

    pub(super) fn count_by_caller(&self, caller_id: &str) -> usize {
        lock_command_session_state(&self.sessions)
            .values()
            .filter(|session| caller_id.is_empty() || session.caller_id == caller_id)
            .count()
    }

    /// A snapshot of the live sessions (for the reaper sweep).
    pub(super) fn live(&self) -> Vec<Arc<CommandSession>> {
        lock_command_session_state(&self.sessions)
            .values()
            .cloned()
            .collect()
    }

    pub(super) fn push_completed(&self, completion: Value) {
        let id = completion
            .get("command_session_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if id.is_empty() {
            return;
        }
        lock_command_session_state(&self.completed).insert(id, completion);
    }

    pub(super) fn take_completed_result(&self, id: &str) -> Option<Value> {
        lock_command_session_state(&self.completed)
            .remove(id)
            .and_then(|completion| completion.get("result").cloned())
    }

    /// Collect (and **remove**, so the map stays bounded) the parked completions
    /// matching the requested ids/agent. Removal on delivery is the exactly-once
    /// gate: a later `write_stdin` poll finds the entry gone and recovers the
    /// terse already-reported result from the host supervisor (§8/D8).
    pub(super) fn collect_completed(&self, args: &Value) -> Value {
        let wanted: Option<HashSet<String>> = args
            .get("command_session_ids")
            .and_then(Value::as_array)
            .map(|ids| {
                ids.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            });
        let caller_id = args
            .get("caller_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let mut completed = lock_command_session_state(&self.completed);
        let matched: Vec<String> = completed
            .iter()
            .filter(|(id, completion)| {
                let item_caller = completion
                    .get("caller_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let id_matches = wanted.as_ref().is_none_or(|ids| ids.contains(*id));
                let caller_matches = caller_id.is_empty() || caller_id == item_caller;
                id_matches && caller_matches
            })
            .map(|(id, _)| id.clone())
            .collect();
        let returned: Vec<Value> = matched
            .iter()
            .filter_map(|id| completed.remove(id))
            .map(|mut completion| {
                if let Some(notification_result) = completion.get("notification_result").cloned() {
                    completion["result"] = notification_result;
                }
                completion
            })
            .collect();
        drop(completed);
        json!({"success": true, "completions": returned})
    }
}

#[cfg(target_os = "linux")]
pub(super) fn command_session_registry() -> &'static CommandSessionRegistry {
    static REGISTRY: OnceLock<CommandSessionRegistry> = OnceLock::new();
    REGISTRY.get_or_init(CommandSessionRegistry::new)
}

#[cfg(target_os = "linux")]
fn completed_session_stdout(session: &CommandSession) -> String {
    session.process.wait_for_reader_done(Duration::from_millis(
        command_session_config().output_drain_grace_ms,
    ));
    session.output.all_recent(None)
}

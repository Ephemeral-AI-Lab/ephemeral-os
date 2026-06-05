#[cfg(target_os = "linux")]
use std::collections::{HashMap, HashSet};
#[cfg(target_os = "linux")]
use std::fs::File;
#[cfg(target_os = "linux")]
use std::os::unix::process::ExitStatusExt;
#[cfg(target_os = "linux")]
use std::process::Child;
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(target_os = "linux")]
use std::sync::{mpsc as std_mpsc, Arc, Mutex, MutexGuard, OnceLock};
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use eos_layerstack::LayerStack;
#[cfg(target_os = "linux")]
use eos_runner::RunResult;
#[cfg(target_os = "linux")]
use serde_json::{json, Value};

#[cfg(target_os = "linux")]
use super::output::{CommandSessionOutput, CommandSessionOutputCursor};
#[cfg(target_os = "linux")]
use super::{
    command_result, command_session_config, finalize_command_workspace,
    finalize_isolated_command_workspace, response_with_stdout, terminate_command_process_group,
    CommandWorkspaceKind,
};

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
pub(super) struct CommandSession {
    pub(super) id: String,
    pub(super) agent_id: String,
    pub(super) command: String,
    pub(super) started_at: Instant,
    pub(super) pgid: i32,
    pub(super) writer: Mutex<File>,
    pub(super) output: Arc<CommandSessionOutput>,
    pub(super) reader_done: Mutex<Option<std_mpsc::Receiver<()>>>,
    pub(super) cancelled: Mutex<bool>,
    pub(super) interrupted: Mutex<bool>,
    pub(super) model_cursor: Mutex<CommandSessionOutputCursor>,
    pub(super) notification_cursor: Mutex<CommandSessionOutputCursor>,
    // sense-2: the child lives in the session (was moved into a per-session
    // detached finalizer thread). One idempotent `try_finalize` reaps it.
    pub(super) child: Mutex<Option<Child>>,
    pub(super) workspace: CommandWorkspaceKind,
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
    /// prologue/epilogue are shared and only the workspace-finalize body and the
    /// teardown branch on [`CommandWorkspaceKind`].
    pub(super) fn try_finalize(&self, publish: bool) -> Option<Value> {
        let mut latch = lock_command_session_state(&self.finalized);
        if let Some(cached) = latch.as_ref() {
            return Some(cached.clone());
        }
        // Reap the child without blocking; bail while it is still running.
        let exit_status = {
            let mut child = lock_command_session_state(&self.child);
            match child.as_mut() {
                Some(handle) => match handle.try_wait() {
                    Ok(Some(status)) => {
                        let _ = child.take();
                        Some(status)
                    }
                    Ok(None) => return None,
                    // A wait error means the child is unwaitable; finalize anyway.
                    Err(_) => {
                        let _ = child.take();
                        None
                    }
                },
                // No child handle (already reaped) — finalize with the runner file.
                None => None,
            }
        };
        terminate_command_process_group(self.pgid);
        let runner = std::fs::read(self.workspace.output_path())
            .ok()
            .and_then(|bytes| serde_json::from_slice::<RunResult>(&bytes).ok());
        let mut exit_code = runner
            .as_ref()
            .map(|result| i64::from(result.exit_code))
            .or_else(|| {
                exit_status.map(|status| {
                    status
                        .code()
                        .map(i64::from)
                        .or_else(|| status.signal().map(|signal| -i64::from(signal)))
                        .unwrap_or(1)
                })
            })
            .unwrap_or(1);
        let mut command_status = runner
            .as_ref()
            .and_then(|result| result.tool_result.get("status"))
            .and_then(Value::as_str)
            .unwrap_or("error")
            .to_owned();
        let cancelled = *lock_command_session_state(&self.cancelled);
        if cancelled
            || (*lock_command_session_state(&self.interrupted) && matches!(exit_code, 130 | -2))
        {
            "cancelled".clone_into(&mut command_status);
            exit_code = 130;
        }
        let stdout = completed_session_stdout(self);
        let response = match &self.workspace {
            CommandWorkspaceKind::Ephemeral(workspace) => finalize_command_workspace(
                self,
                workspace,
                &command_status,
                exit_code,
                &stdout,
                publish,
            ),
            CommandWorkspaceKind::Isolated(workspace) => finalize_isolated_command_workspace(
                self,
                workspace,
                runner.as_ref(),
                &command_status,
                exit_code,
                &stdout,
                publish,
            ),
        }
        .unwrap_or_else(|err| {
            command_result(
                "error",
                Some(exit_code),
                &stdout,
                &err.to_string(),
                Some(self.id.clone()),
            )
        });
        // Teardown MUST run even on a finalize Err, or the shared ephemeral lease
        // leaks. Isolated teardown is deferred to `exit_isolated_workspace`.
        match &self.workspace {
            CommandWorkspaceKind::Ephemeral(workspace) => {
                let _ = std::fs::remove_dir_all(&workspace.dirs.run_dir);
                let _ = LayerStack::open(workspace.root.clone())
                    .and_then(|mut stack| stack.release_lease(&workspace.lease_id));
            }
            CommandWorkspaceKind::Isolated(_) => {}
        }
        let owned_live_session = command_session_registry().remove(&self.id).is_some();
        if let CommandWorkspaceKind::Isolated(_) = &self.workspace {
            crate::isolated::unregister_command_session(&self.agent_id, &self.id);
        }
        if should_publish_command_session_completion(publish, cancelled, owned_live_session) {
            command_session_registry().push_completed(json!({
                "command_session_id": self.id,
                "agent_id": self.agent_id,
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

/// Whether `wait_for_yield` finalized the session inline or it is still running.
#[cfg(target_os = "linux")]
pub(super) enum WaitOutcome {
    /// The child exited; the terminal result is ready to return.
    Completed(Value),
    /// Still running; the model-facing output captured so far.
    Running(String),
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
    let deadline = Instant::now() + Duration::from_millis(yield_time_ms);
    let start_off = session.output.next_byte_offset();
    let (mut last_off, mut last_change) = (start_off, Instant::now());
    loop {
        if let Some(result) = session.try_finalize(false) {
            return WaitOutcome::Completed(result);
        }
        let off = session.output.next_byte_offset();
        if off != last_off {
            last_off = off;
            last_change = Instant::now();
        }
        if off > start_off
            && last_change.elapsed() >= Duration::from_millis(command_session_config().quiet_ms)
        {
            return WaitOutcome::Running(session.read_model_output(max_tokens));
        }
        if Instant::now() >= deadline {
            return WaitOutcome::Running(session.read_model_output(max_tokens));
        }
        thread::sleep(Duration::from_millis(5));
    }
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

    pub(super) fn count_by_agent(&self, agent_id: &str) -> usize {
        lock_command_session_state(&self.sessions)
            .values()
            .filter(|session| agent_id.is_empty() || session.agent_id == agent_id)
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
    /// terse already-reported result from the agent-core supervisor (§8/D8).
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
        let agent_id = args
            .get("agent_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let mut completed = lock_command_session_state(&self.completed);
        let matched: Vec<String> = completed
            .iter()
            .filter(|(id, completion)| {
                let item_agent = completion
                    .get("agent_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let id_matches = wanted.as_ref().is_none_or(|ids| ids.contains(*id));
                let agent_matches = agent_id.is_empty() || agent_id == item_agent;
                id_matches && agent_matches
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
    let reader_done = lock_command_session_state(&session.reader_done).take();
    if let Some(reader_done) = reader_done {
        let _ = reader_done.recv_timeout(Duration::from_millis(
            command_session_config().output_drain_grace_ms,
        ));
    }
    session.output.all_recent(None)
}

//! [`CommandSessionLane`] (spec §9.3) — the per-agent-run command-session
//! subsystem. Command-session **PTYs are daemon-owned** (the daemon's
//! `WorkspaceRunRegistry`, keyed by `caller_id == agent_run_id`); this lane is the
//! agent-core mirror that (a) tracks records for completion delivery, (b) **owns
//! the [`CommandCompletionHeartbeat`]** that polls the daemon and sends
//! completions to the run's notifier through [`BackgroundNotificationEmitter`],
//! and (c) cancels via the single per-caller daemon RPC
//! `cancel_workspace_runs_by_caller_id` — never per-session.
//!
//! Reference-cycle rule (spec §8.3): the heartbeat task captures a **`Weak`** to
//! the lane records, never a strong `Arc` to anything that transitively owns its
//! `JoinHandle`. The lane owns both the records `Arc` and the heartbeat
//! `JoinHandle`, so dropping the lane drops the `JoinHandle`, whose `Drop` aborts
//! the task; the task's per-tick `upgrade()` then fails and it exits.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Weak};
use std::time::Duration;

use eos_sandbox_port::{
    cancel_workspace_runs_by_caller_id, collect_command_completions, SandboxTransport,
};
use eos_types::{AgentRunId, CommandSessionId, SandboxId};
use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::super::notifications::{BackgroundCompletion, BackgroundNotificationEmitter};
use super::BackgroundTaskStatus;

/// The first-class handle for one tracked command session (spec §9.3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSessionHandle {
    /// Daemon-minted `cmd_<n>` correlation key.
    pub command_session_id: CommandSessionId,
    /// Owning sandbox.
    pub sandbox_id: SandboxId,
}

/// One tracked background command session.
#[derive(Debug, Clone)]
pub struct CommandSessionRecord {
    /// The command-session handle (ids).
    pub handle: CommandSessionHandle,
    /// The launched command, for the notification body.
    pub command: String,
    /// Lifecycle status.
    pub status: BackgroundTaskStatus,
    /// Terminal completion payload (`None` until terminal).
    pub result: Option<Value>,
}

/// The shared records map. Held by the lane (`Arc`) and the heartbeat (`Weak`).
type CommandSessionRecords = HashMap<CommandSessionId, CommandSessionRecord>;

/// Map a daemon completion `result.status` to a terminal supervisor status:
/// `ok` → `Completed`, `cancelled` → `Cancelled`, anything else → `Failed`.
fn command_completion_status(result: Option<&Value>) -> BackgroundTaskStatus {
    match result
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
    {
        Some("ok") => BackgroundTaskStatus::Completed,
        Some("cancelled") => BackgroundTaskStatus::Cancelled,
        _ => BackgroundTaskStatus::Failed,
    }
}

/// Ingest pulled daemon completions into still-`Running` records and collect the
/// fresh-terminal transitions to emit, latching each to `Delivered` so neither a
/// later heartbeat tick nor a control-tool recover re-delivers it (exactly-once).
fn ingest_and_collect(
    records: &mut CommandSessionRecords,
    completions: &[eos_types::JsonObject],
) -> Vec<BackgroundCompletion> {
    let mut out = Vec::new();
    for completion in completions {
        let Some(id) = completion
            .get("command_session_id")
            .and_then(Value::as_str)
            .and_then(|id| id.parse::<CommandSessionId>().ok())
        else {
            continue;
        };
        let Some(record) = records.get_mut(&id) else {
            continue;
        };
        if !matches!(record.status, BackgroundTaskStatus::Running) {
            continue;
        }
        let result = completion.get("result").cloned().unwrap_or(Value::Null);
        let status = command_completion_status(Some(&result));
        record.result = Some(result.clone());
        out.push(BackgroundCompletion::CommandSession {
            command_session_id: id,
            sandbox_id: record.handle.sandbox_id.clone(),
            status,
            result,
        });
        record.status = BackgroundTaskStatus::Delivered;
    }
    out
}

/// The command-session subsystem for one agent run.
pub struct CommandSessionLane {
    owner_agent_run_id: AgentRunId,
    transport: Arc<dyn SandboxTransport>,
    records: Arc<Mutex<CommandSessionRecords>>,
    /// Held for RAII only — `Drop` aborts the poll task when the lane drops.
    _heartbeat: CommandCompletionHeartbeat,
}

impl std::fmt::Debug for CommandSessionLane {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandSessionLane")
            .field("owner_agent_run_id", &self.owner_agent_run_id)
            .finish_non_exhaustive()
    }
}

impl CommandSessionLane {
    /// Build the lane and spawn its command-completion heartbeat against the agent
    /// run's notifier. Must be called within a Tokio runtime.
    #[must_use]
    pub fn new(
        owner_agent_run_id: AgentRunId,
        notifications: BackgroundNotificationEmitter,
        transport: Arc<dyn SandboxTransport>,
        interval: Duration,
    ) -> Self {
        let records: Arc<Mutex<CommandSessionRecords>> = Arc::new(Mutex::new(HashMap::new()));
        let heartbeat = CommandCompletionHeartbeat::spawn(
            owner_agent_run_id.clone(),
            Arc::downgrade(&records),
            notifications,
            transport.clone(),
            interval,
        );
        Self {
            owner_agent_run_id,
            transport,
            records,
            _heartbeat: heartbeat,
        }
    }

    /// Register a freshly-started background command session as running.
    /// Idempotent: an existing record (running or terminal) is kept.
    pub(crate) async fn register(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        command: &str,
    ) {
        self.records
            .lock()
            .await
            .entry(command_session_id.clone())
            .or_insert_with(|| CommandSessionRecord {
                handle: CommandSessionHandle {
                    command_session_id: command_session_id.clone(),
                    sandbox_id: sandbox_id.clone(),
                },
                command: command.to_owned(),
                status: BackgroundTaskStatus::Running,
                result: None,
            });
    }

    /// The stored terminal result for a session that is no longer running (the
    /// recover race), else `None`.
    pub(crate) async fn command_session_result(
        &self,
        command_session_id: &CommandSessionId,
    ) -> Option<Value> {
        let guard = self.records.lock().await;
        let record = guard.get(command_session_id)?;
        if matches!(record.status, BackgroundTaskStatus::Running) {
            return None;
        }
        record.result.clone()
    }

    /// Latch a session to `Delivered` with the terminal `result` a control tool
    /// observed inline, so the heartbeat does not re-deliver it.
    pub(crate) async fn mark_command_session_reported(
        &self,
        command_session_id: &CommandSessionId,
        result: Value,
    ) {
        if let Some(record) = self.records.lock().await.get_mut(command_session_id) {
            record.status = BackgroundTaskStatus::Delivered;
            record.result = Some(result);
        }
    }

    /// Whether a tracked session's completion was already delivered to the model.
    pub(crate) async fn command_session_already_reported(
        &self,
        command_session_id: &CommandSessionId,
    ) -> bool {
        self.records
            .lock()
            .await
            .get(command_session_id)
            .is_some_and(|record| matches!(record.status, BackgroundTaskStatus::Delivered))
    }

    /// Count still-running command sessions.
    pub(crate) async fn count_running(&self) -> usize {
        self.records
            .lock()
            .await
            .values()
            .filter(|record| matches!(record.status, BackgroundTaskStatus::Running))
            .count()
    }

    /// Cancel ALL of this lane's command sessions in one daemon RPC per sandbox
    /// (`caller_id == owner_agent_run_id`). The daemon tears down the caller's
    /// whole workspace run (PTYs + overlay); there is no per-session cancel from
    /// agent-core. Records are then settled `Cancelled`. Best-effort: a transport
    /// fault is logged, not propagated.
    pub(crate) async fn cancel_all_command_sessions(&self, reason: &str) {
        let sandboxes: Vec<SandboxId> = {
            let guard = self.records.lock().await;
            let mut seen: BTreeMap<SandboxId, ()> = BTreeMap::new();
            for record in guard.values() {
                if matches!(record.status, BackgroundTaskStatus::Running) {
                    seen.insert(record.handle.sandbox_id.clone(), ());
                }
            }
            seen.into_keys().collect()
        };
        for sandbox in sandboxes {
            if let Err(err) = cancel_workspace_runs_by_caller_id(
                &*self.transport,
                &sandbox,
                self.owner_agent_run_id.as_str(),
            )
            .await
            {
                tracing::warn!(
                    error = %err,
                    caller_id = self.owner_agent_run_id.as_str(),
                    sandbox_id = sandbox.as_str(),
                    reason,
                    "per-caller workspace-run cancellation failed"
                );
            }
        }
        let mut guard = self.records.lock().await;
        for record in guard.values_mut() {
            if matches!(record.status, BackgroundTaskStatus::Running) {
                record.status = BackgroundTaskStatus::Cancelled;
                record.result = Some(json!({
                    "status": "cancelled",
                    "exit_code": Value::Null,
                    "output": {"stdout": "", "stderr": ""},
                }));
            }
        }
    }
}

/// The command-completion heartbeat, an RAII runner owned by [`CommandSessionLane`]
/// (spec §8.3). Polls the daemon for this caller's running command-session
/// completions and emits them to the run's notifier. `Drop` aborts the task.
struct CommandCompletionHeartbeat {
    join: JoinHandle<()>,
}

impl Drop for CommandCompletionHeartbeat {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl CommandCompletionHeartbeat {
    /// Spawn the heartbeat. `records` is a `Weak` to the lane's shared records — a
    /// strong capture would form a cycle (task → records-owner → JoinHandle) so the
    /// `JoinHandle` would never drop and never abort the task.
    fn spawn(
        owner_agent_run_id: AgentRunId,
        records: Weak<Mutex<CommandSessionRecords>>,
        notifications: BackgroundNotificationEmitter,
        transport: Arc<dyn SandboxTransport>,
        interval: Duration,
    ) -> Self {
        let join = tokio::spawn(async move {
            loop {
                // Plan: running ids grouped by sandbox. Upgrade per access and drop
                // the strong `Arc` before any `.await` so the lane (and this task's
                // `JoinHandle`) can drop and abort us.
                let groups = {
                    let Some(records) = records.upgrade() else {
                        return;
                    };
                    let guard = records.lock().await;
                    running_by_sandbox(&guard)
                };
                for (sandbox_id, ids) in groups {
                    let Ok(completions) = collect_command_completions(
                        &*transport,
                        &sandbox_id,
                        owner_agent_run_id.as_str(),
                        &ids,
                    )
                    .await
                    else {
                        continue; // transport faults are swallowed; retried next tick
                    };
                    if completions.is_empty() {
                        continue;
                    }
                    let to_emit = {
                        let Some(records) = records.upgrade() else {
                            return;
                        };
                        let mut guard = records.lock().await;
                        ingest_and_collect(&mut guard, &completions)
                    };
                    for completion in to_emit {
                        let _ = notifications.emit(completion).await;
                    }
                }
                sleep(interval).await;
            }
        });
        Self { join }
    }
}

/// Running command-session ids grouped by sandbox — the heartbeat's pull plan.
fn running_by_sandbox(records: &CommandSessionRecords) -> Vec<(SandboxId, Vec<String>)> {
    let mut groups: BTreeMap<SandboxId, Vec<String>> = BTreeMap::new();
    for record in records.values() {
        if matches!(record.status, BackgroundTaskStatus::Running) {
            groups
                .entry(record.handle.sandbox_id.clone())
                .or_default()
                .push(record.handle.command_session_id.as_str().to_owned());
        }
    }
    groups.into_iter().collect()
}

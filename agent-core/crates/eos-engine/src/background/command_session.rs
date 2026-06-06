//! Background PTY command-session supervision (anchor §5): the per-session
//! record, the [`BackgroundTaskSupervisor`] methods that ingest daemon
//! completions and render `[BACKGROUND COMPLETED]` notifications, and the
//! [`CommandSessionSupervisorPort`] implementation the `exec_command` /
//! `write_stdin` tools call through.
//!
//! The supervisor never touches the daemon; the heartbeat
//! ([`super::heartbeat`]) is the sole completion-pull driver. The `result`
//! payloads are the daemon completion's `result` map (status, `exit_code`,
//! `output.stdout`, …) — opaque JSON the supervisor only renders.

use async_trait::async_trait;
use eos_tools::ports::CommandSessionSupervisorPort;
use eos_tools::SystemNotification as ToolNotification;
use eos_types::{AgentRunId, CommandSessionId, SandboxId};
use serde_json::Value;

use super::handle::BackgroundSupervisorHandle;
use super::supervisor::{BackgroundTaskStatus, BackgroundTaskSupervisor};

/// One tracked background command session. `status` reuses
/// [`BackgroundTaskStatus`] (`Running` → `Completed`/`Failed`/`Cancelled` →
/// `Delivered`); `result` holds the terminal completion payload once known.
#[derive(Debug, Clone)]
pub struct CommandSessionRecord {
    /// Daemon-minted `cmd_<n>` — the correlation key.
    pub command_session_id: CommandSessionId,
    /// Owning sandbox id.
    pub sandbox_id: SandboxId,
    /// Owning agent run — per-task-run ownership.
    pub agent_run_id: AgentRunId,
    /// The launched command, for the notification body.
    pub command: String,
    /// Lifecycle status.
    pub status: BackgroundTaskStatus,
    /// Terminal completion payload (`None` until terminal).
    pub result: Option<Value>,
}

/// Map a daemon completion `result.status` to a terminal supervisor status:
/// `ok` → `Completed`, `cancelled` → `Cancelled`, anything else
/// (`error`/`timed_out`) → `Failed`.
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

/// Render the `[BACKGROUND COMPLETED]` notification body for a terminal record.
fn render_command_completion(record: &CommandSessionRecord) -> String {
    let result = record.result.as_ref();
    let status = result
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let exit_code = result
        .and_then(|result| result.get("exit_code"))
        .and_then(Value::as_i64);
    let stdout = result
        .and_then(|result| {
            result
                .get("output")
                .and_then(|output| output.get("stdout"))
                .or_else(|| result.get("stdout"))
        })
        .and_then(Value::as_str)
        .unwrap_or("");
    let exit = exit_code.map_or_else(|| "none".to_owned(), |code| code.to_string());
    format!(
        "[BACKGROUND COMPLETED] command_session_id={} status={status} exit_code={exit}\n\
         command: {}\nstdout: {stdout}",
        record.command_session_id.as_str(),
        record.command,
    )
}

impl BackgroundTaskSupervisor {
    /// Register a freshly-started background command session as running.
    /// Idempotent: an existing record (already terminal or running) is kept.
    pub fn register_command_session(
        &mut self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        agent_run_id: &AgentRunId,
        command: &str,
    ) {
        self.commands
            .entry(command_session_id.clone())
            .or_insert_with(|| CommandSessionRecord {
                command_session_id: command_session_id.clone(),
                sandbox_id: sandbox_id.clone(),
                agent_run_id: agent_run_id.clone(),
                command: command.to_owned(),
                status: BackgroundTaskStatus::Running,
                result: None,
            });
    }

    /// Apply one pulled daemon completion to its record (heartbeat path). Only a
    /// still-`Running` record is updated, so an already-reported terminal (the
    /// recover/mark latch) is never re-opened.
    pub fn ingest_completion(&mut self, completion: &Value) {
        let Some(id) = completion.get("command_session_id").and_then(Value::as_str) else {
            return;
        };
        let Ok(id) = id.parse() else {
            return;
        };
        let Some(record) = self.commands.get_mut(&id) else {
            return;
        };
        if !matches!(record.status, BackgroundTaskStatus::Running) {
            return;
        }
        let result = completion.get("result").cloned();
        record.status = command_completion_status(result.as_ref());
        record.result = result;
    }

    /// Render one `[BACKGROUND COMPLETED]` notification per terminal-undelivered
    /// record and latch it to `Delivered` (exactly-once).
    pub fn drain_command_session_notifications(&mut self) -> Vec<ToolNotification> {
        let mut notifications = Vec::new();
        for record in self.commands.values_mut() {
            let terminal = matches!(
                record.status,
                BackgroundTaskStatus::Completed
                    | BackgroundTaskStatus::Failed
                    | BackgroundTaskStatus::Cancelled
            );
            if terminal && record.result.is_some() {
                notifications.push(ToolNotification {
                    event: record.command_session_id.as_str().to_owned(),
                    message: render_command_completion(record),
                });
                record.status = BackgroundTaskStatus::Delivered;
            }
        }
        notifications
    }

    /// The stored terminal result for a session that is no longer running (the
    /// recover race), else `None`.
    #[must_use]
    pub fn command_session_result(&self, command_session_id: &CommandSessionId) -> Option<Value> {
        let record = self.commands.get(command_session_id)?;
        if matches!(record.status, BackgroundTaskStatus::Running) {
            return None;
        }
        record.result.clone()
    }

    /// Latch a session to `Delivered` with the terminal `result` a control tool
    /// observed inline, so the heartbeat does not re-deliver it.
    pub fn mark_command_session_reported(
        &mut self,
        command_session_id: &CommandSessionId,
        result: Value,
    ) {
        if let Some(record) = self.commands.get_mut(command_session_id) {
            record.status = BackgroundTaskStatus::Delivered;
            record.result = Some(result);
        }
    }

    /// Whether a tracked session's completion was already delivered to the model
    /// (the heartbeat latched it `Delivered`), so a late `write_stdin` poll can
    /// answer with a terse already-reported note instead of the full payload.
    #[must_use]
    pub fn command_session_already_reported(&self, command_session_id: &CommandSessionId) -> bool {
        self.commands
            .get(command_session_id)
            .is_some_and(|record| matches!(record.status, BackgroundTaskStatus::Delivered))
    }

    /// Running command-session ids grouped by `(sandbox_id, agent_run_id)` — the
    /// heartbeat's pull plan (deterministic order for stable polling).
    #[must_use]
    pub fn running_command_session_ids_by_sandbox_run(
        &self,
    ) -> Vec<((String, AgentRunId), Vec<String>)> {
        let mut groups: std::collections::BTreeMap<(String, AgentRunId), Vec<String>> =
            std::collections::BTreeMap::new();
        for record in self.commands.values() {
            if matches!(record.status, BackgroundTaskStatus::Running) {
                groups
                    .entry((
                        record.sandbox_id.as_str().to_owned(),
                        record.agent_run_id.clone(),
                    ))
                    .or_default()
                    .push(record.command_session_id.as_str().to_owned());
            }
        }
        groups.into_iter().collect()
    }

    /// Count tracked, still-running command sessions for one agent run, or all
    /// runs when `agent_run_id` is `None`.
    #[must_use]
    pub fn count_commands_by_run(&self, agent_run_id: Option<&AgentRunId>) -> usize {
        self.commands
            .values()
            .filter(|record| {
                matches!(record.status, BackgroundTaskStatus::Running)
                    && super::supervisor::matches_agent_run(&record.agent_run_id, agent_run_id)
            })
            .count()
    }
}

#[async_trait]
impl CommandSessionSupervisorPort for BackgroundSupervisorHandle {
    async fn register(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        agent_run_id: &AgentRunId,
        command: &str,
    ) {
        self.inner().lock().await.register_command_session(
            command_session_id,
            sandbox_id,
            agent_run_id,
            command,
        );
    }

    async fn command_session_result(&self, command_session_id: &CommandSessionId) -> Option<Value> {
        self.inner()
            .lock()
            .await
            .command_session_result(command_session_id)
    }

    async fn mark_command_session_reported(
        &self,
        command_session_id: &CommandSessionId,
        result: Value,
    ) {
        self.inner()
            .lock()
            .await
            .mark_command_session_reported(command_session_id, result);
    }

    async fn command_session_already_reported(
        &self,
        command_session_id: &CommandSessionId,
    ) -> bool {
        self.inner()
            .lock()
            .await
            .command_session_already_reported(command_session_id)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn completion(id: &str, agent: &str, status: &str, stdout: &str) -> Value {
        json!({
            "command_session_id": id,
            "agent_run_id": agent,
            "command": "cargo test -q",
            "result": {
                "status": status,
                "exit_code": if status == "ok" { 0 } else { 1 },
                "output": {"stdout": stdout, "stderr": ""},
            },
        })
    }

    fn csid(id: &str) -> CommandSessionId {
        id.parse().expect("valid command session id")
    }

    fn sbid(id: &str) -> SandboxId {
        id.parse().expect("valid sandbox id")
    }

    fn arid(id: &str) -> AgentRunId {
        id.parse().expect("valid agent run id")
    }

    #[test]
    fn register_pull_completed_flips_count_and_renders_once() {
        let mut supervisor = BackgroundTaskSupervisor::new();
        let agent_run = arid("agent-a");
        supervisor.register_command_session(
            &csid("cmd_1"),
            &sbid("sb"),
            &agent_run,
            "cargo test -q",
        );
        assert_eq!(supervisor.count_commands_by_run(Some(&agent_run)), 1);

        supervisor.ingest_completion(&completion("cmd_1", "agent-a", "ok", "3 passed"));
        // Terminal → no longer counted as running.
        assert_eq!(supervisor.count_commands_by_run(Some(&agent_run)), 0);

        let first = supervisor.drain_command_session_notifications();
        assert_eq!(first.len(), 1);
        assert!(first[0].message.contains("[BACKGROUND COMPLETED]"));
        assert!(first[0].message.contains("command_session_id=cmd_1"));
        assert!(first[0].message.contains("status=ok"));
        assert!(first[0].message.contains("3 passed"));

        // Exactly-once: the Delivered latch suppresses a second drain.
        assert!(supervisor.drain_command_session_notifications().is_empty());
    }

    #[test]
    fn recover_race_returns_stored_terminal_and_marks_reported() {
        let mut supervisor = BackgroundTaskSupervisor::new();
        supervisor.register_command_session(&csid("cmd_2"), &sbid("sb"), &arid("agent-a"), "make");
        // Still running → no recoverable result yet, not reported.
        assert!(supervisor.command_session_result(&csid("cmd_2")).is_none());
        assert!(!supervisor.command_session_already_reported(&csid("cmd_2")));

        supervisor.ingest_completion(&completion("cmd_2", "agent-a", "error", "boom"));
        // Terminal (Failed) → recover returns the stored result; not yet reported.
        let recovered = supervisor
            .command_session_result(&csid("cmd_2"))
            .expect("stored terminal");
        assert_eq!(recovered["status"], "error");
        assert!(!supervisor.command_session_already_reported(&csid("cmd_2")));

        // The control tool latches it Delivered → heartbeat drain stays empty and
        // a late write_stdin poll sees it already-reported (the terse §8/D8 path).
        supervisor.mark_command_session_reported(&csid("cmd_2"), recovered);
        assert!(supervisor.drain_command_session_notifications().is_empty());
        assert!(supervisor.command_session_already_reported(&csid("cmd_2")));
    }

    #[test]
    fn running_ids_group_by_sandbox_and_agent_run() {
        let mut supervisor = BackgroundTaskSupervisor::new();
        let agent_a = arid("agent-a");
        let agent_b = arid("agent-b");
        supervisor.register_command_session(&csid("cmd_a"), &sbid("sb1"), &agent_a, "a");
        supervisor.register_command_session(&csid("cmd_b"), &sbid("sb1"), &agent_a, "b");
        supervisor.register_command_session(&csid("cmd_c"), &sbid("sb2"), &agent_b, "c");
        let groups = supervisor.running_command_session_ids_by_sandbox_run();
        assert_eq!(groups.len(), 2);
        let agent_a = groups
            .iter()
            .find(|((sandbox, agent), _)| sandbox == "sb1" && agent.as_str() == "agent-a")
            .expect("agent-a group");
        assert_eq!(agent_a.1.len(), 2);
    }

    #[test]
    fn untracked_completion_is_ignored() {
        let mut supervisor = BackgroundTaskSupervisor::new();
        supervisor.ingest_completion(&completion("cmd_unknown", "agent-a", "ok", "x"));
        assert!(supervisor.drain_command_session_notifications().is_empty());
    }
}

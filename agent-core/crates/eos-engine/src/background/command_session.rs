use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_sandbox_port::SandboxCommandApi;
use eos_types::{AgentRunId, CommandSessionId, SandboxId};
use serde_json::Value;
use tokio::sync::{Mutex, Notify};
use tokio::task::JoinHandle;

use super::session_runtime::{
    BackgroundSession, BackgroundSessionManager, BackgroundSessionStatus,
};
use crate::background::notification::{BackgroundCompletion, BackgroundNotificationEmitter};

/// One tracked background command session.
#[derive(Debug, Clone)]
pub(in crate::background) struct CommandSession {
    id: CommandSessionId,
    sandbox_id: SandboxId,
    status: BackgroundSessionStatus,
    result: Option<Value>,
}

impl CommandSession {
    fn running(id: CommandSessionId, sandbox_id: SandboxId) -> Self {
        Self {
            id,
            sandbox_id,
            status: BackgroundSessionStatus::Running,
            result: None,
        }
    }

    fn sandbox_id(&self) -> &SandboxId {
        &self.sandbox_id
    }

    const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    fn deliver(&mut self, result: Value) -> BackgroundSessionStatus {
        let status = command_completion_status(Some(&result));
        self.result = Some(result);
        self.status = BackgroundSessionStatus::Delivered;
        status
    }

    fn cancel(&mut self) {
        if matches!(self.status, BackgroundSessionStatus::Running) {
            self.status = BackgroundSessionStatus::Cancelled;
            self.result = Some(serde_json::json!({
                "status": "cancelled",
                "exit_code": Value::Null,
                "output": {"stdout": "", "stderr": ""},
            }));
        }
    }
}

impl BackgroundSession for CommandSession {
    type Id = CommandSessionId;

    fn id(&self) -> &Self::Id {
        &self.id
    }
}

fn command_completion_status(result: Option<&Value>) -> BackgroundSessionStatus {
    match result
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
    {
        Some("ok") => BackgroundSessionStatus::Completed,
        Some("cancelled") => BackgroundSessionStatus::Cancelled,
        _ => BackgroundSessionStatus::Failed,
    }
}

type CommandSessions = HashMap<CommandSessionId, CommandSession>;

#[derive(Debug, Clone)]
pub(in crate::background) struct CommandCompletion {
    pub(super) command_session_id: CommandSessionId,
    pub(super) sandbox_id: SandboxId,
    pub(super) status: BackgroundSessionStatus,
    pub(super) result: Value,
}

/// Tracks sandbox command sessions for one agent run.
#[derive(Clone)]
pub(in crate::background) struct CommandSessionManager {
    sessions: Arc<Mutex<CommandSessions>>,
    monitor_wakeup: Arc<Notify>,
    agent_run_id: AgentRunId,
    command_service: Arc<dyn SandboxCommandApi>,
    notification: BackgroundNotificationEmitter,
}

impl std::fmt::Debug for CommandSessionManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandSessionManager")
            .field("agent_run_id", &self.agent_run_id)
            .finish_non_exhaustive()
    }
}

impl CommandSessionManager {
    pub(in crate::background) fn new(
        agent_run_id: AgentRunId,
        command_service: Arc<dyn SandboxCommandApi>,
        notification: BackgroundNotificationEmitter,
    ) -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
            monitor_wakeup: Arc::new(Notify::new()),
            agent_run_id,
            command_service,
            notification,
        }
    }

    pub(in crate::background) async fn register_background_session(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
    ) {
        let session = CommandSession::running(command_session_id.clone(), sandbox_id.clone());
        {
            self.sessions
                .lock()
                .await
                .entry(command_session_id.clone())
                .or_insert(session);
        }
        self.monitor_wakeup.notify_one();
    }

    async fn has_running_sessions(&self) -> bool {
        self.sessions
            .lock()
            .await
            .values()
            .any(|session| matches!(session.status(), BackgroundSessionStatus::Running))
    }

    async fn wait_for_running_session(&self) {
        loop {
            if self.has_running_sessions().await {
                return;
            }
            self.monitor_wakeup.notified().await;
        }
    }

    fn running_by_sandbox(sessions: &CommandSessions) -> Vec<(SandboxId, Vec<CommandSessionId>)> {
        let mut groups: BTreeMap<SandboxId, Vec<CommandSessionId>> = BTreeMap::new();
        for session in sessions.values() {
            if matches!(session.status(), BackgroundSessionStatus::Running) {
                groups
                    .entry(session.sandbox_id().clone())
                    .or_default()
                    .push(session.id().clone());
            }
        }
        groups.into_iter().collect()
    }

    fn running_sandboxes(sessions: &CommandSessions) -> Vec<SandboxId> {
        let mut seen: BTreeMap<SandboxId, ()> = BTreeMap::new();
        for session in sessions.values() {
            if matches!(session.status(), BackgroundSessionStatus::Running) {
                seen.insert(session.sandbox_id().clone(), ());
            }
        }
        seen.into_keys().collect()
    }

    fn ingest_completions(
        sessions: &mut CommandSessions,
        completions: &[eos_types::JsonObject],
    ) -> Vec<CommandCompletion> {
        let mut out = Vec::new();
        for completion in completions {
            let Some(id) = completion
                .get("command_session_id")
                .and_then(Value::as_str)
                .and_then(|id| id.parse::<CommandSessionId>().ok())
            else {
                continue;
            };
            let Some(session) = sessions.get_mut(&id) else {
                continue;
            };
            if !matches!(session.status(), BackgroundSessionStatus::Running) {
                continue;
            }
            let result = completion.get("result").cloned().unwrap_or(Value::Null);
            let status = session.deliver(result.clone());
            out.push(CommandCompletion {
                command_session_id: id,
                sandbox_id: session.sandbox_id().clone(),
                status,
                result,
            });
        }
        out
    }

    pub(in crate::background) async fn poll_completions(&self) -> Vec<CommandCompletion> {
        let groups = {
            let guard = self.sessions.lock().await;
            Self::running_by_sandbox(&guard)
        };
        let mut out = Vec::new();
        for (sandbox_id, ids) in groups {
            let Ok(completions) = self
                .command_service
                .collect_completed_commands(&sandbox_id, &self.agent_run_id, &ids)
                .await
            else {
                continue;
            };
            if completions.is_empty() {
                continue;
            }
            let mut guard = self.sessions.lock().await;
            out.extend(Self::ingest_completions(&mut guard, &completions));
        }
        out
    }
}

pub(in crate::background) struct CommandSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for CommandSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl CommandSessionMonitor {
    pub(in crate::background) fn spawn(manager: CommandSessionManager, interval: Duration) -> Self {
        Self {
            join: tokio::spawn(async move {
                loop {
                    manager.wait_for_running_session().await;
                    for completion in manager.poll_completions().await {
                        manager.push_notification_on_completion(completion).await;
                    }
                    tokio::time::sleep(interval).await;
                }
            }),
        }
    }
}

#[async_trait]
impl BackgroundSessionManager for CommandSessionManager {
    type Session = CommandSession;
    type Completion = CommandCompletion;

    async fn insert(&self, session: Self::Session) {
        self.sessions
            .lock()
            .await
            .insert(session.id().clone(), session);
        self.monitor_wakeup.notify_one();
    }

    async fn count(&self) -> usize {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .count()
    }

    async fn push_notification_on_completion(&self, completion: Self::Completion) {
        let _ = self
            .notification
            .emit(BackgroundCompletion::CommandSession {
                command_session_id: completion.command_session_id,
                sandbox_id: completion.sandbox_id,
                status: completion.status,
                result: completion.result,
            })
            .await;
    }

    async fn cancel(&self, reason: &str) {
        let sandboxes = {
            let guard = self.sessions.lock().await;
            Self::running_sandboxes(&guard)
        };
        for sandbox in sandboxes {
            if let Err(err) = self
                .command_service
                .cancel_commands_for_run(&sandbox, &self.agent_run_id, reason)
                .await
            {
                tracing::warn!(
                    error = %err,
                    caller_id = self.agent_run_id.as_str(),
                    sandbox_id = sandbox.as_str(),
                    reason,
                    "per-caller workspace-run cancellation failed"
                );
            }
        }
        for session in self.sessions.lock().await.values_mut() {
            session.cancel();
        }
    }
}

#[cfg(test)]
#[path = "../../tests/background/command_session/mod.rs"]
mod tests;

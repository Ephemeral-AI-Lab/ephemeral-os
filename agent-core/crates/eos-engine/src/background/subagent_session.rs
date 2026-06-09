use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_tool::ToolResult;
#[cfg(test)]
use eos_types::AgentRun;
use eos_types::{AgentRunApi, AgentRunId, AgentRunOutcome, AgentRunStatus, JsonObject};
use serde_json::{json, Value};
use tokio::sync::{Mutex, Notify};
use tokio::task::JoinHandle;

use super::session_runtime::{
    BackgroundSession, BackgroundSessionManager, BackgroundSessionStatus,
};
use crate::background::notification::{BackgroundCompletion, BackgroundNotificationEmitter};

/// One tracked subagent run owned by an agent run's background session runtime.
#[derive(Debug, Clone)]
pub(in crate::background) struct SubagentSession {
    agent_run_id: AgentRunId,
    status: BackgroundSessionStatus,
    result: Option<ToolResult>,
}

impl SubagentSession {
    fn tracked(agent_run_id: AgentRunId) -> Self {
        Self {
            agent_run_id,
            status: BackgroundSessionStatus::Running,
            result: None,
        }
    }

    fn agent_run_id(&self) -> &AgentRunId {
        &self.agent_run_id
    }

    const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    fn cancel(&mut self, reason: &str) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = BackgroundSessionStatus::Cancelled;
        self.result = Some(
            ToolResult::error(format!("Background subagent cancelled: {reason}"))
                .meta("subagent_cancelled", serde_json::json!(true)),
        );
        true
    }

    fn settle(
        &mut self,
        status: BackgroundSessionStatus,
        result: ToolResult,
    ) -> Option<ToolResult> {
        if status.precedence() <= self.status.precedence() {
            return None;
        }
        self.status = status;
        self.result = Some(result.clone());
        Some(result)
    }
}

impl BackgroundSession for SubagentSession {
    type Id = AgentRunId;

    fn id(&self) -> &Self::Id {
        &self.agent_run_id
    }
}

#[derive(Debug, Clone)]
pub(in crate::background) struct SubagentCompletion {
    pub(super) agent_run_id: AgentRunId,
    pub(super) status: BackgroundSessionStatus,
    pub(super) result: ToolResult,
}

#[derive(Default)]
struct SubagentSessionState {
    sessions: HashMap<AgentRunId, SubagentSession>,
}

/// Tracks subagent background sessions for one agent run.
#[derive(Clone)]
pub(in crate::background) struct SubagentSessionManager {
    agent_run_id: AgentRunId,
    sessions: Arc<Mutex<SubagentSessionState>>,
    monitor_wakeup: Arc<Notify>,
    agent_run_service: Arc<dyn AgentRunApi>,
    notification: BackgroundNotificationEmitter,
}

impl std::fmt::Debug for SubagentSessionManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SubagentSessionManager")
            .field("agent_run_id", &self.agent_run_id)
            .finish_non_exhaustive()
    }
}

impl SubagentSessionManager {
    pub(in crate::background) fn new(
        agent_run_id: AgentRunId,
        agent_run_service: Arc<dyn AgentRunApi>,
        notification: BackgroundNotificationEmitter,
    ) -> Self {
        Self {
            agent_run_id,
            sessions: Arc::new(Mutex::new(SubagentSessionState::default())),
            monitor_wakeup: Arc::new(Notify::new()),
            agent_run_service,
            notification,
        }
    }

    async fn cancel_one(&self, agent_run_id: &AgentRunId, reason: &str) -> bool {
        let agent_run_id = {
            let mut guard = self.sessions.lock().await;
            let Some(session) = guard.sessions.get_mut(agent_run_id) else {
                return false;
            };
            if session.cancel(reason) {
                Some(session.agent_run_id().clone())
            } else {
                None
            }
        };
        let Some(agent_run_id) = agent_run_id else {
            return false;
        };
        if let Err(err) = self
            .agent_run_service
            .cancel_agent_run(&agent_run_id, reason)
            .await
        {
            tracing::warn!(
                error = %err,
                agent_run_id = agent_run_id.as_str(),
                "background subagent cancellation failed"
            );
        }
        true
    }

    pub(in crate::background) async fn cancel_agent_run(
        &self,
        child_run_id: &AgentRunId,
        reason: &str,
    ) -> bool {
        self.cancel_one(child_run_id, reason).await
    }

    pub(super) async fn settle(
        &self,
        agent_run_id: &AgentRunId,
        status: BackgroundSessionStatus,
        result: ToolResult,
    ) -> Option<SubagentCompletion> {
        let mut guard = self.sessions.lock().await;
        let session = guard.sessions.get_mut(agent_run_id)?;
        let result = session.settle(status, result)?;
        Some(SubagentCompletion {
            agent_run_id: agent_run_id.clone(),
            status: session.status(),
            result,
        })
    }

    pub(in crate::background) async fn poll_completions(&self) -> Vec<SubagentCompletion> {
        let running = self.running_agent_runs().await;
        let mut completions = Vec::new();
        for agent_run_id in running {
            let terminal = match self
                .agent_run_service
                .poll_agent_run_outcome(&agent_run_id)
                .await
            {
                Ok(terminal) => terminal,
                Err(_) => continue,
            };
            let Some(terminal) = terminal else {
                continue;
            };
            let status = agent_run_status_to_background(terminal.status);
            let result = submission_outcome(terminal);
            let is_error = result.is_error;
            if let Some(completion) = self.settle(&agent_run_id, status, result).await {
                trace_background_tool(
                    terminal_event_type(status),
                    &agent_run_id,
                    status,
                    Some(i64::from(is_error)),
                );
                completions.push(completion);
            }
        }
        completions
    }

    async fn running_agent_runs(&self) -> Vec<AgentRunId> {
        self.sessions
            .lock()
            .await
            .sessions
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .map(|session| session.agent_run_id().clone())
            .collect()
    }

    async fn has_running_sessions(&self) -> bool {
        self.sessions
            .lock()
            .await
            .sessions
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
}

pub(in crate::background) struct SubagentSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for SubagentSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl SubagentSessionMonitor {
    pub(in crate::background) fn spawn(
        manager: SubagentSessionManager,
        interval: Duration,
    ) -> Self {
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
impl BackgroundSessionManager for SubagentSessionManager {
    type Session = SubagentSession;
    type Completion = SubagentCompletion;

    async fn insert(&self, session: Self::Session) {
        self.sessions
            .lock()
            .await
            .sessions
            .insert(session.id().clone(), session);
        self.monitor_wakeup.notify_one();
    }

    async fn count(&self) -> usize {
        self.sessions
            .lock()
            .await
            .sessions
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .count()
    }

    async fn push_notification_on_completion(&self, completion: Self::Completion) {
        let _ = self
            .notification
            .emit(BackgroundCompletion::Subagent {
                agent_run_id: completion.agent_run_id,
                status: completion.status,
                result: completion.result,
            })
            .await;
    }

    async fn cancel(&self, reason: &str) {
        let actions = {
            let mut guard = self.sessions.lock().await;
            guard
                .sessions
                .values_mut()
                .filter_map(|session| {
                    if session.cancel(reason) {
                        Some(session.agent_run_id().clone())
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>()
        };
        for agent_run_id in actions {
            if let Err(err) = self
                .agent_run_service
                .cancel_agent_run(&agent_run_id, reason)
                .await
            {
                tracing::warn!(
                    error = %err,
                    agent_run_id = agent_run_id.as_str(),
                    "background subagent cancellation failed"
                );
            }
        }
    }
}

impl SubagentSessionManager {
    pub(in crate::background) async fn register_background_session(
        &self,
        agent_run_id: &AgentRunId,
    ) {
        trace_background_tool(
            "background_tool.started",
            agent_run_id,
            BackgroundSessionStatus::Running,
            None,
        );
        self.insert(SubagentSession::tracked(agent_run_id.clone()))
            .await;
    }

    pub(in crate::background) async fn cancel_background_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> bool {
        self.cancel_agent_run(agent_run_id, reason).await
    }

    pub(in crate::background) async fn cancel_all_background_sessions(&self, reason: &str) {
        BackgroundSessionManager::cancel(self, reason).await;
    }
}

fn submission_outcome(outcome: AgentRunOutcome) -> ToolResult {
    outcome
        .submission_payload
        .as_ref()
        .map(tool_result_from_payload)
        .unwrap_or_else(|| {
            ToolResult::error(
                outcome
                    .error
                    .unwrap_or_else(|| "subagent exited without terminal output".to_owned()),
            )
            .meta("subagent_terminal_called", serde_json::json!(false))
        })
}

const fn agent_run_status_to_background(status: AgentRunStatus) -> BackgroundSessionStatus {
    match status {
        AgentRunStatus::Completed => BackgroundSessionStatus::Completed,
        AgentRunStatus::Failed => BackgroundSessionStatus::Failed,
        AgentRunStatus::Cancelled => BackgroundSessionStatus::Cancelled,
    }
}

const fn terminal_event_type(status: BackgroundSessionStatus) -> &'static str {
    match status {
        BackgroundSessionStatus::Running => "background_tool.started",
        BackgroundSessionStatus::Completed => "background_tool.completed",
        BackgroundSessionStatus::Failed => "background_tool.failed",
        BackgroundSessionStatus::Cancelled => "background_tool.cancelled",
        BackgroundSessionStatus::Delivered => "background_tool.delivered",
    }
}

const fn status_value(status: BackgroundSessionStatus) -> &'static str {
    match status {
        BackgroundSessionStatus::Running => "running",
        BackgroundSessionStatus::Completed => "completed",
        BackgroundSessionStatus::Failed => "failed",
        BackgroundSessionStatus::Cancelled => "cancelled",
        BackgroundSessionStatus::Delivered => "delivered",
    }
}

fn trace_background_tool(
    event_type: &str,
    agent_run_id: &AgentRunId,
    status: BackgroundSessionStatus,
    exit_code: Option<i64>,
) {
    tracing::debug!(
        target: "eos_engine::diagnostics",
        event_type,
        background_task_id = agent_run_id.as_str(),
        task_kind = "subagent",
        tool_name = "run_subagent",
        agent_run_id = agent_run_id.as_str(),
        status = status_value(status),
        exit_code,
        "background tool lifecycle"
    );
}

#[cfg(test)]
pub(super) fn completion_from_agent_run(
    run: &AgentRun,
) -> Option<(BackgroundSessionStatus, ToolResult, i64)> {
    run.finished_at?;
    if let Some(terminal) = &run.terminal_payload {
        let result = tool_result_from_payload(terminal);
        let exit_code = i64::from(result.is_error);
        return Some((BackgroundSessionStatus::Completed, result, exit_code));
    }
    let message = match &run.error {
        Some(error) => format!("subagent crashed: {error}"),
        None => "subagent exited without calling a terminal tool. Findings were not delivered."
            .to_owned(),
    };
    Some((
        BackgroundSessionStatus::Failed,
        ToolResult::error(message).meta("subagent_terminal_called", json!(false)),
        1,
    ))
}

fn tool_result_from_payload(payload: &JsonObject) -> ToolResult {
    let output = payload
        .get("output")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let is_error = payload
        .get("is_error")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let is_terminal = payload
        .get("is_terminal")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut metadata = payload
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.insert("subagent_terminal_called".to_owned(), json!(true));
    ToolResult {
        output,
        is_error,
        metadata,
        is_terminal,
    }
}

#[cfg(test)]
#[path = "../../tests/background/subagent_session/mod.rs"]
mod tests;

mod command_session_manager;
mod subagent_session_manager;
mod workflow_session_manager;

use std::future::Future;
use std::hash::Hash;
use std::pin::Pin;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use async_trait::async_trait;
use eos_agent_ports::{AgentRunApi, AgentRunError, AgentRunOutcome, SpawnAgentRequest};
use eos_sandbox_port::SandboxCommandApi;
use eos_tool_ports::{
    BackgroundSessionCounts, CommandSessionToolService, SubagentToolService, WorkflowToolService,
};
use eos_types::AgentRunId;
use eos_types::WorkflowApi;

use self::command_session_manager::{CommandSessionManager, CommandSessionMonitor};
use self::subagent_session_manager::{SubagentSessionManager, SubagentSessionMonitor};
use self::workflow_session_manager::{
    WorkflowServiceCell, WorkflowSessionManager, WorkflowSessionMonitor,
};
use super::notification::BackgroundNotificationEmitter;
use crate::notifications::NotificationService;
use crate::query::{QueryContext, QueryExitReason};

type BackgroundTeardownFuture = Pin<Box<dyn Future<Output = BackgroundSessionCounts> + Send>>;
type BackgroundTeardownCallback = Arc<dyn Fn(String) -> BackgroundTeardownFuture + Send + Sync>;

/// Lifecycle status for one tracked background session.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackgroundSessionStatus {
    /// The session is still running.
    Running,
    /// The session completed normally.
    Completed,
    /// The session failed.
    Failed,
    /// The session was cancelled.
    Cancelled,
    /// The terminal tool outcome was already delivered to the model.
    Delivered,
}

impl BackgroundSessionStatus {
    /// Terminal precedence; higher status wins when cancel/completion events race.
    #[must_use]
    pub const fn precedence(self) -> u8 {
        match self {
            Self::Running => 0,
            Self::Cancelled => 1,
            Self::Failed => 2,
            Self::Completed => 3,
            Self::Delivered => 4,
        }
    }
}

trait BackgroundSession {
    type Id: Eq + Hash + Clone + Send + Sync + 'static;

    fn id(&self) -> &Self::Id;
}

#[async_trait]
trait BackgroundSessionManager {
    type Session: BackgroundSession + Send + 'static;
    type Completion: Send + 'static;

    async fn insert(&self, session: Self::Session);
    async fn count(&self) -> usize;
    async fn push_notification_on_completion(&self, completion: Self::Completion);
    async fn cancel(&self, reason: &str);
}

/// Per-agent-run aggregate for background session accounting and lifecycle.
pub(super) struct BackgroundSessionRuntime {
    agent_run_id: AgentRunId,
    agent_run_service: Arc<dyn AgentRunApi>,
    subagent_session_manager: SubagentSessionManager,
    workflow_session_manager: WorkflowSessionManager,
    command_session_manager: CommandSessionManager,
    _subagent_monitor: SubagentSessionMonitor,
    _workflow_monitor: WorkflowSessionMonitor,
    _command_monitor: CommandSessionMonitor,
}

impl BackgroundSessionRuntime {
    pub(super) fn new(
        agent_run_id: AgentRunId,
        agent_run_service: Arc<dyn AgentRunApi>,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        notifications: NotificationService,
        workflow_service: WorkflowServiceCell,
    ) -> Self {
        let notification = BackgroundNotificationEmitter::new(notifications);
        let subagent_session_manager = SubagentSessionManager::new(
            agent_run_id.clone(),
            agent_run_service.clone(),
            notification.clone(),
        );
        let workflow_session_manager = WorkflowSessionManager::new(
            agent_run_id.clone(),
            workflow_service,
            notification.clone(),
        );
        let command_session_manager =
            CommandSessionManager::new(agent_run_id.clone(), command_service, notification);
        let subagent_monitor = SubagentSessionMonitor::spawn(
            subagent_session_manager.clone(),
            completion_poll_interval,
        );
        let workflow_monitor = WorkflowSessionMonitor::spawn(
            workflow_session_manager.clone(),
            completion_poll_interval,
        );
        let command_monitor =
            CommandSessionMonitor::spawn(command_session_manager.clone(), completion_poll_interval);
        Self {
            agent_run_id,
            agent_run_service,
            subagent_session_manager,
            workflow_session_manager,
            command_session_manager,
            _subagent_monitor: subagent_monitor,
            _workflow_monitor: workflow_monitor,
            _command_monitor: command_monitor,
        }
    }

    pub(super) fn agent_run_id(&self) -> &AgentRunId {
        &self.agent_run_id
    }

    pub(super) fn subagent_session_manager(&self) -> &SubagentSessionManager {
        &self.subagent_session_manager
    }

    pub(super) fn agent_run_service(&self) -> &Arc<dyn AgentRunApi> {
        &self.agent_run_service
    }

    pub(super) fn workflow_session_manager(&self) -> &WorkflowSessionManager {
        &self.workflow_session_manager
    }

    pub(super) fn command_session_manager(&self) -> &CommandSessionManager {
        &self.command_session_manager
    }

    pub(super) async fn count(&self) -> BackgroundSessionCounts {
        let subagents = self.subagent_session_manager.count().await;
        let workflows = self.workflow_session_manager.count().await;
        let command_sessions = self.command_session_manager.count().await;
        BackgroundSessionCounts {
            total: subagents + workflows + command_sessions,
            subagents,
            workflows,
            command_sessions,
        }
    }
}

/// Cloneable aggregate for one agent run's background session runtime.
#[derive(Clone)]
pub struct BackgroundManagers {
    runtime: Arc<BackgroundSessionRuntime>,
}

impl std::fmt::Debug for BackgroundManagers {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundManagers")
            .field("agent_run_id", self.runtime.agent_run_id())
            .finish_non_exhaustive()
    }
}

impl BackgroundManagers {
    #[must_use]
    pub fn new(
        agent_run_id: AgentRunId,
        agent_run_service: Arc<dyn AgentRunApi>,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        notifications: NotificationService,
        workflow_service: &Arc<OnceLock<Arc<dyn WorkflowApi>>>,
    ) -> Self {
        Self {
            runtime: Arc::new(BackgroundSessionRuntime::new(
                agent_run_id,
                agent_run_service,
                command_service,
                completion_poll_interval,
                notifications,
                workflow_service.clone(),
            )),
        }
    }

    #[must_use]
    pub fn agent_run_id(&self) -> &AgentRunId {
        self.runtime.agent_run_id()
    }

    pub async fn teardown(&self, reason: &str) -> BackgroundSessionCounts {
        self.runtime.subagent_session_manager().cancel(reason).await;
        self.runtime.workflow_session_manager().cancel(reason).await;
        self.runtime.command_session_manager().cancel(reason).await;
        self.runtime.count().await
    }

    #[must_use]
    pub fn teardown_service(&self) -> BackgroundTeardownService {
        let background = self.clone();
        BackgroundTeardownService::new(move |reason| {
            let background = background.clone();
            async move { background.teardown(&reason).await }
        })
    }

    #[must_use]
    pub fn subagent_tool_service(&self) -> SubagentToolService {
        let register = self.clone();
        let cancel = self.clone();
        let count = self.clone();
        let cancel_all = self.clone();
        SubagentToolService::new(
            move |agent_run_id| {
                let service = register.clone();
                async move {
                    service
                        .runtime
                        .subagent_session_manager()
                        .register_background_session(&agent_run_id)
                        .await;
                }
            },
            move |agent_run_id, reason| {
                let service = cancel.clone();
                async move {
                    service
                        .runtime
                        .subagent_session_manager()
                        .cancel_background_agent_run(&agent_run_id, &reason)
                        .await
                }
            },
            move || {
                let service = count.clone();
                async move {
                    service
                        .runtime
                        .subagent_session_manager()
                        .count_background_sessions()
                        .await
                }
            },
            move |reason| {
                let service = cancel_all.clone();
                async move {
                    service
                        .runtime
                        .subagent_session_manager()
                        .cancel_all_background_sessions(&reason)
                        .await;
                }
            },
        )
    }

    #[must_use]
    pub fn workflow_tool_service(&self) -> WorkflowToolService {
        let register = self.clone();
        WorkflowToolService::new(move |workflow| {
            let service = register.clone();
            async move {
                service
                    .runtime
                    .workflow_session_manager()
                    .register_background_session(&workflow)
                    .await;
            }
        })
    }

    #[must_use]
    pub fn command_session_tool_service(&self) -> CommandSessionToolService {
        let register = self.clone();
        CommandSessionToolService::new(move |command_session_id, sandbox_id| {
            let service = register.clone();
            async move {
                service
                    .runtime
                    .command_session_manager()
                    .register_background_session(&command_session_id, &sandbox_id)
                    .await;
            }
        })
    }
}

#[async_trait]
impl AgentRunApi for BackgroundManagers {
    async fn spawn_agent(
        &self,
        request: SpawnAgentRequest,
    ) -> Result<eos_types::AgentRunId, AgentRunError> {
        AgentRunApi::spawn_agent(self.runtime.agent_run_service().as_ref(), request).await
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        AgentRunApi::wait_for_agent_outcome(self.runtime.agent_run_service().as_ref(), agent_run_id)
            .await
    }

    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &eos_types::AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        AgentRunApi::poll_agent_run_outcome(self.runtime.agent_run_service().as_ref(), agent_run_id)
            .await
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &eos_types::AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError> {
        AgentRunApi::cancel_agent_run(
            self.runtime.agent_run_service().as_ref(),
            agent_run_id,
            reason,
        )
        .await
    }
}

#[derive(Clone)]
pub struct BackgroundTeardownService {
    teardown: BackgroundTeardownCallback,
}

impl BackgroundTeardownService {
    #[must_use]
    pub fn new<Teardown, TeardownFuture>(teardown: Teardown) -> Self
    where
        Teardown: Fn(String) -> TeardownFuture + Send + Sync + 'static,
        TeardownFuture: Future<Output = BackgroundSessionCounts> + Send + 'static,
    {
        Self {
            teardown: Arc::new(move |reason| Box::pin(teardown(reason))),
        }
    }

    pub async fn teardown(&self, reason: &str) -> BackgroundSessionCounts {
        (self.teardown)(reason.to_owned()).await
    }
}

impl std::fmt::Debug for BackgroundTeardownService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundTeardownService")
            .finish_non_exhaustive()
    }
}

/// Normal-exit background cleanup for one agent run.
pub(crate) struct BackgroundSessionFinalizer {
    background: Option<BackgroundTeardownService>,
    armed: bool,
}

impl BackgroundSessionFinalizer {
    pub(crate) fn new(background: Option<BackgroundTeardownService>) -> Self {
        Self {
            background,
            armed: true,
        }
    }

    pub(crate) async fn finalize(&mut self, ctx: &QueryContext, error: Option<&str>) {
        let Some(background) = &self.background else {
            self.disarm();
            return;
        };
        let reason = finalize_reason(ctx.exit_reason, error);
        background.teardown(&reason).await;
        self.disarm();
    }

    /// Disarm without running cleanup: the caller has handed background teardown
    /// to another owner, so neither `finalize` nor the `Drop` backstop should fire
    /// a second teardown.
    pub(crate) fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for BackgroundSessionFinalizer {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        let Some(background) = self.background.take() else {
            return;
        };
        let reason = "engine run dropped before background finalization".to_owned();
        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            tracing::warn!(
                "engine run dropped outside a Tokio runtime; background cleanup could not be spawned"
            );
            return;
        };
        handle.spawn(async move {
            background.teardown(&reason).await;
        });
    }
}

fn finalize_reason(exit_reason: Option<QueryExitReason>, error: Option<&str>) -> String {
    match (exit_reason, error) {
        (_, Some(error)) => format!("engine run failed: {error}"),
        (Some(QueryExitReason::TerminalNotSubmitted), None) => {
            "parent agent exited without submitting a terminal tool".to_owned()
        }
        (Some(QueryExitReason::ToolStop), None) => "parent agent submitted its terminal".to_owned(),
        (None, None) => "parent agent exited".to_owned(),
    }
}

#[cfg(test)]
mod finalizer_tests {
    #![allow(clippy::expect_used)]

    use tokio::sync::mpsc;
    use tokio::time::{timeout, Duration};

    use super::*;

    fn recording_background(tx: mpsc::UnboundedSender<String>) -> BackgroundTeardownService {
        BackgroundTeardownService::new(move |reason| {
            let tx = tx.clone();
            async move {
                tx.send(reason).expect("send cleanup");
                BackgroundSessionCounts {
                    total: 0,
                    subagents: 0,
                    workflows: 0,
                    command_sessions: 0,
                }
            }
        })
    }

    #[tokio::test]
    async fn drop_spawns_background_cleanup_when_still_armed() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        let background = recording_background(tx);

        {
            let _finalizer = BackgroundSessionFinalizer::new(Some(background));
        }

        let reason = timeout(Duration::from_millis(100), rx.recv())
            .await
            .expect("cleanup spawned")
            .expect("cleanup message");
        assert_eq!(reason, "engine run dropped before background finalization");
    }
}

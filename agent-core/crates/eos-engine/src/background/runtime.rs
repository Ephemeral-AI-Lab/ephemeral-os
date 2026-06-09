use std::future::Future;
use std::hash::Hash;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use eos_sandbox_port::SandboxCommandApi;
use eos_tool::{BackgroundSessionControl, ToolError};
use eos_types::{
    AgentRunApi, AgentRunId, BackgroundSessionCounts, CommandSessionId, SandboxId, StartedWorkflow,
    WorkflowApi,
};

use super::command_session::{CommandSessionManager, CommandSessionMonitor};
use super::notification::BackgroundNotificationEmitter;
use super::subagent_session::{SubagentSessionManager, SubagentSessionMonitor};
use super::workflow_session::{WorkflowSessionManager, WorkflowSessionMonitor};
use crate::notifications::EngineNotificationQueue;

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

pub(in crate::background) trait BackgroundSession {
    type Id: Eq + Hash + Clone + Send + Sync + 'static;

    fn id(&self) -> &Self::Id;
}

#[async_trait]
pub(in crate::background) trait BackgroundSessionManager {
    type Session: BackgroundSession + Send + 'static;
    type Completion: Send + 'static;

    async fn insert(&self, session: Self::Session);
    async fn count(&self) -> usize;
    async fn push_notification_on_completion(&self, completion: Self::Completion);
    async fn cancel(&self, reason: &str);
}

/// Per-agent-run aggregate for background session accounting and lifecycle.
struct BackgroundSessionRuntimeState {
    agent_run_id: AgentRunId,
    subagent_session_manager: SubagentSessionManager,
    workflow_session_manager: WorkflowSessionManager,
    command_session_manager: CommandSessionManager,
    _subagent_monitor: SubagentSessionMonitor,
    _workflow_monitor: WorkflowSessionMonitor,
    _command_monitor: CommandSessionMonitor,
}

impl BackgroundSessionRuntimeState {
    fn new(
        agent_run_id: AgentRunId,
        agent_run_service: &Arc<dyn AgentRunApi>,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        notifications: EngineNotificationQueue,
        workflow_service: Arc<dyn WorkflowApi>,
    ) -> Self {
        let notification = BackgroundNotificationEmitter::new(notifications);
        let subagent_session_manager = SubagentSessionManager::new(
            agent_run_id.clone(),
            Arc::clone(agent_run_service),
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
pub struct BackgroundSessionRuntime {
    state: Arc<BackgroundSessionRuntimeState>,
}

impl std::fmt::Debug for BackgroundSessionRuntime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundSessionRuntime")
            .field("agent_run_id", self.state.agent_run_id())
            .finish_non_exhaustive()
    }
}

impl BackgroundSessionRuntime {
    #[must_use]
    pub fn new(
        agent_run_id: AgentRunId,
        agent_run_service: &Arc<dyn AgentRunApi>,
        command_service: Arc<dyn SandboxCommandApi>,
        completion_poll_interval: Duration,
        notifications: EngineNotificationQueue,
        workflow_service: Arc<dyn WorkflowApi>,
    ) -> Self {
        Self {
            state: Arc::new(BackgroundSessionRuntimeState::new(
                agent_run_id,
                agent_run_service,
                command_service,
                completion_poll_interval,
                notifications,
                workflow_service,
            )),
        }
    }

    #[must_use]
    pub fn agent_run_id(&self) -> &AgentRunId {
        self.state.agent_run_id()
    }

    pub async fn teardown(&self, reason: &str) -> BackgroundSessionCounts {
        self.state.subagent_session_manager().cancel(reason).await;
        self.state.workflow_session_manager().cancel(reason).await;
        self.state.command_session_manager().cancel(reason).await;
        self.state.count().await
    }

    pub(crate) async fn count(&self) -> BackgroundSessionCounts {
        self.state.count().await
    }

    pub(crate) async fn flush_completions(&self) {
        for completion in self
            .state
            .subagent_session_manager()
            .poll_completions()
            .await
        {
            self.state
                .subagent_session_manager()
                .push_notification_on_completion(completion)
                .await;
        }
        for completion in self
            .state
            .workflow_session_manager()
            .poll_completions()
            .await
        {
            self.state
                .workflow_session_manager()
                .push_notification_on_completion(completion)
                .await;
        }
        for completion in self
            .state
            .command_session_manager()
            .poll_completions()
            .await
        {
            self.state
                .command_session_manager()
                .push_notification_on_completion(completion)
                .await;
        }
    }

    pub(crate) async fn cancel_all_subagents(&self, reason: &str) {
        self.state
            .subagent_session_manager()
            .cancel_all_background_sessions(reason)
            .await;
    }

    #[must_use]
    pub fn session_teardown(&self) -> BackgroundSessionTeardown {
        let background = self.clone();
        BackgroundSessionTeardown::new(move |reason| {
            let background = background.clone();
            async move { background.teardown(&reason).await }
        })
    }
}

#[async_trait]
impl BackgroundSessionControl for BackgroundSessionRuntime {
    async fn register_subagent_run(&self, run: AgentRunId) -> Result<(), ToolError> {
        self.state
            .subagent_session_manager()
            .register_background_session(&run)
            .await;
        Ok(())
    }

    async fn register_command_session(
        &self,
        id: CommandSessionId,
        sandbox: SandboxId,
    ) -> Result<(), ToolError> {
        self.state
            .command_session_manager()
            .register_background_session(&id, &sandbox)
            .await;
        Ok(())
    }

    async fn register_workflow_session(&self, started: StartedWorkflow) -> Result<(), ToolError> {
        self.state
            .workflow_session_manager()
            .register_background_session(&started)
            .await;
        Ok(())
    }

    async fn cancel_subagent_run(&self, run: AgentRunId, reason: &str) -> Result<bool, ToolError> {
        Ok(self
            .state
            .subagent_session_manager()
            .cancel_background_agent_run(&run, reason)
            .await)
    }
}

#[derive(Clone)]
pub struct BackgroundSessionTeardown {
    teardown: BackgroundTeardownCallback,
}

impl BackgroundSessionTeardown {
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

impl std::fmt::Debug for BackgroundSessionTeardown {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundSessionTeardown")
            .finish_non_exhaustive()
    }
}

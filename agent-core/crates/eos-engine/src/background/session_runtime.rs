use std::sync::{Arc, OnceLock};
use std::time::Duration;

use async_trait::async_trait;
use eos_sandbox_port::SandboxTransport;
use eos_tools::ports::{
    BackgroundSupervisorPort, CommandSessionSupervisorPort, RunningBackgroundTasks,
    SpawnedSubagent, StartedWorkflowHandle,
};
use eos_tools::{ExecutionMetadata, ToolError, ToolResult, WorkflowControlPort};
use eos_types::{AgentRunId, CommandSessionId, JsonObject, SandboxId, SubagentSessionId, WorkflowSessionId};
use serde_json::{json, Value};

use super::notification::BackgroundNotificationEmitter;
use super::session_managers::command::{CommandSessionManager, CommandSessionMonitor};
use super::session_managers::subagent::{
    subagent_status_and_result, SubagentSessionManager, SubagentSessionMonitor,
};
use super::session_managers::workflow::{
    WorkflowControlCell, WorkflowSessionManager, WorkflowSessionMonitor,
};
use super::session_managers::{BackgroundSessionManager, BackgroundSessionMonitor};
use crate::notifications::NotificationService;
use crate::runtime::AgentRunControlFactory;
use crate::EngineRunHandles;

/// Per-agent-run aggregate for background session accounting and lifecycle.
pub(super) struct BackgroundSessionRuntime {
    agent_run_id: AgentRunId,
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
        handles: EngineRunHandles,
        command_port: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
        notifications: NotificationService,
        control_factory: AgentRunControlFactory,
        workflow_port: WorkflowControlCell,
    ) -> Self {
        let notification = BackgroundNotificationEmitter::new(notifications);
        let subagent_session_manager =
            SubagentSessionManager::new(handles, control_factory, notification.clone());
        let workflow_session_manager =
            WorkflowSessionManager::new(workflow_port, notification.clone());
        let command_session_manager =
            CommandSessionManager::new(agent_run_id.clone(), command_port, notification);
        let subagent_monitor =
            SubagentSessionMonitor::spawn(subagent_session_manager.clone(), completion_poll_interval);
        let workflow_monitor =
            WorkflowSessionMonitor::spawn(workflow_session_manager.clone(), completion_poll_interval);
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

    pub(super) async fn count(&self) -> RunningBackgroundTasks {
        let subagents = self.subagent_session_manager.count().await;
        let workflows = self.workflow_session_manager.count().await;
        let command_sessions = self.command_session_manager.count().await;
        RunningBackgroundTasks {
            total: subagents + workflows + command_sessions,
            subagents,
            workflows,
            command_sessions,
        }
    }

    pub(super) async fn cancel(&self, reason: &str) -> RunningBackgroundTasks {
        self.subagent_session_manager.cancel(reason).await;
        self.workflow_session_manager.cancel(reason).await;
        self.command_session_manager.cancel(reason).await;
        self.count().await
    }
}

/// Cloneable port-facing service for one agent run's background session runtime.
#[derive(Clone)]
pub struct BackgroundSessionService {
    runtime: Arc<BackgroundSessionRuntime>,
}

impl std::fmt::Debug for BackgroundSessionService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundSessionService")
            .field("agent_run_id", self.runtime.agent_run_id())
            .finish_non_exhaustive()
    }
}

impl BackgroundSessionService {
    #[must_use]
    pub fn new(
        agent_run_id: AgentRunId,
        handles: EngineRunHandles,
        command_port: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
        notifications: NotificationService,
        control_factory: AgentRunControlFactory,
        workflow_port: &Arc<OnceLock<Arc<dyn WorkflowControlPort>>>,
    ) -> Self {
        Self {
            runtime: Arc::new(BackgroundSessionRuntime::new(
                agent_run_id,
                handles,
                command_port,
                completion_poll_interval,
                notifications,
                control_factory,
                workflow_port.clone(),
            )),
        }
    }

    #[must_use]
    pub fn agent_run_id(&self) -> &AgentRunId {
        self.runtime.agent_run_id()
    }

    pub(super) fn command_session_manager(&self) -> &CommandSessionManager {
        self.runtime.command_session_manager()
    }

    pub async fn running_background_tasks(&self) -> RunningBackgroundTasks {
        self.runtime.count().await
    }

    pub async fn cancel(&self, reason: &str) -> RunningBackgroundTasks {
        self.runtime.cancel(reason).await
    }

    pub async fn cancel_subagents(&self, reason: &str) -> RunningBackgroundTasks {
        self.runtime.subagent_session_manager().cancel(reason).await;
        self.runtime.count().await
    }

    pub async fn teardown(
        &self,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
        reason: &str,
    ) -> RunningBackgroundTasks {
        self.runtime.subagent_session_manager().cancel(reason).await;
        self.runtime
            .workflow_session_manager()
            .cancel_with_port(workflow_control, reason)
            .await;
        self.runtime.command_session_manager().cancel(reason).await;
        self.runtime.count().await
    }
}

impl eos_tools::ports::Sealed for BackgroundSessionService {}

#[async_trait]
impl BackgroundSupervisorPort for BackgroundSessionService {
    async fn spawn(
        &self,
        ctx: &ExecutionMetadata,
        agent_name: &str,
        prompt: &str,
    ) -> Result<SpawnedSubagent, ToolError> {
        self.runtime
            .subagent_session_manager()
            .spawn(ctx, agent_name, prompt)
            .await
    }

    async fn progress(
        &self,
        subagent_session_id: &SubagentSessionId,
        _last_n_messages: u8,
    ) -> Result<ToolResult, ToolError> {
        let Some(snapshot) = self
            .runtime
            .subagent_session_manager()
            .progress_snapshot(subagent_session_id)
            .await
        else {
            return Ok(ToolResult::error(format!(
                "No subagent session found with ID: {}",
                subagent_session_id.as_str()
            )));
        };
        let (status, result_text) =
            subagent_status_and_result(snapshot.status, snapshot.result.as_ref());
        let payload = json!({
            "subagent_session_id": subagent_session_id.as_str(),
            "status": status,
            "agent_name": snapshot.agent_name,
            "result": result_text,
        });
        let output = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string());
        let mut metadata = JsonObject::new();
        metadata.insert("subagent_snapshot".to_owned(), payload);
        Ok(ToolResult {
            output,
            is_error: false,
            metadata,
            is_terminal: false,
        })
    }

    async fn cancel(
        &self,
        subagent_session_id: &SubagentSessionId,
        reason: &str,
    ) -> Result<ToolResult, ToolError> {
        let cancelled = self
            .runtime
            .subagent_session_manager()
            .cancel_one(subagent_session_id, reason)
            .await;
        if !cancelled {
            return Ok(ToolResult::error(format!(
                "Could not cancel subagent session {}. It may have already completed \
                 or does not exist.",
                subagent_session_id.as_str()
            )));
        }
        let reason_suffix = if reason.is_empty() {
            String::new()
        } else {
            format!(" Reason: {reason}")
        };
        Ok(ToolResult::ok(format!(
            "Subagent session {} cancellation requested.{reason_suffix}",
            subagent_session_id.as_str()
        )))
    }

    async fn running_background_tasks(&self) -> RunningBackgroundTasks {
        BackgroundSessionService::running_background_tasks(self).await
    }

    async fn cancel_subagents(&self) -> RunningBackgroundTasks {
        BackgroundSessionService::cancel_subagents(self, "parent submitted its terminal").await
    }

    async fn register_workflow(&self, workflow: &StartedWorkflowHandle) {
        self.runtime.workflow_session_manager().register(workflow).await;
    }

    async fn cancel_workflow_record(
        &self,
        workflow_task_id: &WorkflowSessionId,
        _reason: &str,
    ) -> bool {
        self.runtime
            .workflow_session_manager()
            .cancel_record(workflow_task_id)
            .await
    }

    async fn teardown(
        &self,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
        reason: &str,
    ) -> RunningBackgroundTasks {
        BackgroundSessionService::teardown(self, workflow_control, reason).await
    }
}

#[async_trait]
impl CommandSessionSupervisorPort for BackgroundSessionService {
    async fn register(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        command: &str,
    ) {
        self.command_session_manager()
            .register(command_session_id, sandbox_id, command)
            .await;
    }

    async fn command_session_result(&self, command_session_id: &CommandSessionId) -> Option<Value> {
        self.command_session_manager()
            .command_session_result(command_session_id)
            .await
    }

    async fn mark_command_session_reported(
        &self,
        command_session_id: &CommandSessionId,
        result: Value,
    ) {
        self.command_session_manager()
            .mark_command_session_reported(command_session_id, result)
            .await;
    }

    async fn command_session_already_reported(
        &self,
        command_session_id: &CommandSessionId,
    ) -> bool {
        self.command_session_manager()
            .command_session_already_reported(command_session_id)
            .await
    }
}

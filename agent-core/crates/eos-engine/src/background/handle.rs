//! [`BackgroundSupervisorHandle`] — the per-agent-run background object (spec
//! §8.2). It wraps an `Arc<BackgroundSupervisorRuntime>` that owns this run's
//! `owner_agent_run_id`, the subagent+workflow ledger
//! ([`BackgroundTaskSupervisor`], behind a `Mutex`), and — as a sibling, not
//! inside that `Mutex` — the [`CommandSessionLane`] (its own interior lock plus
//! the command-completion heartbeat). It is the real
//! [`BackgroundSupervisorPort`](eos_tools::ports::BackgroundSupervisorPort) (impl
//! in `subagent.rs`) and
//! [`CommandSessionSupervisorPort`](eos_tools::ports::CommandSessionSupervisorPort)
//! (impl in `command_session.rs`).

use std::sync::Arc;
use std::time::Duration;

use eos_sandbox_port::SandboxTransport;
use eos_tools::ports::Sealed;
use eos_tools::{RunningBackgroundTasks, WorkflowControlPort};
use eos_types::AgentRunId;
use tokio::sync::Mutex;

use super::lanes::CommandSessionLane;
use super::notifications::BackgroundNotificationEmitter;
use super::supervisor::BackgroundTaskSupervisor;
use crate::notifications::NotificationService;
use crate::runtime::AgentRunControlFactory;
use crate::EngineRunHandles;

/// The shared per-run background runtime. Held behind one `Arc` so the whole
/// handle clones cheaply; the contained [`CommandSessionLane`] owns the
/// command-completion heartbeat (RAII), so the heartbeat is aborted when the last
/// handle clone drops.
pub(super) struct BackgroundSupervisorRuntime {
    /// The agent run that owns this supervisor (`== caller_id` for daemon calls).
    pub(super) owner_agent_run_id: AgentRunId,
    /// Subagent + workflow ledger, lock-coupled (spec §8.5).
    pub(super) inner: Arc<Mutex<BackgroundTaskSupervisor>>,
    /// Command-session lane sibling: own interior lock + heartbeat (spec §9.3).
    pub(super) commands: CommandSessionLane,
    /// Engine run handles the subagent driver needs.
    pub(super) handles: EngineRunHandles,
    /// Request-scoped factory clone, so `spawn` mints each subagent its own
    /// ephemeral control (spec §8.1/§11.3). Value capability only — no live
    /// control is retained, so there is no reference cycle.
    pub(super) control_factory: AgentRunControlFactory,
    /// This run's background-completion emitter (its own notifier).
    pub(super) notifications: BackgroundNotificationEmitter,
}

/// The per-agent-run background supervisor handle.
#[derive(Clone)]
pub struct BackgroundSupervisorHandle {
    runtime: Arc<BackgroundSupervisorRuntime>,
}

impl std::fmt::Debug for BackgroundSupervisorHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BackgroundSupervisorHandle")
            .field("owner_agent_run_id", &self.runtime.owner_agent_run_id)
            .finish_non_exhaustive()
    }
}

impl BackgroundSupervisorHandle {
    /// Create the per-agent-run supervisor and spawn its command-completion
    /// heartbeat against `notifications` (this run's own queue). Must be called
    /// within a Tokio runtime.
    #[must_use]
    pub fn new(
        owner_agent_run_id: AgentRunId,
        handles: EngineRunHandles,
        transport: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
        notifications: NotificationService,
        control_factory: AgentRunControlFactory,
    ) -> Self {
        let emitter = BackgroundNotificationEmitter::new(notifications);
        let commands = CommandSessionLane::new(
            owner_agent_run_id.clone(),
            emitter.clone(),
            transport,
            completion_poll_interval,
        );
        Self {
            runtime: Arc::new(BackgroundSupervisorRuntime {
                owner_agent_run_id,
                inner: Arc::new(Mutex::new(BackgroundTaskSupervisor::new())),
                commands,
                handles,
                control_factory,
                notifications: emitter,
            }),
        }
    }

    /// The agent run that owns this supervisor.
    #[must_use]
    pub fn owner_agent_run_id(&self) -> &AgentRunId {
        &self.runtime.owner_agent_run_id
    }

    /// The shared subagent+workflow ledger (for the subagent driver settle path).
    #[must_use]
    pub fn inner(&self) -> Arc<Mutex<BackgroundTaskSupervisor>> {
        self.runtime.inner.clone()
    }

    /// The command-session lane (register / recover / cancel-all surface).
    pub(super) fn commands(&self) -> &CommandSessionLane {
        &self.runtime.commands
    }

    /// The engine run handles the subagent driver needs.
    pub(super) fn handles(&self) -> &EngineRunHandles {
        &self.runtime.handles
    }

    /// The control factory used to mint each subagent its own ephemeral control.
    pub(super) fn control_factory(&self) -> &AgentRunControlFactory {
        &self.runtime.control_factory
    }

    /// This agent run's background-completion emitter (its own notifier).
    pub(super) fn notifications(&self) -> &BackgroundNotificationEmitter {
        &self.runtime.notifications
    }

    /// This run's in-flight background report (Running-only) across all three lanes.
    pub async fn running_background_tasks(&self) -> RunningBackgroundTasks {
        let (subagents, workflows) = {
            let guard = self.runtime.inner.lock().await;
            (
                guard.subagents.count_running(),
                guard.workflows.count_running(),
            )
        };
        let command_sessions = self.runtime.commands.count_running().await;
        RunningBackgroundTasks {
            total: subagents + workflows + command_sessions,
            subagents,
            workflows,
            command_sessions,
        }
    }

    /// Settle this run's in-flight subagents (`Cancelled` + abort) and return the
    /// post-cancel report — the terminal/exit prehook entry point.
    pub async fn cancel_subagents(&self, reason: &str) -> RunningBackgroundTasks {
        self.runtime.inner.lock().await.subagents.cancel_all(reason);
        self.running_background_tasks().await
    }

    /// Tear down all background work owned by this agent run (spec §8.2): settle +
    /// abort subagents, cancel delegated workflows through the optional
    /// workflow-control port (a missing port still settles the in-memory record),
    /// and cancel all command sessions in one per-caller daemon RPC (§9.3).
    pub async fn teardown(
        &self,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
        reason: &str,
    ) -> RunningBackgroundTasks {
        let workflows = {
            let mut guard = self.runtime.inner.lock().await;
            guard.subagents.cancel_all(reason);
            guard.workflows.running_ids()
        };
        for workflow_task_id in workflows {
            if let Some(control) = &workflow_control {
                if let Err(err) = control.cancel(&workflow_task_id, reason).await {
                    tracing::warn!(
                        error = %err,
                        workflow_task_id = workflow_task_id.as_str(),
                        "background workflow parent-exit cancellation failed"
                    );
                }
            }
            self.runtime
                .inner
                .lock()
                .await
                .workflows
                .cancel_record(&workflow_task_id);
        }
        self.runtime
            .commands
            .cancel_all_command_sessions(reason)
            .await;
        self.running_background_tasks().await
    }
}

impl Sealed for BackgroundSupervisorHandle {}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use std::sync::Mutex as StdMutex;

    use async_trait::async_trait;
    use eos_agent_def::AgentRegistry;
    use eos_audit::NoopAuditSink;
    use eos_llm_client::{LlmClient, LlmRequest, LlmStream, ProviderError};
    use eos_sandbox_port::{DaemonOp, SandboxPortError};
    use eos_skills::SkillRegistry;
    use eos_state::{
        AgentRun, AgentRunStore, CoreError, Sealed as StateSealed, TaskId, UtcDateTime,
    };
    use eos_tools::{
        OutstandingWorkflow, SandboxToolService, SkillToolService, StartedWorkflowHandle,
        ToolConfigSet, ToolError,
    };
    use eos_types::{JsonObject, SandboxId, WorkflowId, WorkflowSessionId};
    use serde_json::json;

    use crate::{BackgroundSupervisorFactory, ForegroundExecutorFactory};

    use super::*;

    fn test_control_factory(transport: Arc<dyn SandboxTransport>) -> AgentRunControlFactory {
        AgentRunControlFactory::new(
            ForegroundExecutorFactory,
            BackgroundSupervisorFactory::new(
                handles(transport.clone()),
                transport,
                std::time::Duration::from_secs(3600),
            ),
        )
    }

    fn test_handle(
        owner: &str,
        transport: Arc<dyn SandboxTransport>,
    ) -> BackgroundSupervisorHandle {
        BackgroundSupervisorHandle::new(
            owner.parse().expect("agent run id"),
            handles(transport.clone()),
            transport.clone(),
            std::time::Duration::from_secs(3600),
            NotificationService::new(),
            test_control_factory(transport),
        )
    }

    #[derive(Debug)]
    struct NoopLlmClient;

    #[async_trait]
    impl LlmClient for NoopLlmClient {
        async fn stream_message(&self, _request: LlmRequest) -> Result<LlmStream, ProviderError> {
            Ok(Box::pin(futures::stream::empty()))
        }
    }

    #[derive(Debug, Default)]
    struct NoopAgentRunStore;

    impl StateSealed for NoopAgentRunStore {}

    #[async_trait]
    impl AgentRunStore for NoopAgentRunStore {
        async fn create_run(
            &self,
            agent_run_id: &AgentRunId,
            task_id: &TaskId,
            agent_name: &str,
            initial_messages: Option<&[JsonObject]>,
        ) -> Result<AgentRun, CoreError> {
            Ok(AgentRun {
                id: agent_run_id.clone(),
                task_id: task_id.clone(),
                initial_messages: initial_messages.map(<[_]>::to_vec),
                agent_name: agent_name.to_owned(),
                message_history: None,
                terminal_tool_result: None,
                token_count: 0,
                error: None,
                created_at: UtcDateTime::now(),
                finished_at: None,
            })
        }

        async fn finish_run(
            &self,
            _agent_run_id: &AgentRunId,
            _message_history: Option<&[JsonObject]>,
            _terminal_tool_result: Option<&JsonObject>,
            _token_count: i64,
            _error: Option<&str>,
        ) -> Result<Option<AgentRun>, CoreError> {
            Ok(None)
        }

        async fn get(&self, _agent_run_id: &AgentRunId) -> Result<Option<AgentRun>, CoreError> {
            Ok(None)
        }

        async fn get_for_task(&self, _task_id: &TaskId) -> Result<Option<AgentRun>, CoreError> {
            Ok(None)
        }
    }

    #[derive(Debug, Default)]
    struct RecordingTransport {
        calls: StdMutex<Vec<(SandboxId, DaemonOp, JsonObject)>>,
    }

    impl RecordingTransport {
        fn calls(&self) -> Vec<(SandboxId, DaemonOp, JsonObject)> {
            self.calls.lock().expect("calls lock").clone()
        }
    }

    #[async_trait]
    impl SandboxTransport for RecordingTransport {
        async fn call(
            &self,
            sandbox_id: &SandboxId,
            op: DaemonOp,
            payload: JsonObject,
            _timeout_s: u32,
        ) -> Result<JsonObject, SandboxPortError> {
            self.calls
                .lock()
                .expect("calls lock")
                .push((sandbox_id.clone(), op, payload));
            Ok(json!({
                "success": true,
                "cancelled_command_sessions": 1,
                "isolated_exited": false
            })
            .as_object()
            .expect("object")
            .clone())
        }
    }

    #[derive(Debug, Default)]
    struct RecordingWorkflowControl {
        cancels: StdMutex<Vec<(WorkflowSessionId, String)>>,
    }

    impl RecordingWorkflowControl {
        fn cancels(&self) -> Vec<(WorkflowSessionId, String)> {
            self.cancels.lock().expect("workflow lock").clone()
        }
    }

    impl eos_tools::ports::Sealed for RecordingWorkflowControl {}

    #[async_trait]
    impl WorkflowControlPort for RecordingWorkflowControl {
        async fn start(
            &self,
            _parent_task_id: &TaskId,
            _agent_run_id: &AgentRunId,
            _workflow_goal: &str,
        ) -> Result<StartedWorkflowHandle, ToolError> {
            Ok(StartedWorkflowHandle {
                workflow_id: WorkflowId::new_v4(),
                workflow_task_id: "wf_started".parse().expect("workflow handle"),
            })
        }

        async fn status(
            &self,
            _workflow_id: &WorkflowId,
            _workflow_task_id: Option<&WorkflowSessionId>,
        ) -> Result<String, ToolError> {
            Ok("running".to_owned())
        }

        async fn cancel(
            &self,
            workflow_task_id: &WorkflowSessionId,
            reason: &str,
        ) -> Result<String, ToolError> {
            self.cancels
                .lock()
                .expect("workflow lock")
                .push((workflow_task_id.clone(), reason.to_owned()));
            Ok("cancelled".to_owned())
        }

        async fn find_outstanding(
            &self,
            _parent_task_id: &TaskId,
            _agent_run_id: &AgentRunId,
        ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
            Ok(Vec::new())
        }

        async fn workflow_depth(&self, _workflow_id: &WorkflowId) -> Result<u32, ToolError> {
            Ok(1)
        }
    }

    fn handles(transport: Arc<dyn SandboxTransport>) -> EngineRunHandles {
        EngineRunHandles {
            agent_run_store: Arc::new(NoopAgentRunStore),
            llm_client: Arc::new(NoopLlmClient),
            event_source_factory: None,
            agent_registry: Arc::new(Vec::new().into_iter().collect::<AgentRegistry>()),
            tool_config: Arc::new(
                ToolConfigSet::load_from_dir(&eos_testkit::test_tools_root()).expect("tool config"),
            ),
            sandbox_service: SandboxToolService::new(transport),
            root_submission: None,
            skill_service: SkillToolService::new(Arc::new(SkillRegistry::new())),
            tool_registry_extender: None,
            audit: Arc::new(NoopAuditSink),
            message_records: None,
            workspace_root: "/tmp".to_owned(),
        }
    }

    // Spec §8.2/§9.3: teardown cancels delegated workflows through the control port
    // and cancels command sessions with one per-caller daemon RPC (not per-session).
    #[tokio::test]
    async fn teardown_cancels_workflows_and_command_sessions() {
        let transport = Arc::new(RecordingTransport::default());
        let handle = test_handle("agent-a", transport.clone());
        let workflow = StartedWorkflowHandle {
            workflow_id: WorkflowId::new_v4(),
            workflow_task_id: "wf_1".parse().expect("workflow handle"),
        };
        handle.inner().lock().await.workflows.register(&workflow);
        handle
            .commands()
            .register(
                &"cmd_1".parse().expect("command id"),
                &"sandbox-a".parse().expect("sandbox id"),
                "cargo test",
            )
            .await;
        let workflow_control = Arc::new(RecordingWorkflowControl::default());

        let report = handle
            .teardown(
                Some(workflow_control.clone()),
                "parent submitted its terminal",
            )
            .await;

        assert_eq!(report.total, 0);
        assert_eq!(workflow_control.cancels().len(), 1);
        assert_eq!(workflow_control.cancels()[0].0, workflow.workflow_task_id);
        let calls = transport.calls();
        assert_eq!(calls.len(), 1, "one per-caller cancel, not one per session");
        assert_eq!(calls[0].1, DaemonOp::CancelWorkspaceRunsByCaller);
        assert_eq!(calls[0].2["caller_id"], json!("agent-a"));
    }

    #[tokio::test]
    async fn teardown_settles_workflow_without_workflow_control() {
        let transport = Arc::new(RecordingTransport::default());
        let handle = test_handle("agent-a", transport);
        let workflow = StartedWorkflowHandle {
            workflow_id: WorkflowId::new_v4(),
            workflow_task_id: "wf_2".parse().expect("workflow handle"),
        };
        handle.inner().lock().await.workflows.register(&workflow);

        let report = handle.teardown(None, "parent exited").await;

        assert_eq!(report.workflows, 0);
        assert_eq!(report.total, 0);
    }
}

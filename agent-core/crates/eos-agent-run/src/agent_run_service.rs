//! Launcher-backed agent-run lifecycle service.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentLoopCompletion, AgentLoopLauncher, AgentLoopMessage, AgentLoopOutcome,
    AgentLoopOutcomeKind, AgentName as DefinitionAgentName, AgentRegistry, AgentRunApi,
    AgentRunError, AgentRunId, AgentRunOutcome, AgentRunStatus, AgentRunStore, AgentType,
    CreatedTaskAgentRun, Message, ParentedAgentRunKind, SpawnAgentRequest, SpawnAgentTarget,
    TaskAgentRunKind, TaskAgentRunStore, TaskStatus,
};

use crate::active_agent_runs::ActiveAgentRunRegistry;
use crate::agent_loop_request::build_start_agent_loop_request;
use crate::agent_run_persistence::{
    completion_from_agent_run, create_compat_agent_run, finish_compat_agent_run,
    finish_compat_agent_run_cancelled,
};

type RuntimeStateRecorder = Arc<
    dyn Fn(&SpawnAgentRequest, &CreatedTaskAgentRun) -> Result<(), AgentRunError> + Send + Sync,
>;
type RuntimeStateRemover = Arc<dyn Fn(&AgentRunId) + Send + Sync>;

/// Agent-run lifecycle service.
#[derive(Clone)]
pub struct AgentRunService {
    agent_registry: Arc<AgentRegistry>,
    agent_loop_launcher: Arc<dyn AgentLoopLauncher>,
    agent_run_store: Arc<dyn AgentRunStore>,
    task_agent_run_store: Arc<dyn TaskAgentRunStore>,
    active_agent_runs: ActiveAgentRunRegistry,
    runtime_state_recorder: Option<RuntimeStateRecorder>,
    runtime_state_remover: Option<RuntimeStateRemover>,
}

impl std::fmt::Debug for AgentRunService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunService").finish_non_exhaustive()
    }
}

impl AgentRunService {
    /// Build a runner service from injected trait contracts.
    #[must_use]
    pub fn new(
        agent_registry: Arc<AgentRegistry>,
        agent_loop_launcher: Arc<dyn AgentLoopLauncher>,
        agent_run_store: Arc<dyn AgentRunStore>,
        task_agent_run_store: Arc<dyn TaskAgentRunStore>,
    ) -> Self {
        Self {
            agent_registry,
            agent_loop_launcher,
            agent_run_store,
            task_agent_run_store,
            active_agent_runs: ActiveAgentRunRegistry::new(),
            runtime_state_recorder: None,
            runtime_state_remover: None,
        }
    }

    /// Attach runtime-only state hooks used by the production composition layer.
    ///
    /// The runner still owns agent-run lifecycle state; these hooks only record
    /// and remove mutable execution facts such as workspace/isolation metadata.
    #[must_use]
    pub fn with_runtime_state_hooks<Record, Remove>(
        mut self,
        record: Record,
        remove: Remove,
    ) -> Self
    where
        Record: Fn(&SpawnAgentRequest, &CreatedTaskAgentRun) -> Result<(), AgentRunError>
            + Send
            + Sync
            + 'static,
        Remove: Fn(&AgentRunId) + Send + Sync + 'static,
    {
        self.runtime_state_recorder = Some(Arc::new(record));
        self.runtime_state_remover = Some(Arc::new(remove));
        self
    }

    async fn finalize_agent_run_from_agent_loop_outcome(
        &self,
        agent_run_id: AgentRunId,
        outcome: AgentLoopOutcome,
    ) -> AgentRunOutcome {
        let agent_outcome = agent_run_outcome_from_loop(agent_run_id.clone(), outcome);
        let error = agent_outcome.error.as_deref();
        let finish = finish_compat_agent_run(
            &*self.agent_run_store,
            &agent_run_id,
            agent_outcome.submission_payload.as_ref(),
            agent_outcome.token_count,
            error,
        )
        .await;
        let finish_lineage = self
            .finish_task_agent_run(
                &agent_run_id,
                task_status_for_agent_status(agent_outcome.status),
                agent_outcome.submission_payload.as_ref(),
                agent_outcome.token_count.unwrap_or_default(),
                error,
            )
            .await;
        match finish.and(finish_lineage) {
            Ok(()) => agent_outcome,
            Err(err) => AgentRunOutcome {
                agent_run_id,
                status: AgentRunStatus::Failed,
                submission_payload: None,
                message_history: Vec::new(),
                token_count: None,
                error: Some(err.to_string()),
            },
        }
    }

    async fn finalize_agent_run_from_dropped_agent_loop_sender(
        &self,
        agent_run_id: AgentRunId,
    ) -> AgentRunOutcome {
        let error = "agent loop outcome sender dropped".to_owned();
        let _ignored = finish_compat_agent_run(
            &*self.agent_run_store,
            &agent_run_id,
            None,
            None,
            Some(&error),
        )
        .await;
        let _ignored = self
            .finish_task_agent_run(&agent_run_id, TaskStatus::Failed, None, 0, Some(&error))
            .await;
        AgentRunOutcome {
            agent_run_id,
            status: AgentRunStatus::Failed,
            submission_payload: None,
            message_history: Vec::new(),
            token_count: None,
            error: Some(error),
        }
    }

    async fn create_task_agent_run(
        &self,
        request: &SpawnAgentRequest,
        agent_run_id: &AgentRunId,
        agent_name: &DefinitionAgentName,
    ) -> Result<CreatedTaskAgentRun, AgentRunError> {
        match &request.target {
            SpawnAgentTarget::Root { request_id } => {
                self.task_agent_run_store
                    .create_root_task_agent_run(request_id, agent_run_id, agent_name)
                    .await
            }
            SpawnAgentTarget::Workflow {
                request_id,
                workflow,
                workflow_node_id,
            } => {
                self.task_agent_run_store
                    .create_workflow_task_agent_run(
                        request_id,
                        agent_run_id,
                        workflow,
                        workflow_node_id,
                        agent_name,
                    )
                    .await
            }
            SpawnAgentTarget::Subagent { parent } => {
                self.task_agent_run_store
                    .create_parented_task_agent_run(
                        agent_run_id,
                        parent,
                        ParentedAgentRunKind::Subagent,
                        request.tool_use_id.as_ref(),
                        agent_name,
                    )
                    .await
            }
            SpawnAgentTarget::Advisor { parent } => {
                self.task_agent_run_store
                    .create_parented_task_agent_run(
                        agent_run_id,
                        parent,
                        ParentedAgentRunKind::Advisor,
                        request.tool_use_id.as_ref(),
                        agent_name,
                    )
                    .await
            }
        }
        .map_err(|err| AgentRunError::Internal(err.to_string()))
    }

    async fn finish_task_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        status: TaskStatus,
        terminal_payload: Option<&eos_types::JsonObject>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<(), AgentRunError> {
        let Some(index) = self
            .task_agent_run_store
            .record_index_for_agent_run(agent_run_id)
            .await
            .map_err(|err| AgentRunError::Internal(err.to_string()))?
        else {
            return Err(AgentRunError::Internal(format!(
                "task-agent-run row not found for {}",
                agent_run_id.as_str()
            )));
        };
        let updated = match index.kind {
            TaskAgentRunKind::Root | TaskAgentRunKind::Workflow { .. } => self
                .task_agent_run_store
                .finish_task_run(agent_run_id, status, terminal_payload, token_count, error)
                .await
                .map(|row| row.is_some()),
            TaskAgentRunKind::Parented { .. } => self
                .task_agent_run_store
                .finish_parented_run(agent_run_id, status, terminal_payload, token_count, error)
                .await
                .map(|row| row.is_some()),
        }
        .map_err(|err| AgentRunError::Internal(err.to_string()))?;
        if updated {
            Ok(())
        } else {
            Err(AgentRunError::Internal(format!(
                "task-agent-run row not updated for {}",
                agent_run_id.as_str()
            )))
        }
    }
}

#[async_trait]
impl AgentRunApi for AgentRunService {
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        if request.initial_messages.is_empty() {
            return Err(AgentRunError::Internal(
                "agent launch requires at least one initial message".to_owned(),
            ));
        }
        let requested_agent_name = request.agent_name.as_str().to_owned();
        let agent_name = DefinitionAgentName::new(request.agent_name.as_str())
            .map_err(|_| AgentRunError::AgentNotRegistered(requested_agent_name.clone()))?;
        let Some(agent_def) = self.agent_registry.get(&agent_name) else {
            return Err(AgentRunError::AgentNotRegistered(requested_agent_name));
        };
        let expected = expected_agent_type(&request.target.task_agent_run_kind());
        if agent_def.agent_type != expected {
            return Err(AgentRunError::WrongAgentType {
                agent_name: requested_agent_name,
                expected: agent_type_value(expected),
                actual: agent_type_value(agent_def.agent_type),
            });
        }

        let agent_def = (**agent_def).clone();
        let agent_run_id = AgentRunId::new_v4();
        let created_run = self
            .create_task_agent_run(&request, &agent_run_id, &agent_name)
            .await?;
        let compat_task_id = match &request.target {
            SpawnAgentTarget::Root { .. } | SpawnAgentTarget::Workflow { .. } => {
                Some(&created_run.task_id)
            }
            SpawnAgentTarget::Subagent { .. } | SpawnAgentTarget::Advisor { .. } => None,
        };
        create_compat_agent_run(
            &*self.agent_run_store,
            compat_task_id,
            &agent_run_id,
            agent_def.name.as_str(),
        )
        .await?;
        let record_target = created_run.record_target.clone();
        if let Some(record_runtime_state) = &self.runtime_state_recorder {
            record_runtime_state(&request, &created_run)?;
        }
        let start_request = build_start_agent_loop_request(&agent_def, request, record_target);
        let agent_run_api: Arc<dyn AgentRunApi> = Arc::new(self.clone());
        let started = self
            .agent_loop_launcher
            .start_agent_loop(start_request, agent_run_api);

        self.active_agent_runs
            .insert(agent_run_id.clone(), started.cancellation)
            .await;
        let service = self.clone();
        let forward_agent_run_id = agent_run_id.clone();
        tokio::spawn(async move {
            forward_agent_loop_outcome(service, forward_agent_run_id, started.completion).await;
        });

        Ok(agent_run_id)
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        if let Some(outcome) = self.poll_agent_run_outcome(agent_run_id).await? {
            return Ok(outcome);
        }
        let mut rx = self.active_agent_runs.subscribe(agent_run_id).await?;
        loop {
            if let Some(outcome) = rx.borrow().clone() {
                return Ok(outcome);
            }
            rx.changed()
                .await
                .map_err(|_| AgentRunError::CompletionChannelClosed(agent_run_id.clone()))?;
        }
    }

    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        if let Some(outcome) = self.active_agent_runs.current_outcome(agent_run_id).await {
            return Ok(Some(outcome));
        }
        let Some(run) = self
            .agent_run_store
            .get(agent_run_id)
            .await
            .map_err(|err| AgentRunError::Internal(err.to_string()))?
        else {
            return Ok(None);
        };
        Ok(completion_from_agent_run(agent_run_id, &run))
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError> {
        let completion = self.active_agent_runs.take(agent_run_id).await;
        if let Some(completion) = &completion {
            completion.cancel(reason);
        }
        finish_compat_agent_run_cancelled(&*self.agent_run_store, agent_run_id, reason).await?;
        let payload = cancelled_task_payload(reason);
        self.finish_task_agent_run(
            agent_run_id,
            TaskStatus::Cancelled,
            Some(&payload),
            0,
            Some(reason),
        )
        .await?;
        let outcome = AgentRunOutcome {
            agent_run_id: agent_run_id.clone(),
            status: AgentRunStatus::Cancelled,
            submission_payload: None,
            message_history: Vec::new(),
            token_count: None,
            error: Some(reason.to_owned()),
        };
        if let Some(completion) = completion {
            completion.publish(outcome);
        }
        if let Some(remove_runtime_state) = &self.runtime_state_remover {
            remove_runtime_state(agent_run_id);
        }
        Ok(())
    }
}

async fn forward_agent_loop_outcome(
    service: AgentRunService,
    agent_run_id: AgentRunId,
    loop_completion: AgentLoopCompletion,
) {
    let received = loop_completion.wait().await;
    let Some(completion) = service.active_agent_runs.take(&agent_run_id).await else {
        return;
    };
    let outcome = match received {
        Some(outcome) => {
            service
                .finalize_agent_run_from_agent_loop_outcome(agent_run_id.clone(), outcome)
                .await
        }
        None => {
            service
                .finalize_agent_run_from_dropped_agent_loop_sender(agent_run_id.clone())
                .await
        }
    };
    completion.publish(outcome);
    if let Some(remove_runtime_state) = &service.runtime_state_remover {
        remove_runtime_state(&agent_run_id);
    }
}

fn agent_run_outcome_from_loop(
    agent_run_id: AgentRunId,
    outcome: AgentLoopOutcome,
) -> AgentRunOutcome {
    let message_history = loop_messages_to_llm_messages(outcome.final_conversation_messages);
    let total_token_count = outcome.total_token_count;
    match outcome.kind {
        AgentLoopOutcomeKind::TerminalToolSubmitted { submission_payload } => AgentRunOutcome {
            agent_run_id,
            status: AgentRunStatus::Completed,
            submission_payload: Some(submission_payload),
            message_history,
            token_count: total_token_count,
            error: None,
        },
        AgentLoopOutcomeKind::LoopFailed { error_summary } => AgentRunOutcome {
            agent_run_id,
            status: AgentRunStatus::Failed,
            submission_payload: None,
            message_history,
            token_count: total_token_count,
            error: Some(error_summary),
        },
    }
}

fn loop_messages_to_llm_messages(messages: Vec<AgentLoopMessage>) -> Vec<Message> {
    messages
        .into_iter()
        .filter_map(|message| match message {
            AgentLoopMessage::SystemPrompt(_) => None,
            AgentLoopMessage::UserMessage(message)
            | AgentLoopMessage::AssistantMessage(message) => Some(message),
        })
        .collect()
}

const fn task_status_for_agent_status(status: AgentRunStatus) -> TaskStatus {
    match status {
        AgentRunStatus::Completed => TaskStatus::Done,
        AgentRunStatus::Failed => TaskStatus::Failed,
        AgentRunStatus::Cancelled => TaskStatus::Cancelled,
    }
}

fn cancelled_task_payload(reason: &str) -> eos_types::JsonObject {
    let mut payload = eos_types::JsonObject::new();
    payload.insert("fail_reason".to_owned(), serde_json::json!("cancelled"));
    payload.insert("reason".to_owned(), serde_json::json!(reason));
    payload
}

const fn agent_type_value(agent_type: AgentType) -> &'static str {
    match agent_type {
        AgentType::Agent => "agent",
        AgentType::Subagent => "subagent",
        AgentType::Advisor => "advisor",
    }
}

const fn expected_agent_type(run_kind: &TaskAgentRunKind) -> AgentType {
    match run_kind {
        TaskAgentRunKind::Root | TaskAgentRunKind::Workflow { .. } => AgentType::Agent,
        TaskAgentRunKind::Parented {
            kind: ParentedAgentRunKind::Subagent,
            ..
        } => AgentType::Subagent,
        TaskAgentRunKind::Parented {
            kind: ParentedAgentRunKind::Advisor,
            ..
        } => AgentType::Advisor,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::num::NonZeroU32;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Mutex as StdMutex, MutexGuard};
    use tokio::sync::oneshot;
    use tokio::time::{timeout, Duration};

    use eos_types::{
        format_record_dir, root_task_id, AgentDefinition, AgentLoopCancellation, AgentName,
        AgentRegistryBuilder, AgentRun, AgentRunRecordIndex, AgentRunRecordTarget, ContentBlock,
        CoreError, JsonObject, ParentAgentRunAnchor, ParentedRun, RequestId,
        StartAgentLoopRequest, StartedAgentLoop, TaskAgentRunKind, TaskExecutionIndex, TaskId,
        TaskRole, TaskRun, ToolUseId, UtcDateTime, WorkflowCoordinates, WorkflowNodeId,
    };

    #[test]
    fn task_agent_run_kind_declares_required_agent_type() {
        let parent_agent_run_id = AgentRunId::new_v4();
        assert_eq!(
            expected_agent_type(&TaskAgentRunKind::Root),
            AgentType::Agent
        );
        assert_eq!(
            expected_agent_type(&TaskAgentRunKind::Parented {
                parent_agent_run_id: parent_agent_run_id.clone(),
                kind: ParentedAgentRunKind::Subagent,
            }),
            AgentType::Subagent
        );
        assert_eq!(
            expected_agent_type(&TaskAgentRunKind::Parented {
                parent_agent_run_id,
                kind: ParentedAgentRunKind::Advisor,
            }),
            AgentType::Advisor
        );
    }

    #[tokio::test]
    async fn engine_completion_finalizes_once_and_publishes_waiters() {
        let harness = ServiceHarness::new();
        let run_id = harness
            .service
            .spawn_agent(root_spawn_request())
            .await
            .expect("spawn succeeds");
        let waiter = {
            let service = harness.service.clone();
            let run_id = run_id.clone();
            tokio::spawn(async move { service.wait_for_agent_outcome(&run_id).await })
        };

        harness.launcher.complete(successful_loop_outcome());

        let outcome = timeout(Duration::from_secs(1), waiter)
            .await
            .expect("waiter completes")
            .expect("waiter task joins")
            .expect("waiter returns outcome");
        assert_eq!(outcome.status, AgentRunStatus::Completed);
        assert_eq!(harness.agent_run_store.finish_count(), 1);
        assert_eq!(harness.task_agent_run_store.finish_count(), 1);
        assert_eq!(
            harness
                .service
                .poll_agent_run_outcome(&run_id)
                .await
                .expect("poll succeeds")
                .expect("outcome is persisted")
                .status,
            AgentRunStatus::Completed
        );
    }

    #[tokio::test]
    async fn cancellation_before_engine_completion_finalizes_once() {
        let harness = ServiceHarness::new();
        let run_id = harness
            .service
            .spawn_agent(root_spawn_request())
            .await
            .expect("spawn succeeds");
        let waiter = {
            let service = harness.service.clone();
            let run_id = run_id.clone();
            tokio::spawn(async move { service.wait_for_agent_outcome(&run_id).await })
        };

        harness
            .service
            .cancel_agent_run(&run_id, "caller cancelled")
            .await
            .expect("cancel succeeds");

        let outcome = timeout(Duration::from_secs(1), waiter)
            .await
            .expect("waiter completes")
            .expect("waiter task joins")
            .expect("waiter returns outcome");
        assert_eq!(outcome.status, AgentRunStatus::Cancelled);
        assert_eq!(
            harness.launcher.cancellation_reason(),
            Some("caller cancelled".to_owned())
        );
        assert_eq!(harness.agent_run_store.finish_count(), 1);
        assert_eq!(harness.task_agent_run_store.finish_count(), 1);

        harness.launcher.complete(successful_loop_outcome());
        tokio::time::sleep(Duration::from_millis(20)).await;

        assert_eq!(harness.agent_run_store.finish_count(), 1);
        assert_eq!(harness.task_agent_run_store.finish_count(), 1);
    }

    struct ServiceHarness {
        service: AgentRunService,
        launcher: Arc<ControlledLauncher>,
        agent_run_store: Arc<FakeAgentRunStore>,
        task_agent_run_store: Arc<FakeTaskAgentRunStore>,
    }

    impl ServiceHarness {
        fn new() -> Self {
            let mut registry = AgentRegistryBuilder::new();
            registry.add(root_agent_definition());
            let launcher = Arc::new(ControlledLauncher::default());
            let agent_run_store = Arc::new(FakeAgentRunStore::default());
            let task_agent_run_store = Arc::new(FakeTaskAgentRunStore::default());
            let service = AgentRunService::new(
                Arc::new(registry.build()),
                launcher.clone(),
                agent_run_store.clone(),
                task_agent_run_store.clone(),
            );
            Self {
                service,
                launcher,
                agent_run_store,
                task_agent_run_store,
            }
        }
    }

    fn root_agent_definition() -> AgentDefinition {
        AgentDefinition {
            name: AgentName::new("root").expect("valid agent name"),
            description: "root test agent".to_owned(),
            system_prompt: Some("system".to_owned()),
            model: Some("test-model".to_owned()),
            tool_call_limit: NonZeroU32::new(4).expect("non-zero"),
            agent_type: AgentType::Agent,
            allowed_tools: Vec::new(),
            terminals: Vec::new(),
            notification_triggers: Vec::new(),
            skill: None,
            context_recipe: None,
        }
    }

    fn root_spawn_request() -> SpawnAgentRequest {
        SpawnAgentRequest {
            agent_name: AgentName::new("root").expect("valid agent name"),
            initial_messages: vec![Message::from_user_text("start")],
            target: SpawnAgentTarget::Root {
                request_id: RequestId::new_v4(),
            },
            tool_use_id: None,
            sandbox_id: None,
            workspace_root: "/workspace".to_owned(),
            is_isolated_workspace_mode: false,
        }
    }

    fn successful_loop_outcome() -> AgentLoopOutcome {
        let mut payload = JsonObject::new();
        payload.insert("summary".to_owned(), serde_json::json!("done"));
        AgentLoopOutcome {
            kind: AgentLoopOutcomeKind::TerminalToolSubmitted {
                submission_payload: payload,
            },
            final_conversation_messages: vec![
                AgentLoopMessage::UserMessage(Message::from_user_text("start")),
                AgentLoopMessage::AssistantMessage(Message {
                    role: eos_types::MessageRole::Assistant,
                    content: vec![ContentBlock::Text {
                        text: "done".to_owned(),
                    }],
                }),
            ],
            total_token_count: Some(12),
        }
    }

    #[derive(Default)]
    struct ControlledLauncher {
        completion_sender: StdMutex<Option<oneshot::Sender<Option<AgentLoopOutcome>>>>,
        cancellation: Arc<TestCancellation>,
    }

    impl ControlledLauncher {
        fn complete(&self, outcome: AgentLoopOutcome) {
            let Some(sender) = lock(&self.completion_sender).take() else {
                panic!("agent loop was not started");
            };
            let _ignored = sender.send(Some(outcome));
        }

        fn cancellation_reason(&self) -> Option<String> {
            lock(&self.cancellation.reason).clone()
        }
    }

    impl AgentLoopLauncher for ControlledLauncher {
        fn start_agent_loop(
            &self,
            _request: StartAgentLoopRequest,
            _agent_run_api: Arc<dyn AgentRunApi>,
        ) -> StartedAgentLoop {
            let (sender, receiver) = oneshot::channel();
            *lock(&self.completion_sender) = Some(sender);
            StartedAgentLoop {
                completion: AgentLoopCompletion::new(async move { receiver.await.ok().flatten() }),
                cancellation: self.cancellation.clone(),
            }
        }
    }

    #[derive(Debug, Default)]
    struct TestCancellation {
        reason: StdMutex<Option<String>>,
    }

    impl AgentLoopCancellation for TestCancellation {
        fn cancel(&self, reason: &str) {
            let mut stored = lock(&self.reason);
            if stored.is_none() {
                *stored = Some(reason.to_owned());
            }
        }
    }

    #[derive(Default)]
    struct FakeAgentRunStore {
        runs: StdMutex<HashMap<AgentRunId, AgentRun>>,
        finish_count: AtomicUsize,
    }

    impl FakeAgentRunStore {
        fn finish_count(&self) -> usize {
            self.finish_count.load(Ordering::SeqCst)
        }
    }

    impl eos_types::Sealed for FakeAgentRunStore {}

    #[async_trait]
    impl AgentRunStore for FakeAgentRunStore {
        async fn create_run(
            &self,
            agent_run_id: &AgentRunId,
            task_id: Option<&TaskId>,
            agent_name: &str,
        ) -> Result<AgentRun, CoreError> {
            let run = AgentRun {
                id: agent_run_id.clone(),
                task_id: task_id.cloned(),
                agent_name: agent_name.to_owned(),
                terminal_payload: None,
                token_count: 0,
                error: None,
                created_at: UtcDateTime::now(),
                finished_at: None,
            };
            lock(&self.runs).insert(agent_run_id.clone(), run.clone());
            Ok(run)
        }

        async fn finish_run(
            &self,
            agent_run_id: &AgentRunId,
            terminal_payload: Option<&JsonObject>,
            token_count: i64,
            error: Option<&str>,
        ) -> Result<Option<AgentRun>, CoreError> {
            self.finish_count.fetch_add(1, Ordering::SeqCst);
            let mut runs = lock(&self.runs);
            let Some(run) = runs.get_mut(agent_run_id) else {
                return Ok(None);
            };
            run.terminal_payload = terminal_payload.cloned();
            run.token_count = token_count;
            run.error = error.map(str::to_owned);
            run.finished_at = Some(UtcDateTime::now());
            Ok(Some(run.clone()))
        }

        async fn get(&self, agent_run_id: &AgentRunId) -> Result<Option<AgentRun>, CoreError> {
            Ok(lock(&self.runs).get(agent_run_id).cloned())
        }

        async fn get_for_task(&self, task_id: &TaskId) -> Result<Option<AgentRun>, CoreError> {
            Ok(lock(&self.runs)
                .values()
                .find(|run| run.task_id.as_ref() == Some(task_id))
                .cloned())
        }
    }

    #[derive(Default)]
    struct FakeTaskAgentRunStore {
        indexes: StdMutex<HashMap<AgentRunId, AgentRunRecordIndex>>,
        finish_count: AtomicUsize,
    }

    impl FakeTaskAgentRunStore {
        fn finish_count(&self) -> usize {
            self.finish_count.load(Ordering::SeqCst)
        }
    }

    impl eos_types::Sealed for FakeTaskAgentRunStore {}

    #[async_trait]
    impl TaskAgentRunStore for FakeTaskAgentRunStore {
        async fn create_root_task_agent_run(
            &self,
            request_id: &RequestId,
            agent_run_id: &AgentRunId,
            _agent_name: &AgentName,
        ) -> Result<CreatedTaskAgentRun, CoreError> {
            let index = AgentRunRecordIndex {
                request_id: request_id.clone(),
                agent_run_id: agent_run_id.clone(),
                task_id: root_task_id(request_id),
                kind: TaskAgentRunKind::Root,
                parent_record_dir: None,
            };
            lock(&self.indexes).insert(agent_run_id.clone(), index.clone());
            Ok(created_from_index(&index))
        }

        async fn create_workflow_task_agent_run(
            &self,
            _request_id: &RequestId,
            _agent_run_id: &AgentRunId,
            _workflow: &WorkflowCoordinates,
            _workflow_node_id: &WorkflowNodeId,
            _agent_name: &AgentName,
        ) -> Result<CreatedTaskAgentRun, CoreError> {
            Err(CoreError::Store("workflow fake not implemented".to_owned()))
        }

        async fn create_parented_task_agent_run(
            &self,
            _agent_run_id: &AgentRunId,
            _parent: &ParentAgentRunAnchor,
            _kind: ParentedAgentRunKind,
            _tool_use_id: Option<&ToolUseId>,
            _agent_name: &AgentName,
        ) -> Result<CreatedTaskAgentRun, CoreError> {
            Err(CoreError::Store("parented fake not implemented".to_owned()))
        }

        async fn finish_task_run(
            &self,
            agent_run_id: &AgentRunId,
            status: TaskStatus,
            terminal_payload: Option<&JsonObject>,
            token_count: i64,
            error: Option<&str>,
        ) -> Result<Option<TaskRun>, CoreError> {
            self.finish_count.fetch_add(1, Ordering::SeqCst);
            let Some(index) = lock(&self.indexes).get(agent_run_id).cloned() else {
                return Ok(None);
            };
            Ok(Some(TaskRun {
                task_id: index.task_id,
                agent_run_id: index.agent_run_id,
                request_id: index.request_id,
                role: TaskRole::Root,
                status,
                workflow_id: None,
                iteration_id: None,
                attempt_id: None,
                agent_name: AgentName::new("root").expect("valid agent name"),
                terminal_payload: terminal_payload.cloned(),
                token_count,
                error: error.map(str::to_owned),
                created_at: UtcDateTime::now(),
                updated_at: UtcDateTime::now(),
                finished_at: Some(UtcDateTime::now()),
            }))
        }

        async fn finish_parented_run(
            &self,
            _agent_run_id: &AgentRunId,
            _status: TaskStatus,
            _terminal_payload: Option<&JsonObject>,
            _token_count: i64,
            _error: Option<&str>,
        ) -> Result<Option<ParentedRun>, CoreError> {
            Err(CoreError::Store("parented fake not implemented".to_owned()))
        }

        async fn record_index_for_agent_run(
            &self,
            agent_run_id: &AgentRunId,
        ) -> Result<Option<AgentRunRecordIndex>, CoreError> {
            Ok(lock(&self.indexes).get(agent_run_id).cloned())
        }

        async fn get_task_run(&self, _task_id: &TaskId) -> Result<Option<TaskRun>, CoreError> {
            Err(CoreError::Store("get task fake not implemented".to_owned()))
        }

        async fn list_parented_runs_for_parent_task(
            &self,
            _parent_task_id: &TaskId,
            _kind: ParentedAgentRunKind,
        ) -> Result<Vec<ParentedRun>, CoreError> {
            Err(CoreError::Store(
                "list parented fake not implemented".to_owned(),
            ))
        }

        async fn task_execution_index(
            &self,
            _task_id: &TaskId,
        ) -> Result<Option<TaskExecutionIndex>, CoreError> {
            Err(CoreError::Store(
                "task execution index fake not implemented".to_owned(),
            ))
        }
    }

    fn created_from_index(index: &AgentRunRecordIndex) -> CreatedTaskAgentRun {
        CreatedTaskAgentRun {
            agent_run_id: index.agent_run_id.clone(),
            task_id: index.task_id.clone(),
            record_target: AgentRunRecordTarget {
                request_id: index.request_id.clone(),
                agent_run_id: index.agent_run_id.clone(),
                task_id: index.task_id.clone(),
                task_agent_run_kind: index.kind.clone(),
                record_dir: format_record_dir(index),
            },
        }
    }

    fn lock<T>(mutex: &StdMutex<T>) -> MutexGuard<'_, T> {
        mutex
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

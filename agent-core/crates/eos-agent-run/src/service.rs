//! Launcher-backed agent-run lifecycle service.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentLoopLauncher, AgentRegistry, AgentRunApi, AgentRunError, AgentRunId, AgentRunOutcome,
    AgentRunStore, SpawnAgentRequest,
};

use crate::active_agent_runs::ActiveAgentRunRegistry;
use crate::{cancellation, completion, spawn};

/// Agent-run lifecycle service.
#[derive(Clone)]
pub struct AgentRunService {
    pub(crate) agent_registry: Arc<AgentRegistry>,
    pub(crate) loop_launcher: Arc<dyn AgentLoopLauncher>,
    pub(crate) agent_run_store: Arc<dyn AgentRunStore>,
    pub(crate) active_agent_runs: ActiveAgentRunRegistry,
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
        loop_launcher: Arc<dyn AgentLoopLauncher>,
        agent_run_store: Arc<dyn AgentRunStore>,
    ) -> Self {
        Self {
            agent_registry,
            loop_launcher,
            agent_run_store,
            active_agent_runs: ActiveAgentRunRegistry::new(),
        }
    }
}

#[async_trait]
impl AgentRunApi for AgentRunService {
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError> {
        spawn::spawn_agent(self, request).await
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        completion::wait_for_agent_outcome(self, agent_run_id).await
    }

    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError> {
        completion::poll_agent_run_outcome(self, agent_run_id).await
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError> {
        cancellation::cancel_agent_run(self, agent_run_id, reason).await
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
        format_record_dir, AgentDefinition, AgentLoopCancellation, AgentLoopCompletion,
        AgentLoopLauncher, AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind, AgentName,
        AgentRegistryBuilder, AgentRun, AgentRunApi, AgentRunRecordIndex, AgentRunRecordTarget,
        AgentRunStatus, AgentRunStore, AgentType, ContentBlock, CoreError, CreatedAgentRun,
        ExecutionStatus, JsonObject, Message, RequestId, RunningRequestAgentRun, SpawnAgentRequest,
        StartAgentLoopRequest, StartedAgentLoop, SubmissionOutcome, ToolUseId, UtcDateTime,
    };

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

        harness.launcher.complete(successful_loop_outcome());
        tokio::time::sleep(Duration::from_millis(20)).await;

        assert_eq!(harness.agent_run_store.finish_count(), 1);
    }

    struct ServiceHarness {
        service: AgentRunService,
        launcher: Arc<ControlledLauncher>,
        agent_run_store: Arc<FakeAgentRunStore>,
    }

    impl ServiceHarness {
        fn new() -> Self {
            let mut registry = AgentRegistryBuilder::new();
            registry.add(root_agent_definition());
            let launcher = Arc::new(ControlledLauncher::default());
            let agent_run_store = Arc::new(FakeAgentRunStore::default());
            let service = AgentRunService::new(
                Arc::new(registry.build()),
                launcher.clone(),
                agent_run_store.clone(),
            );
            Self {
                service,
                launcher,
                agent_run_store,
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
            agent_type: AgentType::Main,
            allowed_tools: Vec::new(),
            terminals: Vec::new(),
            notification_triggers: Vec::new(),
            skill: None,
            context_recipe: None,
        }
    }

    fn root_spawn_request() -> SpawnAgentRequest {
        SpawnAgentRequest {
            agent_run_id: AgentRunId::new_v4(),
            agent_name: AgentName::new("root").expect("valid agent name"),
            agent_type: AgentType::Main,
            request_id: RequestId::new_v4(),
            parent_agent_run_id: None,
            initial_messages: vec![Message::from_user_text("start")],
            tool_use_id: None,
            sandbox_id: None,
            workspace_root: "/workspace".to_owned(),
            is_isolated_workspace_mode: false,
        }
    }

    fn successful_loop_outcome() -> AgentLoopOutcome {
        let mut payload = JsonObject::new();
        payload.insert("kind".to_owned(), serde_json::json!("root"));
        payload.insert("is_pass".to_owned(), serde_json::json!(true));
        payload.insert("outcome".to_owned(), serde_json::json!("done"));
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
        completion_sender: StdMutex<Option<oneshot::Sender<AgentLoopOutcome>>>,
        cancellation: Arc<TestCancellation>,
    }

    impl ControlledLauncher {
        fn complete(&self, outcome: AgentLoopOutcome) {
            let Some(sender) = lock(&self.completion_sender).take() else {
                panic!("agent loop was not started");
            };
            let _ignored = sender.send(outcome);
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
                completion: AgentLoopCompletion::new(async move {
                    receiver.await.unwrap_or_else(|_| AgentLoopOutcome {
                        kind: AgentLoopOutcomeKind::LoopFailed {
                            error_summary: "test completion sender dropped".to_owned(),
                        },
                        final_conversation_messages: Vec::new(),
                        total_token_count: None,
                    })
                }),
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
        indexes: StdMutex<HashMap<AgentRunId, AgentRunRecordIndex>>,
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
        async fn create_agent_run(
            &self,
            agent_run_id: &AgentRunId,
            request_id: &RequestId,
            agent_name: &AgentName,
            agent_type: AgentType,
            parent_agent_run_id: Option<&AgentRunId>,
            tool_use_id: Option<&ToolUseId>,
        ) -> Result<CreatedAgentRun, CoreError> {
            let index = AgentRunRecordIndex {
                request_id: request_id.clone(),
                agent_run_id: agent_run_id.clone(),
            };
            lock(&self.indexes).insert(agent_run_id.clone(), index.clone());
            lock(&self.runs).insert(
                agent_run_id.clone(),
                agent_run_from_index(
                    &index,
                    agent_type,
                    agent_name.clone(),
                    parent_agent_run_id.cloned(),
                    tool_use_id.cloned(),
                    ExecutionStatus::Running,
                    None,
                    None,
                    0,
                    None,
                ),
            );
            Ok(created_from_index(&index))
        }

        async fn finish_agent_run(
            &self,
            agent_run_id: &AgentRunId,
            status: ExecutionStatus,
            terminal_payload: Option<&JsonObject>,
            submission_outcome: Option<&SubmissionOutcome>,
            token_count: i64,
            error: Option<&str>,
        ) -> Result<Option<AgentRun>, CoreError> {
            self.finish_count.fetch_add(1, Ordering::SeqCst);
            let Some(index) = lock(&self.indexes).get(agent_run_id).cloned() else {
                return Ok(None);
            };
            let existing = lock(&self.runs).get(agent_run_id).cloned();
            let run = AgentRun {
                agent_run_id: index.agent_run_id.clone(),
                request_id: index.request_id.clone(),
                agent_type: existing
                    .as_ref()
                    .map_or(AgentType::Main, |run| run.agent_type),
                status,
                agent_name: existing.as_ref().map_or_else(
                    || AgentName::new("root").expect("valid agent name"),
                    |run| run.agent_name.clone(),
                ),
                parent_agent_run_id: existing
                    .as_ref()
                    .and_then(|run| run.parent_agent_run_id.clone()),
                tool_use_id: existing.as_ref().and_then(|run| run.tool_use_id.clone()),
                terminal_payload: terminal_payload.cloned(),
                submission_outcome: submission_outcome.cloned(),
                token_count,
                error: error.map(str::to_owned),
                created_at: UtcDateTime::now(),
                updated_at: UtcDateTime::now(),
                finished_at: Some(UtcDateTime::now()),
            };
            lock(&self.runs).insert(agent_run_id.clone(), run.clone());
            Ok(Some(run))
        }

        async fn record_index_for_agent_run(
            &self,
            agent_run_id: &AgentRunId,
        ) -> Result<Option<AgentRunRecordIndex>, CoreError> {
            Ok(lock(&self.indexes).get(agent_run_id).cloned())
        }

        async fn get_agent_run(
            &self,
            agent_run_id: &AgentRunId,
        ) -> Result<Option<AgentRun>, CoreError> {
            Ok(lock(&self.runs).get(agent_run_id).cloned())
        }

        async fn list_agent_runs_for_request(
            &self,
            request_id: &RequestId,
        ) -> Result<Vec<AgentRun>, CoreError> {
            Ok(lock(&self.runs)
                .values()
                .filter(|run| &run.request_id == request_id)
                .cloned()
                .collect())
        }

        async fn list_running_agent_runs_for_request(
            &self,
            request_id: &RequestId,
        ) -> Result<Vec<RunningRequestAgentRun>, CoreError> {
            Ok(lock(&self.indexes)
                .values()
                .filter(|index| &index.request_id == request_id)
                .map(|index| RunningRequestAgentRun {
                    request_id: index.request_id.clone(),
                    agent_run_id: index.agent_run_id.clone(),
                    status: ExecutionStatus::Running,
                })
                .collect())
        }

        async fn list_child_agent_runs_for_parent_agent_run(
            &self,
            parent_agent_run_id: &AgentRunId,
            agent_type: Option<AgentType>,
        ) -> Result<Vec<AgentRun>, CoreError> {
            Ok(lock(&self.runs)
                .values()
                .filter(|run| run.parent_agent_run_id.as_ref() == Some(parent_agent_run_id))
                .filter(|run| agent_type.is_none_or(|agent_type| run.agent_type == agent_type))
                .cloned()
                .collect())
        }
    }

    fn created_from_index(index: &AgentRunRecordIndex) -> CreatedAgentRun {
        CreatedAgentRun {
            agent_run_id: index.agent_run_id.clone(),
            record_target: AgentRunRecordTarget {
                request_id: index.request_id.clone(),
                agent_run_id: index.agent_run_id.clone(),
                record_dir: format_record_dir(index),
            },
        }
    }

    fn agent_run_from_index(
        index: &AgentRunRecordIndex,
        agent_type: AgentType,
        agent_name: AgentName,
        parent_agent_run_id: Option<AgentRunId>,
        tool_use_id: Option<ToolUseId>,
        status: ExecutionStatus,
        terminal_payload: Option<&JsonObject>,
        submission_outcome: Option<&SubmissionOutcome>,
        token_count: i64,
        error: Option<&str>,
    ) -> AgentRun {
        AgentRun {
            agent_run_id: index.agent_run_id.clone(),
            request_id: index.request_id.clone(),
            agent_type,
            status,
            agent_name,
            parent_agent_run_id,
            tool_use_id,
            terminal_payload: terminal_payload.cloned(),
            submission_outcome: submission_outcome.cloned(),
            token_count,
            error: error.map(str::to_owned),
            created_at: UtcDateTime::now(),
            updated_at: UtcDateTime::now(),
            finished_at: status.is_terminal().then_some(UtcDateTime::now()),
        }
    }

    fn lock<T>(mutex: &StdMutex<T>) -> MutexGuard<'_, T> {
        mutex
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

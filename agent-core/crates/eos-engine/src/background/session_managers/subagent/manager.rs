use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::{AgentName, AgentType};
use eos_agent_message_records::AgentRunRecordKind;
use eos_llm_client::Message;
use eos_state::AgentRun;
use eos_tools::ports::{
    BackgroundSessionPort, CommandSessionPort, SpawnedSubagent, StartedSubagent, SubagentLaunch,
    SubagentLaunchRejection,
};
use eos_tools::{ExecutionMetadata, ToolError, ToolResult};
use eos_types::{AgentRunId, JsonObject, SubagentSessionId};
use serde_json::{json, Value};
use tokio::sync::Mutex;

use super::super::{BackgroundSession, BackgroundSessionManager, BackgroundSessionStatus};
use super::session::{SubagentCancelAction, SubagentSession};
use crate::background::notification::{BackgroundCompletion, BackgroundNotificationEmitter};
use crate::runtime::AgentRunControlFactory;
use crate::{run_agent, AgentRunInput, EngineRunHandles};

#[derive(Debug, Clone)]
pub(in crate::background) struct SubagentCompletion {
    pub(super) subagent_session_id: SubagentSessionId,
    pub(super) status: BackgroundSessionStatus,
    pub(super) result: ToolResult,
}

#[derive(Default)]
struct SubagentSessionState {
    next_session_seq: u64,
    sessions: HashMap<SubagentSessionId, SubagentSession>,
}

/// Tracks subagent background sessions for one agent run.
#[derive(Clone)]
pub(in crate::background) struct SubagentSessionManager {
    sessions: Arc<Mutex<SubagentSessionState>>,
    handles: EngineRunHandles,
    control_factory: AgentRunControlFactory,
    notification: BackgroundNotificationEmitter,
}

impl std::fmt::Debug for SubagentSessionManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SubagentSessionManager")
            .finish_non_exhaustive()
    }
}

impl SubagentSessionManager {
    pub(in crate::background) fn new(
        handles: EngineRunHandles,
        control_factory: AgentRunControlFactory,
        notification: BackgroundNotificationEmitter,
    ) -> Self {
        Self {
            sessions: Arc::new(Mutex::new(SubagentSessionState::default())),
            handles,
            control_factory,
            notification,
        }
    }

    pub(in crate::background) async fn spawn(
        &self,
        ctx: &ExecutionMetadata,
        launch: SubagentLaunch,
    ) -> Result<SpawnedSubagent, ToolError> {
        let registry = &self.handles.agent_registry;

        if let Ok(caller) = AgentName::new(ctx.agent_name.as_str()) {
            if registry.get(&caller).map(|def| def.agent_type) == Some(AgentType::Subagent) {
                return Ok(SpawnedSubagent::Rejected(
                    SubagentLaunchRejection::Recursive,
                ));
            }
        }

        let requested_agent_name = launch.agent_name.clone();
        let Ok(target) = AgentName::new(&requested_agent_name) else {
            return Ok(SpawnedSubagent::Rejected(
                SubagentLaunchRejection::NotRegistered {
                    agent_name: requested_agent_name,
                },
            ));
        };
        let Some(sub_def) = registry.get(&target) else {
            return Ok(SpawnedSubagent::Rejected(
                SubagentLaunchRejection::NotRegistered {
                    agent_name: requested_agent_name,
                },
            ));
        };
        if sub_def.agent_type != AgentType::Subagent {
            return Ok(SpawnedSubagent::Rejected(
                SubagentLaunchRejection::NotSubagent {
                    agent_name: requested_agent_name,
                    agent_type: agent_type_value(sub_def.agent_type).to_owned(),
                },
            ));
        }
        let sub_def = (**sub_def).clone();
        let SubagentLaunch {
            agent_name,
            prompt,
            guidance,
        } = launch;

        let caller_agent_run_id = ctx.require_agent_run_id()?.clone();
        let mut tool_input = JsonObject::new();
        tool_input.insert("agent_name".to_owned(), json!(agent_name.clone()));
        tool_input.insert("prompt".to_owned(), json!(prompt.clone()));

        let agent_run_id = AgentRunId::new_v4();
        let subagent_control = self.control_factory.persisted(agent_run_id.clone(), None);
        let subagent_background = subagent_control.background();
        let subagent_background_port: Arc<dyn BackgroundSessionPort> =
            Arc::new(subagent_background.clone());
        let subagent_command_port: Arc<dyn CommandSessionPort> = Arc::new(subagent_background);
        let mut subagent_meta = ctx.clone();
        subagent_meta.agent_name = sub_def.name.as_str().to_owned();
        subagent_meta.agent_run_id = Some(agent_run_id.clone());
        subagent_meta.conversation = Arc::from(Vec::<Message>::new());
        subagent_meta.tool_use_id = None;

        let run_input = AgentRunInput {
            agent: sub_def,
            initial_messages: vec![
                Message::from_user_text(prompt),
                Message::from_user_text(guidance),
            ],
            task_id: None,
            agent_run_id: agent_run_id.clone(),
            tool_metadata: subagent_meta,
            attempt_submission: None,
            workflow_control: None,
            background_session: Some(subagent_background_port),
            command_session_port: Some(subagent_command_port),
            notifier: subagent_control.notifications(),
            cancellation: subagent_control.cancellation(),
            foreground: subagent_control.foreground(),
            agent_run_registry: None,
            persist_agent_run: true,
            record_kind: AgentRunRecordKind::Subagent {
                parent_agent_run_id: caller_agent_run_id.clone(),
            },
        };

        let handles = self.handles.clone();
        let subagent_session_id = self.next_session_id().await;
        trace_background_tool(
            "background_tool.started",
            &subagent_session_id,
            &caller_agent_run_id,
            BackgroundSessionStatus::Running,
            None,
        );

        let session_control = subagent_control.clone();
        let join = tokio::spawn(async move {
            let _subagent_control = subagent_control;
            let run = run_agent(&handles, run_input, None).await;
            if let Some(error) = run.error {
                tracing::warn!(error = %error, "background subagent run failed");
            }
        });

        self.insert(SubagentSession::running(
            subagent_session_id.clone(),
            session_control,
            join.abort_handle(),
            tool_input,
        ))
        .await;

        Ok(SpawnedSubagent::Launched(StartedSubagent {
            subagent_session_id,
        }))
    }

    pub(in crate::background) async fn progress(
        &self,
        subagent_session_id: &SubagentSessionId,
    ) -> Option<(BackgroundSessionStatus, Option<ToolResult>, String)> {
        let guard = self.sessions.lock().await;
        let session = guard.sessions.get(subagent_session_id)?;
        let agent_name = session
            .tool_input()
            .get("agent_name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        Some((session.status(), session.result().cloned(), agent_name))
    }

    pub(in crate::background) async fn cancel_one(
        &self,
        subagent_session_id: &SubagentSessionId,
        reason: &str,
    ) -> bool {
        let action = {
            let mut guard = self.sessions.lock().await;
            guard
                .sessions
                .get_mut(subagent_session_id)
                .and_then(|session| session.cancel(reason))
        };
        let Some(action) = action else {
            return false;
        };
        finish_cancelled_subagent(action, reason).await;
        true
    }

    async fn next_session_id(&self) -> SubagentSessionId {
        let mut guard = self.sessions.lock().await;
        guard.next_session_seq = guard.next_session_seq.saturating_add(1);
        match format!("subagent_{}", guard.next_session_seq).parse() {
            Ok(id) => id,
            Err(_) => unreachable!("generated subagent ids are non-empty"),
        }
    }

    pub(super) async fn settle(
        &self,
        subagent_session_id: &SubagentSessionId,
        status: BackgroundSessionStatus,
        result: ToolResult,
    ) -> Option<SubagentCompletion> {
        let mut guard = self.sessions.lock().await;
        let session = guard.sessions.get_mut(subagent_session_id)?;
        let result = session.settle(status, result)?;
        Some(SubagentCompletion {
            subagent_session_id: subagent_session_id.clone(),
            status: session.status(),
            result,
        })
    }

    pub(in crate::background) async fn poll_completions(&self) -> Vec<SubagentCompletion> {
        let running = self.running_agent_runs().await;
        let mut completions = Vec::new();
        for (subagent_session_id, agent_run_id) in running {
            let run = match self.handles.agent_run_store.get(&agent_run_id).await {
                Ok(Some(run)) => run,
                Ok(None) | Err(_) => continue,
            };
            let Some((status, result, exit_code)) = completion_from_agent_run(&run) else {
                continue;
            };
            if let Some(completion) = self.settle(&subagent_session_id, status, result).await {
                trace_background_tool(
                    terminal_event_type(status),
                    &subagent_session_id,
                    &agent_run_id,
                    status,
                    Some(exit_code),
                );
                completions.push(completion);
            }
        }
        completions
    }

    async fn running_agent_runs(&self) -> Vec<(SubagentSessionId, AgentRunId)> {
        self.sessions
            .lock()
            .await
            .sessions
            .values()
            .filter(|session| matches!(session.status(), BackgroundSessionStatus::Running))
            .map(|session| (session.id().clone(), session.agent_run_id().clone()))
            .collect()
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
                subagent_session_id: completion.subagent_session_id,
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
                .filter_map(|session| session.cancel(reason))
                .collect::<Vec<_>>()
        };
        for action in actions {
            finish_cancelled_subagent(action, reason).await;
        }
    }
}

async fn finish_cancelled_subagent(action: SubagentCancelAction, reason: &str) {
    if let Err(err) = action
        .agent_run_control
        .finalization()
        .finish_cancelled(reason)
        .await
    {
        tracing::warn!(
            error = %err,
            agent_run_id = action.agent_run_control.agent_run_id().as_str(),
            "background subagent cancellation finalization failed"
        );
    }
    action.agent_run_abort.abort();
}

const fn agent_type_value(agent_type: AgentType) -> &'static str {
    match agent_type {
        AgentType::Agent => "agent",
        AgentType::Subagent => "subagent",
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
    background_task_id: &SubagentSessionId,
    agent_run_id: &AgentRunId,
    status: BackgroundSessionStatus,
    exit_code: Option<i64>,
) {
    tracing::debug!(
        target: "eos_engine::diagnostics",
        event_type,
        background_task_id = background_task_id.as_str(),
        task_kind = "subagent",
        tool_name = "run_subagent",
        agent_run_id = agent_run_id.as_str(),
        status = status_value(status),
        exit_code,
        "background tool lifecycle"
    );
}

pub(super) fn completion_from_agent_run(
    run: &AgentRun,
) -> Option<(BackgroundSessionStatus, ToolResult, i64)> {
    run.finished_at?;
    if let Some(terminal) = &run.terminal_tool_result {
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
fn terminal_called(result: Option<&ToolResult>) -> bool {
    result
        .and_then(|result| result.metadata.get("subagent_terminal_called"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

#[cfg(test)]
fn subagent_status_and_result(
    status: BackgroundSessionStatus,
    result: Option<&ToolResult>,
) -> (&'static str, String) {
    let metadata = result.map(|result| &result.metadata);
    if let Some(reason) = metadata
        .and_then(|m| m.get("subagent_termination_reason"))
        .and_then(Value::as_str)
    {
        return ("terminated", format!("[terminated: {reason}] "));
    }
    if metadata
        .and_then(|m| m.get("subagent_cancelled"))
        .and_then(Value::as_bool)
        == Some(true)
    {
        return ("cancelled", "[cancelled] ".to_owned());
    }
    let output = || {
        result
            .map(|result| result.output.clone())
            .unwrap_or_default()
    };
    match status {
        BackgroundSessionStatus::Running => ("running", String::new()),
        BackgroundSessionStatus::Completed | BackgroundSessionStatus::Delivered
            if terminal_called(result) =>
        {
            ("finished", output())
        }
        BackgroundSessionStatus::Cancelled => ("cancelled", "[cancelled] ".to_owned()),
        _ => ("failed", output()),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use std::sync::Arc;
    use std::time::Duration;

    use async_trait::async_trait;
    use eos_agent_def::AgentRegistry;
    use eos_audit::NoopAuditSink;
    use eos_llm_client::{LlmClient, LlmRequest, LlmStream, ProviderError};
    use eos_sandbox_port::SandboxTransport;
    use eos_skills::SkillRegistry;
    use eos_state::{
        AgentRun, AgentRunStore, CoreError, Sealed as StateSealed, TaskId, UtcDateTime,
    };
    use eos_testkit::{test_tools_root, FakeTransport};
    use eos_tools::{SandboxToolService, SkillToolService, ToolConfigSet};

    use crate::background::session_managers::BackgroundSessionManager;
    use crate::NotificationService;
    use crate::{
        AgentRunControlFactory, BackgroundSessionFactory, EngineRunHandles,
        ForegroundExecutorFactory,
    };

    use super::*;

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
            task_id: Option<&TaskId>,
            agent_name: &str,
            initial_messages: Option<&[JsonObject]>,
        ) -> Result<AgentRun, CoreError> {
            Ok(AgentRun {
                id: agent_run_id.clone(),
                task_id: task_id.cloned(),
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

    fn handles() -> EngineRunHandles {
        let transport: Arc<dyn SandboxTransport> = Arc::new(FakeTransport);
        EngineRunHandles {
            agent_run_store: Arc::new(NoopAgentRunStore),
            llm_client: Arc::new(NoopLlmClient),
            event_source_factory: None,
            agent_registry: Arc::new(
                Vec::<eos_agent_def::AgentDefinition>::new()
                    .into_iter()
                    .collect::<AgentRegistry>(),
            ),
            tool_config: Arc::new(
                ToolConfigSet::load_from_dir(&test_tools_root()).expect("tool config"),
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

    fn manager(notifier: &NotificationService) -> SubagentSessionManager {
        let handles = handles();
        let control_factory = AgentRunControlFactory::new(
            ForegroundExecutorFactory,
            BackgroundSessionFactory::new(
                handles.clone(),
                Arc::new(FakeTransport),
                Duration::from_secs(3600),
                Arc::new(std::sync::OnceLock::new()),
            ),
        );
        SubagentSessionManager::new(
            handles,
            control_factory,
            BackgroundNotificationEmitter::new(notifier.clone()),
        )
    }

    fn tool_input(agent_name: &str) -> JsonObject {
        let mut input = JsonObject::new();
        input.insert("agent_name".to_owned(), json!(agent_name));
        input
    }

    fn subagent_control(
        manager: &SubagentSessionManager,
        agent_run_id: &str,
    ) -> Arc<crate::AgentRunControl> {
        manager
            .control_factory
            .persisted(agent_run_id.parse().expect("agent run id"), None)
    }

    fn finished_run(terminal_tool_result: Option<JsonObject>, error: Option<&str>) -> AgentRun {
        AgentRun {
            id: "run-sub-finished".parse().expect("agent run id"),
            task_id: None,
            initial_messages: None,
            agent_name: "explorer".to_owned(),
            message_history: None,
            terminal_tool_result,
            token_count: 0,
            error: error.map(str::to_owned),
            created_at: UtcDateTime::now(),
            finished_at: Some(UtcDateTime::now()),
        }
    }

    #[test]
    fn terminal_payload_settles_completed_and_finished() {
        let mut terminal = JsonObject::new();
        terminal.insert("output".to_owned(), json!("partial but delivered"));
        terminal.insert("is_error".to_owned(), json!(true));
        terminal.insert("metadata".to_owned(), json!({}));
        terminal.insert("is_terminal".to_owned(), json!(true));
        let (status, result, exit_code) =
            completion_from_agent_run(&finished_run(Some(terminal), None)).expect("completion");
        assert_eq!(status, BackgroundSessionStatus::Completed);
        assert!(result.is_error);
        assert_eq!(exit_code, 1);
        assert_eq!(
            subagent_status_and_result(BackgroundSessionStatus::Completed, Some(&result)).0,
            "finished"
        );
    }

    #[test]
    fn no_terminal_settles_failed() {
        let (status, result, _) =
            completion_from_agent_run(&finished_run(None, None)).expect("completion");
        assert_eq!(status, BackgroundSessionStatus::Failed);
        assert!(result.output.contains("without calling a terminal tool"));
    }

    #[tokio::test]
    async fn count_cancel_and_completion_notification_are_manager_owned() {
        let notifier = NotificationService::new();
        let manager = manager(&notifier);
        let running_id: SubagentSessionId = "subagent_1".parse().expect("subagent id");
        let run_abort = tokio::spawn(std::future::pending::<()>()).abort_handle();
        manager
            .insert(SubagentSession::running(
                running_id.clone(),
                subagent_control(&manager, "run-sub-1"),
                run_abort,
                tool_input("explorer"),
            ))
            .await;

        assert_eq!(manager.count().await, 1);
        assert!(manager.cancel_one(&running_id, "not needed").await);
        assert_eq!(manager.count().await, 0);

        let done_id: SubagentSessionId = "subagent_2".parse().expect("subagent id");
        let run_abort = tokio::spawn(std::future::pending::<()>()).abort_handle();
        manager
            .insert(SubagentSession::running(
                done_id.clone(),
                subagent_control(&manager, "run-sub-2"),
                run_abort,
                tool_input("explorer"),
            ))
            .await;
        let completion = manager
            .settle(
                &done_id,
                BackgroundSessionStatus::Completed,
                ToolResult::ok("findings"),
            )
            .await
            .expect("completion");
        manager.push_notification_on_completion(completion).await;

        let notifications = notifier.drain().await;
        assert_eq!(notifications.len(), 1);
        assert!(notifications[0]
            .message
            .contains("[BACKGROUND COMPLETED] subagent_session_id=subagent_2"));
        assert!(notifications[0].message.contains("findings"));
    }
}

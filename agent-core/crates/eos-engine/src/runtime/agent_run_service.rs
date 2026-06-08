use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::AgentType;
use eos_agent_message_records::AgentRunRecordKind;
use eos_agent_run::{
    AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunStatus,
    SpawnAgentRequest as RuntimeSpawnAgentRequest,
};
use eos_llm_client::Message;
use eos_tools::ToolResult;
use eos_tools::{AttemptSubmissionService, WorkflowServicePort};
use eos_types::AgentRun;
use eos_types::{AgentRunId, JsonObject};
use serde_json::json;
use tokio::sync::watch;
use tokio::sync::Mutex;
use tokio::task::AbortHandle;

use super::registry::AgentRunRegistry;
use super::types::EventCallback;
use crate::run_agent;
use crate::runtime::AgentRunControlFactory;
use crate::{
    build_agent_tool_registry, AgentRunInput, AgentToolRegistryServices, EngineRunHandles,
};

#[derive(Clone)]
pub struct AgentRunService {
    handles: EngineRunHandles,
    control_factory: AgentRunControlFactory,
    agent_run_registry: Option<AgentRunRegistry>,
    attempt_submission: Option<AttemptSubmissionService>,
    workflow_service: Option<Arc<dyn WorkflowServicePort>>,
    event_callback: Option<EventCallback>,
    runs: Arc<Mutex<HashMap<AgentRunId, AgentRunHandle>>>,
}

#[derive(Clone)]
struct AgentRunHandle {
    control: Arc<crate::AgentRunControl>,
    abort: AbortHandle,
    outcome_tx: watch::Sender<Option<AgentRunOutcome>>,
}

impl std::fmt::Debug for AgentRunService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunService").finish_non_exhaustive()
    }
}

impl AgentRunService {
    #[must_use]
    pub fn new(handles: EngineRunHandles, control_factory: AgentRunControlFactory) -> Self {
        Self::with_run_services(handles, control_factory, AgentRunServiceOptions::default())
    }

    #[must_use]
    pub fn with_run_services(
        handles: EngineRunHandles,
        control_factory: AgentRunControlFactory,
        options: AgentRunServiceOptions,
    ) -> Self {
        Self {
            handles,
            control_factory,
            agent_run_registry: options.agent_run_registry,
            attempt_submission: options.attempt_submission,
            workflow_service: options.workflow_service,
            event_callback: options.event_callback,
            runs: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

#[derive(Clone, Default)]
pub struct AgentRunServiceOptions {
    pub agent_run_registry: Option<AgentRunRegistry>,
    pub attempt_submission: Option<AttemptSubmissionService>,
    pub workflow_service: Option<Arc<dyn WorkflowServicePort>>,
    pub event_callback: Option<EventCallback>,
}

impl std::fmt::Debug for AgentRunServiceOptions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunServiceOptions")
            .field("has_agent_run_registry", &self.agent_run_registry.is_some())
            .field("has_attempt_submission", &self.attempt_submission.is_some())
            .field("has_workflow_service", &self.workflow_service.is_some())
            .field("has_event_callback", &self.event_callback.is_some())
            .finish()
    }
}

#[async_trait]
impl AgentRunApi for AgentRunService {
    async fn spawn_agent(
        &self,
        request: RuntimeSpawnAgentRequest,
    ) -> Result<AgentRunId, AgentRunError> {
        let registry = &self.handles.agent_registry;
        let requested_agent_name = request.agent_name.as_str().to_owned();
        let Some(agent_def) = registry.get(&request.agent_name) else {
            return Err(AgentRunError::AgentNotRegistered(requested_agent_name));
        };
        if matches!(request.record_kind, AgentRunRecordKind::Subagent { .. })
            && agent_def.agent_type != AgentType::Subagent
        {
            return Err(AgentRunError::WrongAgentType {
                agent_name: requested_agent_name,
                expected: "subagent",
                actual: agent_type_value(agent_def.agent_type),
            });
        }

        let agent_def = (**agent_def).clone();
        let agent_run_id = request.agent_run_id.unwrap_or_else(AgentRunId::new_v4);
        let control = self
            .control_factory
            .persisted(agent_run_id.clone(), request.task_id.clone());
        if let Some(registry) = &self.agent_run_registry {
            registry.insert(control.clone());
        }
        let background = control.background();
        let background_teardown = background.teardown_service();
        let agent_run_port: Arc<dyn AgentRunApi> = Arc::new(background.clone());
        let subagent_service = background.subagent_tool_service();
        let workflow_service = background.workflow_tool_service();
        let command_sessions = background.command_session_tool_service();
        let tool_registry = build_agent_tool_registry(
            &self.handles,
            &agent_def,
            AgentToolRegistryServices {
                attempt_submission: self.attempt_submission.clone(),
                agent_run_service: Some(agent_run_port),
                subagent_sessions: Some(subagent_service),
                workflow_service: self.workflow_service.clone(),
                workflow_sessions: Some(workflow_service),
                command_sessions: Some(command_sessions),
            },
        );
        let meta = eos_tools::ExecutionMetadata {
            agent_name: agent_def.name.as_str().to_owned(),
            agent_run_id: Some(agent_run_id.clone()),
            request_id: request.request_id.clone(),
            task_id: request.task_id.clone(),
            attempt_id: request.attempt_id.clone(),
            workflow_id: request.workflow_id.clone(),
            tool_use_id: None,
            sandbox_invocation_id: None,
            sandbox_id: request.sandbox_id.clone(),
            is_isolated_workspace_mode: request.is_isolated_workspace_mode,
            workspace_root: request.workspace_root,
            conversation: Arc::from(Vec::<Message>::new()),
        };

        let run_input = AgentRunInput {
            agent: agent_def,
            initial_messages: request.initial_messages,
            task_id: request.task_id,
            agent_run_id: agent_run_id.clone(),
            tool_metadata: meta,
            tool_registry,
            background_teardown: Some(background_teardown),
            notifier: control.notifications(),
            cancellation: control.cancellation(),
            foreground: control.foreground(),
            agent_run_registry: self.agent_run_registry.clone(),
            persist_agent_run: request.persist,
            record_kind: request.record_kind,
        };

        let (outcome_tx, _) = watch::channel(None);
        let publish_tx = outcome_tx.clone();
        let handles = self.handles.clone();
        let event_callback = self.event_callback.clone();
        let spawned_agent_run_id = agent_run_id.clone();
        let join = tokio::spawn(async move {
            let run = run_agent(&handles, run_input, event_callback.as_ref()).await;
            let outcome = outcome_from_run(spawned_agent_run_id, run);
            let _ = publish_tx.send(Some(outcome));
        });
        self.runs.lock().await.insert(
            agent_run_id.clone(),
            AgentRunHandle {
                control,
                abort: join.abort_handle(),
                outcome_tx,
            },
        );

        Ok(agent_run_id)
    }

    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        if let Some(outcome) = self.poll_agent_run_outcome(agent_run_id).await? {
            return Ok(outcome);
        }
        let mut rx = {
            let guard = self.runs.lock().await;
            guard
                .get(agent_run_id)
                .map(|handle| handle.outcome_tx.subscribe())
                .ok_or_else(|| AgentRunError::NotActiveInProcess(agent_run_id.clone()))?
        };
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
        if let Some(outcome) = self
            .runs
            .lock()
            .await
            .get(agent_run_id)
            .and_then(|handle| handle.outcome_tx.borrow().clone())
        {
            return Ok(Some(outcome));
        }
        let Some(run) = self
            .handles
            .agent_run_store
            .get(agent_run_id)
            .await
            .map_err(|err| AgentRunError::Internal(err.to_string()))?
        else {
            return Ok(None);
        };
        Ok(
            completion_from_agent_run(&run).map(|(status, submission_payload, error)| {
                AgentRunOutcome {
                    agent_run_id: agent_run_id.clone(),
                    status,
                    submission_payload,
                    message_history: Vec::new(),
                    token_count: Some(run.token_count),
                    error,
                }
            }),
        )
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError> {
        let Some(handle) = self.runs.lock().await.remove(agent_run_id) else {
            return Ok(());
        };
        handle
            .control
            .finalization()
            .finish_cancelled(reason)
            .await
            .map_err(|err| AgentRunError::Internal(err.to_string()))?;
        handle.abort.abort();
        let _ = handle.outcome_tx.send(Some(AgentRunOutcome {
            agent_run_id: agent_run_id.clone(),
            status: AgentRunStatus::Cancelled,
            submission_payload: None,
            message_history: Vec::new(),
            token_count: None,
            error: Some(reason.to_owned()),
        }));
        Ok(())
    }
}

fn outcome_from_run(agent_run_id: AgentRunId, run: crate::AgentRunResult) -> AgentRunOutcome {
    let missing_terminal = run.error.is_none() && run.submission_outcome.is_none();
    let error = run.error.or_else(|| {
        missing_terminal.then(|| "agent exited without calling a terminal tool".to_owned())
    });
    AgentRunOutcome {
        agent_run_id,
        status: if error.is_some() {
            AgentRunStatus::Failed
        } else {
            AgentRunStatus::Completed
        },
        submission_payload: run.submission_outcome.as_ref().map(tool_result_payload),
        message_history: Vec::new(),
        token_count: None,
        error,
    }
}

const fn agent_type_value(agent_type: AgentType) -> &'static str {
    match agent_type {
        AgentType::Agent => "agent",
        AgentType::Subagent => "subagent",
    }
}

fn completion_from_agent_run(
    run: &AgentRun,
) -> Option<(AgentRunStatus, Option<JsonObject>, Option<String>)> {
    run.finished_at?;
    if let Some(terminal) = &run.terminal_tool_result {
        return Some((
            AgentRunStatus::Completed,
            Some(terminal.clone()),
            run.error.clone(),
        ));
    }
    let message = match &run.error {
        Some(error) => format!("agent run failed: {error}"),
        None => "agent run exited without calling a terminal tool. Findings were not delivered."
            .to_owned(),
    };
    Some((AgentRunStatus::Failed, None, Some(message)))
}

fn tool_result_payload(result: &ToolResult) -> JsonObject {
    let mut payload = JsonObject::new();
    payload.insert("output".to_owned(), json!(result.output));
    payload.insert("is_error".to_owned(), json!(result.is_error));
    payload.insert("metadata".to_owned(), json!(result.metadata));
    payload.insert("is_terminal".to_owned(), json!(result.is_terminal));
    payload
}

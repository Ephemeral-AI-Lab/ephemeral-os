use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::{AgentName, AgentType};
use eos_agent_message_records::AgentRunRecordKind;
use eos_llm_client::Message;
use eos_ports::{
    AgentRunServicePort, CommandSessionPort, Sealed, StartSubagentRunOutcome,
    StartSubagentRunRequest, StartedSubagentRun, SubagentLaunchRejection, SubagentSessionStatus,
    TerminalAgentRun, ToolError, ToolResult,
};
use eos_state::AgentRun;
use eos_types::{AgentRunId, JsonObject};
use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio::task::AbortHandle;

use crate::background::BackgroundTeardownPort;
use crate::runtime::AgentRunControlFactory;
use crate::{run_agent, AgentRunInput, EngineRunHandles};

#[derive(Clone)]
pub struct AgentRunService {
    handles: EngineRunHandles,
    control_factory: AgentRunControlFactory,
    runs: Arc<Mutex<HashMap<AgentRunId, SubagentRunHandle>>>,
}

#[derive(Clone)]
struct SubagentRunHandle {
    control: Arc<crate::AgentRunControl>,
    abort: AbortHandle,
}

impl std::fmt::Debug for AgentRunService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentRunService").finish_non_exhaustive()
    }
}

impl AgentRunService {
    #[must_use]
    pub fn new(handles: EngineRunHandles, control_factory: AgentRunControlFactory) -> Self {
        Self {
            handles,
            control_factory,
            runs: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

impl Sealed for AgentRunService {}

#[async_trait]
impl AgentRunServicePort for AgentRunService {
    async fn start_subagent_run(
        &self,
        request: StartSubagentRunRequest,
    ) -> Result<StartSubagentRunOutcome, ToolError> {
        let registry = &self.handles.agent_registry;
        let ctx = request.ctx;

        if let Ok(caller) = AgentName::new(ctx.agent_name.as_str()) {
            if registry.get(&caller).map(|def| def.agent_type) == Some(AgentType::Subagent) {
                return Ok(StartSubagentRunOutcome::Rejected(
                    SubagentLaunchRejection::Recursive,
                ));
            }
        }

        let requested_agent_name = request.agent_name.clone();
        let Ok(target) = AgentName::new(&requested_agent_name) else {
            return Ok(StartSubagentRunOutcome::Rejected(
                SubagentLaunchRejection::NotRegistered {
                    agent_name: requested_agent_name,
                },
            ));
        };
        let Some(sub_def) = registry.get(&target) else {
            return Ok(StartSubagentRunOutcome::Rejected(
                SubagentLaunchRejection::NotRegistered {
                    agent_name: requested_agent_name,
                },
            ));
        };
        if sub_def.agent_type != AgentType::Subagent {
            return Ok(StartSubagentRunOutcome::Rejected(
                SubagentLaunchRejection::NotSubagent {
                    agent_name: requested_agent_name,
                    agent_type: agent_type_value(sub_def.agent_type).to_owned(),
                },
            ));
        }

        let caller_agent_run_id = ctx.require_agent_run_id()?.clone();
        let sub_def = (**sub_def).clone();
        let agent_run_id = AgentRunId::new_v4();
        let subagent_control = self.control_factory.persisted(agent_run_id.clone(), None);
        let subagent_background = subagent_control.background();
        let subagent_background_port: Arc<dyn BackgroundTeardownPort> =
            Arc::new(subagent_background.clone());
        let subagent_command_port: Arc<dyn CommandSessionPort> = Arc::new(subagent_background);
        let mut subagent_meta = ctx;
        subagent_meta.agent_name = sub_def.name.as_str().to_owned();
        subagent_meta.agent_run_id = Some(agent_run_id.clone());
        subagent_meta.conversation = Arc::from(Vec::<Message>::new());
        subagent_meta.tool_use_id = None;

        let run_input = AgentRunInput {
            agent: sub_def,
            initial_messages: vec![
                Message::from_user_text(request.prompt),
                Message::from_user_text(request.guidance),
            ],
            task_id: None,
            agent_run_id: agent_run_id.clone(),
            tool_metadata: subagent_meta,
            attempt_submission: None,
            agent_run_service: None,
            subagent_sessions: None,
            workflow_service: None,
            workflow_sessions: None,
            background_session: Some(subagent_background_port),
            command_session_port: Some(subagent_command_port),
            notifier: subagent_control.notifications(),
            cancellation: subagent_control.cancellation(),
            foreground: subagent_control.foreground(),
            agent_run_registry: None,
            persist_agent_run: true,
            record_kind: AgentRunRecordKind::Subagent {
                parent_agent_run_id: caller_agent_run_id,
            },
        };

        let handles = self.handles.clone();
        let control = subagent_control.clone();
        let join = tokio::spawn(async move {
            let _subagent_control = subagent_control;
            let run = run_agent(&handles, run_input, None).await;
            if let Some(error) = run.error {
                tracing::warn!(error = %error, "background subagent run failed");
            }
        });
        self.runs.lock().await.insert(
            agent_run_id.clone(),
            SubagentRunHandle {
                control,
                abort: join.abort_handle(),
            },
        );

        Ok(StartSubagentRunOutcome::Started(StartedSubagentRun {
            agent_run_id,
            agent_name: requested_agent_name,
        }))
    }

    async fn poll_terminal_agent_run(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<TerminalAgentRun>, ToolError> {
        let Some(run) = self.handles.agent_run_store.get(agent_run_id).await? else {
            return Ok(None);
        };
        let Some((status, result)) = completion_from_agent_run(&run) else {
            return Ok(None);
        };
        self.runs.lock().await.remove(agent_run_id);
        Ok(Some(TerminalAgentRun {
            agent_run_id: agent_run_id.clone(),
            status,
            result,
        }))
    }

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), ToolError> {
        let Some(handle) = self.runs.lock().await.remove(agent_run_id) else {
            return Ok(());
        };
        handle
            .control
            .finalization()
            .finish_cancelled(reason)
            .await
            .map_err(|err| ToolError::Internal(err.to_string()))?;
        handle.abort.abort();
        Ok(())
    }
}

const fn agent_type_value(agent_type: AgentType) -> &'static str {
    match agent_type {
        AgentType::Agent => "agent",
        AgentType::Subagent => "subagent",
    }
}

fn completion_from_agent_run(run: &AgentRun) -> Option<(SubagentSessionStatus, ToolResult)> {
    run.finished_at?;
    if let Some(terminal) = &run.terminal_tool_result {
        return Some((
            SubagentSessionStatus::Completed,
            tool_result_from_payload(terminal),
        ));
    }
    let message = match &run.error {
        Some(error) => format!("subagent crashed: {error}"),
        None => "subagent exited without calling a terminal tool. Findings were not delivered."
            .to_owned(),
    };
    Some((
        SubagentSessionStatus::Failed,
        ToolResult::error(message).meta("subagent_terminal_called", json!(false)),
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

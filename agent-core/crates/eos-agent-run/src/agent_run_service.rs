//! Launcher-backed agent-run lifecycle service.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentDefinition, AgentLoopLauncher, AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeFuture,
    AgentLoopOutcomeKind, AgentName as DefinitionAgentName, AgentRegistry, AgentRunApi,
    AgentRunError, AgentRunId, AgentRunOutcome, AgentRunRecordTarget, AgentRunStatus,
    AgentRunStore, AgentType, CreatedTaskAgentRun, Message, ParentedAgentRunKind,
    SpawnAgentRequest, SpawnAgentTarget, TaskAgentRunKind, TaskAgentRunStore, TaskId, TaskStatus,
};

use crate::active_agent_runs::{ActiveAgentRunRecord, ActiveAgentRuns};
use crate::agent_loop_request::build_start_agent_loop_request;
use crate::agent_run_persistence::{
    completion_from_agent_run, create_agent_run_if_requested, finish_agent_run_cancelled,
    finish_agent_run_if_requested,
};
use crate::agent_run_records::to_agent_run_record_kind;
use crate::records::{AgentMessageRecords, AgentRunRecordStart, NodeFinishStatus};

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
    message_records: Option<AgentMessageRecords>,
    active_agent_runs: ActiveAgentRuns,
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
        message_records: Option<AgentMessageRecords>,
    ) -> Self {
        Self {
            agent_registry,
            agent_loop_launcher,
            agent_run_store,
            task_agent_run_store,
            message_records,
            active_agent_runs: ActiveAgentRuns::new(),
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

    async fn start_message_record(
        &self,
        request: &SpawnAgentRequest,
        agent_def: &AgentDefinition,
        record_target: &AgentRunRecordTarget,
    ) -> Result<Option<ActiveAgentRunRecord>, AgentRunError> {
        let Some(message_records) = &self.message_records else {
            return Ok(None);
        };
        let kind = to_agent_run_record_kind(&request.target.task_agent_run_kind());
        let handle = message_records
            .start_agent_run(AgentRunRecordStart {
                request_id: &record_target.request_id,
                task_id: Some(&record_target.task_id),
                agent_run_id: &record_target.agent_run_id,
                agent_name: agent_def.name.as_str(),
                kind: &kind,
                system_prompt: agent_def.system_prompt.as_deref().unwrap_or_default(),
                initial_messages: &request.initial_messages,
            })
            .await
            .map_err(|err| AgentRunError::Internal(err.to_string()))?;
        Ok(Some(ActiveAgentRunRecord::new(
            handle,
            request.initial_messages.len(),
        )))
    }

    async fn finish_message_record(
        &self,
        message_record: Option<ActiveAgentRunRecord>,
        outcome: &AgentRunOutcome,
    ) {
        let Some(message_record) = message_record else {
            return;
        };
        let later_message_start = message_record
            .initial_message_count
            .min(outcome.message_history.len());
        if let Err(err) = message_record
            .handle
            .append_messages(&outcome.message_history[later_message_start..])
            .await
        {
            tracing::warn!(
                agent_run_id = %outcome.agent_run_id,
                error = %err,
                "failed to append agent-run message record messages"
            );
        }

        let status = match outcome.status {
            AgentRunStatus::Completed => NodeFinishStatus::Completed,
            AgentRunStatus::Failed | AgentRunStatus::Cancelled => NodeFinishStatus::Failed,
        };
        if let Err(err) = message_record.handle.finish(status).await {
            tracing::warn!(
                agent_run_id = %outcome.agent_run_id,
                error = %err,
                "failed to finish agent-run message record"
            );
        }
    }

    async fn finalize_agent_run_from_agent_loop_outcome(
        &self,
        agent_run_id: AgentRunId,
        persistence_requested: bool,
        outcome: AgentLoopOutcome,
    ) -> AgentRunOutcome {
        let agent_outcome = agent_run_outcome_from_loop(agent_run_id.clone(), outcome);
        let error = agent_outcome.error.as_deref();
        let finish = finish_agent_run_if_requested(
            &*self.agent_run_store,
            persistence_requested,
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
        persistence_requested: bool,
    ) -> AgentRunOutcome {
        let error = "agent loop outcome sender dropped".to_owned();
        let _ignored = finish_agent_run_if_requested(
            &*self.agent_run_store,
            persistence_requested,
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
            SpawnAgentTarget::Root {
                request_id,
                task_id,
            } => {
                self.task_agent_run_store
                    .create_root_task_agent_run(request_id, task_id, agent_run_id, agent_name)
                    .await
            }
            SpawnAgentTarget::Workflow {
                request_id,
                task_id,
                workflow,
                role,
            } => {
                self.task_agent_run_store
                    .create_workflow_task_agent_run(
                        request_id,
                        task_id,
                        agent_run_id,
                        workflow,
                        *role,
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
        let persistence_requested = create_agent_run_if_requested(
            &*self.agent_run_store,
            true,
            legacy_task_row_id_for_agent_run(&request.target),
            &agent_run_id,
            agent_def.name.as_str(),
        )
        .await?;
        let record_target = created_run.record_target.clone();
        let message_record = self
            .start_message_record(&request, &agent_def, &record_target)
            .await?;
        if let Some(record_runtime_state) = &self.runtime_state_recorder {
            record_runtime_state(&request, &created_run)?;
        }
        let start_request = build_start_agent_loop_request(&agent_def, request, record_target);
        let agent_run_api: Arc<dyn AgentRunApi> = Arc::new(self.clone());
        let started = self
            .agent_loop_launcher
            .start_agent_loop(start_request, agent_run_api);

        self.active_agent_runs
            .insert(agent_run_id.clone(), started.cancellation, message_record)
            .await;
        let service = self.clone();
        let forward_agent_run_id = agent_run_id.clone();
        tokio::spawn(async move {
            forward_agent_loop_outcome(
                service,
                forward_agent_run_id,
                persistence_requested,
                started.outcome,
            )
            .await;
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
        let mut completion = self.active_agent_runs.take(agent_run_id).await;
        if let Some(completion) = &completion {
            completion.cancel(reason);
        }
        finish_agent_run_cancelled(&*self.agent_run_store, agent_run_id, reason).await?;
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
        if let Some(completion) = &mut completion {
            self.finish_message_record(completion.take_message_record(), &outcome)
                .await;
        }
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
    persistence_requested: bool,
    outcome: AgentLoopOutcomeFuture,
) {
    let received = outcome.await;
    let Some(mut completion) = service.active_agent_runs.take(&agent_run_id).await else {
        return;
    };
    let outcome = match received {
        Some(outcome) => {
            service
                .finalize_agent_run_from_agent_loop_outcome(
                    agent_run_id.clone(),
                    persistence_requested,
                    outcome,
                )
                .await
        }
        None => {
            service
                .finalize_agent_run_from_dropped_agent_loop_sender(
                    agent_run_id.clone(),
                    persistence_requested,
                )
                .await
        }
    };
    service
        .finish_message_record(completion.take_message_record(), &outcome)
        .await;
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

fn legacy_task_row_id_for_agent_run(target: &SpawnAgentTarget) -> Option<&TaskId> {
    match target {
        SpawnAgentTarget::Root { task_id, .. } | SpawnAgentTarget::Workflow { task_id, .. } => {
            Some(task_id)
        }
        SpawnAgentTarget::Subagent { .. } | SpawnAgentTarget::Advisor { .. } => None,
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
}

//! Full agent-loop executor.

use std::sync::Arc;

use eos_llm_client::{ContentBlock, LlmRequest, Message, UsageSnapshot};
use eos_tool::{RegisteredTool, ToolName, ToolResult};
use eos_types::{AgentRunApi, AgentRunId, AgentRunRuntimeSnapshot, JsonObject, ToolUseId};
use futures::StreamExt;

use super::{
    AgentLoopCancelSignal, AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind,
    AgentLoopProviderStream, AgentLoopRunServices, AgentLoopState, AgentLoopToolRegistryFactory,
    BackgroundSessionInputs, ExecutionMetadataBuildInput, StartAgentLoopRequest,
    ToolCallHookStores, ToolExecutionMetadataReader,
};
use crate::notifications::EngineNotificationQueue;
use crate::provider_stream::{messages::build_provider_messages, ProviderStreamSource};
use crate::records::{
    AgentRecordWriter, AgentRunRecordHandle, AgentRunRecordKind, AgentRunRecordStart,
    NodeFinishStatus,
};
use crate::tool_call::{
    execute_tool_once, lifecycle_batch_decision, reject_terminal_batch, DispatchCall,
};
use crate::{stamp_identity, EngineError, StreamEvent};

/// Executes a full agent loop from request to terminal outcome.
pub(crate) struct AgentLoopExecutor {
    provider_stream_source: AgentLoopProviderStream,
    tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
    metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    cancel_signal: AgentLoopCancelSignal,
    background_inputs: Option<BackgroundSessionInputs>,
    hook_stores: Option<ToolCallHookStores>,
    event_sink: Option<crate::event::EngineEventSink>,
    record_writer: Option<AgentRecordWriter>,
    agent_run_api: Arc<dyn AgentRunApi>,
}

/// Dependencies for one agent-loop executor.
pub(crate) struct AgentLoopExecutorInput {
    pub(crate) provider_stream_source: AgentLoopProviderStream,
    pub(crate) tool_registry_factory: Arc<dyn AgentLoopToolRegistryFactory>,
    pub(crate) metadata_reader: Arc<dyn ToolExecutionMetadataReader>,
    pub(crate) cancel_signal: AgentLoopCancelSignal,
    pub(crate) background_inputs: Option<BackgroundSessionInputs>,
    pub(crate) hook_stores: Option<ToolCallHookStores>,
    pub(crate) event_sink: Option<crate::event::EngineEventSink>,
    pub(crate) record_writer: Option<AgentRecordWriter>,
    pub(crate) agent_run_api: Arc<dyn AgentRunApi>,
}

impl std::fmt::Debug for AgentLoopExecutor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopExecutor").finish_non_exhaustive()
    }
}

impl AgentLoopExecutor {
    pub(crate) fn new(input: AgentLoopExecutorInput) -> Self {
        Self {
            provider_stream_source: input.provider_stream_source,
            tool_registry_factory: input.tool_registry_factory,
            metadata_reader: input.metadata_reader,
            cancel_signal: input.cancel_signal,
            background_inputs: input.background_inputs,
            hook_stores: input.hook_stores,
            event_sink: input.event_sink,
            record_writer: input.record_writer,
            agent_run_api: input.agent_run_api,
        }
    }

    pub(crate) async fn execute_agent_loop(
        self,
        request: StartAgentLoopRequest,
    ) -> AgentLoopOutcome {
        let event_identity = match self
            .metadata_reader
            .agent_run_snapshot(&request.record_target.agent_run_id)
            .await
        {
            Ok(identity) => identity,
            Err(error) => {
                return AgentLoopOutcome {
                    kind: AgentLoopOutcomeKind::LoopFailed {
                        error_summary: error.to_string(),
                    },
                    final_conversation_messages: Vec::new(),
                    total_token_count: None,
                };
            }
        };
        let record = match self.start_agent_run_record(&request, &event_identity).await {
            Ok(record) => record,
            Err(error) => {
                return AgentLoopOutcome {
                    kind: AgentLoopOutcomeKind::LoopFailed {
                        error_summary: error.to_string(),
                    },
                    final_conversation_messages: request.initial_messages,
                    total_token_count: None,
                };
            }
        };
        let provider_stream_source = self.resolve_provider_stream_source(&request, &event_identity);
        let run_services = self.build_run_services(&request.record_target.agent_run_id);
        let initial_messages_for_error = request.initial_messages.clone();
        let mut state = match AgentLoopState::from_request(
            request,
            &*self.tool_registry_factory,
            run_services,
            self.agent_run_api.clone(),
        ) {
            Ok(state) => state,
            Err(error) => {
                let outcome = AgentLoopOutcome {
                    kind: AgentLoopOutcomeKind::LoopFailed {
                        error_summary: error.to_string(),
                    },
                    final_conversation_messages: initial_messages_for_error,
                    total_token_count: None,
                };
                return self.finish_agent_run_record(record, outcome).await;
            }
        };

        loop {
            if let Some(reason) = self.cancel_signal.reason() {
                state
                    .teardown_background(&format!("agent loop cancelled: {reason}"))
                    .await;
                let outcome =
                    state.loop_failed_summary(format!("agent loop cancelled: {reason}"));
                return self.finish_agent_run_record(record, outcome).await;
            }
            if state.turn_limit_reached() {
                state
                    .teardown_background("agent loop exited without a terminal tool submission")
                    .await;
                let summary = state.terminal_not_submitted_summary();
                let outcome = state.loop_failed_summary(summary);
                return self.finish_agent_run_record(record, outcome).await;
            }

            self.drain_notifications(&mut state, &event_identity).await;
            let turn_result = match self
                .execute_assistant_turn(&provider_stream_source, &event_identity, &mut state)
                .await
            {
                Ok(turn_result) => turn_result,
                Err(error) => {
                    state
                        .teardown_background(&format!("agent loop failed: {error}"))
                        .await;
                    let outcome = state.loop_failed(&error);
                    return self.finish_agent_run_record(record, outcome).await;
                }
            };

            match turn_result {
                AssistantTurnResult::Continue => {}
                AssistantTurnResult::TerminalToolSubmitted { outcome } => {
                    state
                        .teardown_background("parent agent submitted its terminal")
                        .await;
                    let outcome = state.terminal_tool_submitted(&outcome);
                    return self.finish_agent_run_record(record, outcome).await;
                }
            }
        }
    }

    async fn start_agent_run_record(
        &self,
        request: &StartAgentLoopRequest,
        event_identity: &AgentRunRuntimeSnapshot,
    ) -> Result<Option<LoopRecordHandle>, EngineError> {
        let Some(record_writer) = &self.record_writer else {
            return Ok(None);
        };
        let record_kind =
            AgentRunRecordKind::from_task_agent_run_kind(&request.record_target.task_agent_run_kind);
        let (system_prompt, initial_messages) =
            split_record_initial_messages(&request.initial_messages);
        let handle = record_writer
            .start_agent_run_at(
                &request.record_target.record_dir,
                AgentRunRecordStart {
                    request_id: &request.record_target.request_id,
                    task_id: Some(&request.record_target.task_id),
                    agent_run_id: &request.record_target.agent_run_id,
                    agent_name: &event_identity.agent_name,
                    kind: &record_kind,
                    system_prompt: &system_prompt,
                    initial_messages: &initial_messages,
                },
            )
            .await
            .map_err(|error| EngineError::Internal(error.to_string()))?;
        Ok(Some(LoopRecordHandle {
            handle,
            initial_message_count: initial_messages.len(),
        }))
    }

    async fn finish_agent_run_record(
        &self,
        record: Option<LoopRecordHandle>,
        outcome: AgentLoopOutcome,
    ) -> AgentLoopOutcome {
        let Some(record) = record else {
            return outcome;
        };
        let later_messages = loop_messages_to_llm_messages(&outcome.final_conversation_messages);
        let later_message_start = record.initial_message_count.min(later_messages.len());
        if let Err(error) = record
            .handle
            .append_messages(&later_messages[later_message_start..])
            .await
        {
            return record_write_failed(outcome, error);
        }
        if let Err(error) = record.handle.finish(node_finish_status(&outcome.kind)).await {
            return record_write_failed(outcome, error);
        }
        outcome
    }

    async fn execute_assistant_turn(
        &self,
        provider_stream_source: &Arc<dyn ProviderStreamSource>,
        event_identity: &AgentRunRuntimeSnapshot,
        state: &mut AgentLoopState,
    ) -> Result<AssistantTurnResult, EngineError> {
        let request = build_loop_provider_request(state);
        let mut stream = provider_stream_source.stream(&request).await?;
        let mut final_message: Option<Message> = None;
        let mut final_usage: Option<UsageSnapshot> = None;

        while let Some(item) = stream.next().await {
            let event = item?;
            let event = stamp_identity(
                event,
                &event_identity.agent_name,
                &event_identity.agent_run_id,
            );
            if let StreamEvent::AssistantMessageComplete { payload, .. } = &event {
                final_usage = Some(payload.usage);
                final_message = Some(payload.message.clone());
            }
            self.emit_event(&event);
        }

        let message = final_message.ok_or_else(|| {
            EngineError::Internal("provider stream ended without assistant completion".to_owned())
        })?;
        if let Some(usage) = final_usage {
            let turn_tokens = i64::from(usage.input_tokens) + i64::from(usage.output_tokens);
            state.total_token_count = Some(
                state
                    .total_token_count
                    .unwrap_or_default()
                    .saturating_add(turn_tokens),
            );
        }

        let tool_calls = tool_uses_from_message(&message);
        state.record_tool_calls(tool_calls.len());
        state
            .conversation_messages
            .push(AgentLoopMessage::AssistantMessage(message));
        if tool_calls.is_empty() {
            state.record_text_only_turn();
            return Ok(AssistantTurnResult::Continue);
        }

        let dispatch = self.dispatch_tool_batch(state, &tool_calls).await?;
        let result_message = Message {
            role: eos_llm_client::MessageRole::User,
            content: dispatch.tool_results,
        };
        state
            .conversation_messages
            .push(AgentLoopMessage::UserMessage(result_message));

        match dispatch.submission_outcome {
            Some(outcome) if outcome.is_terminal => {
                Ok(AssistantTurnResult::TerminalToolSubmitted { outcome })
            }
            _ => Ok(AssistantTurnResult::Continue),
        }
    }

    async fn dispatch_tool_batch(
        &self,
        state: &AgentLoopState,
        calls: &[ToolUseRequest],
    ) -> Result<LoopToolDispatchOutcome, EngineError> {
        let dispatch_calls: Vec<DispatchCall<'_>> = calls
            .iter()
            .map(|call| DispatchCall {
                tool_use_id: call.tool_use_id.as_str(),
                name: &call.name,
            })
            .collect();

        if let Some(rejections) = reject_terminal_batch(&dispatch_calls, &state.tool_registry) {
            let tool_results = calls
                .iter()
                .filter_map(|call| {
                    rejections
                        .iter()
                        .find(|rejection| rejection.tool_use_id == call.tool_use_id.as_str())
                        .map(|rejection| {
                            result_block(&call.tool_use_id, &rejection_result(&rejection.message))
                        })
                })
                .collect();
            return Ok(LoopToolDispatchOutcome {
                tool_results,
                submission_outcome: None,
            });
        }

        let lifecycle = lifecycle_batch_decision(&dispatch_calls, &state.tool_registry);
        let dispatched: std::collections::BTreeSet<&str> =
            lifecycle.dispatched.iter().map(String::as_str).collect();
        let rejected: std::collections::BTreeMap<String, ToolResult> = lifecycle
            .rejected
            .into_iter()
            .map(|rejection| (rejection.tool_use_id, rejection_result(&rejection.message)))
            .collect();

        let mut tool_results = Vec::new();
        let mut submission_outcome = None;
        let conversation = Arc::from(loop_messages_to_llm_messages(&state.conversation_messages));

        for call in calls {
            if let Some(result) = rejected.get(call.tool_use_id.as_str()) {
                tool_results.push(result_block(&call.tool_use_id, result));
                continue;
            }
            if !dispatched.contains(call.tool_use_id.as_str()) {
                continue;
            }

            let Some(tool) = state.tool_registry.get_wire(&call.name).cloned() else {
                let result = rejection_result(&format!("Unknown tool `{}`.", call.name));
                tool_results.push(result_block(&call.tool_use_id, &result));
                continue;
            };

            let result = self
                .execute_registered_tool(state, call, &tool, Arc::clone(&conversation))
                .await?;
            if tool.is_terminal && result.is_terminal {
                submission_outcome = Some(result.clone());
            }
            tool_results.push(result_block(&call.tool_use_id, &result));
        }

        Ok(LoopToolDispatchOutcome {
            tool_results,
            submission_outcome,
        })
    }

    async fn execute_registered_tool(
        &self,
        state: &AgentLoopState,
        call: &ToolUseRequest,
        tool: &RegisteredTool,
        conversation: Arc<[Message]>,
    ) -> Result<ToolResult, EngineError> {
        let tool_name = ToolName::from_wire(&call.name)
            .ok_or_else(|| EngineError::UnknownTool(call.name.clone()))?;
        let metadata = self
            .metadata_reader
            .build_execution_metadata(ExecutionMetadataBuildInput {
                agent_run_id: state.agent_run_id.clone(),
                tool_name,
                tool_use_id: call.tool_use_id.clone(),
                conversation,
            })
            .await
            .map_err(|err| EngineError::Internal(err.to_string()))?;
        self.emit_event(&StreamEvent::ToolExecutionStarted {
            agent_name: metadata.agent_name.clone(),
            agent_run_id: metadata.agent_run_id.clone(),
            tool_name: call.name.clone(),
            tool_input: call.input.clone(),
            tool_use_id: call.tool_use_id.clone(),
        });
        let hooks = state.background().and_then(|background| {
            self.hook_stores
                .clone()
                .map(|stores| crate::tool_call::ToolCallHooks::new(background, stores))
        });
        let result = execute_tool_once(tool, &call.input, &metadata, hooks.as_ref()).await?;
        self.emit_event(&StreamEvent::ToolExecutionCompleted {
            agent_name: metadata.agent_name,
            agent_run_id: metadata.agent_run_id,
            tool_name: call.name.clone(),
            output: result.output.clone(),
            is_error: result.is_error,
            tool_use_id: call.tool_use_id.clone(),
            metadata: result.metadata.clone(),
            is_terminal: result.is_terminal,
        });
        Ok(result)
    }

    fn build_run_services(&self, agent_run_id: &AgentRunId) -> AgentLoopRunServices {
        let Some(inputs) = &self.background_inputs else {
            return AgentLoopRunServices::inert();
        };
        let notifier = EngineNotificationQueue::new();
        let background =
            inputs.build_managers(agent_run_id.clone(), &self.agent_run_api, notifier.clone());
        AgentLoopRunServices::from_background(&background, notifier)
    }

    fn resolve_provider_stream_source(
        &self,
        request: &StartAgentLoopRequest,
        agent_run_snapshot: &AgentRunRuntimeSnapshot,
    ) -> Arc<dyn ProviderStreamSource> {
        match &self.provider_stream_source {
            AgentLoopProviderStream::Static(source) => Arc::clone(source),
            AgentLoopProviderStream::Factory(factory) => factory(request, agent_run_snapshot),
        }
    }

    async fn drain_notifications(
        &self,
        state: &mut AgentLoopState,
        event_identity: &AgentRunRuntimeSnapshot,
    ) {
        for notification in state.drain_notifications().await {
            self.emit_event(&StreamEvent::SystemNotification {
                agent_name: event_identity.agent_name.clone(),
                agent_run_id: Some(event_identity.agent_run_id.clone()),
                text: notification.message,
            });
        }
    }

    fn emit_event(&self, event: &StreamEvent) {
        if let Some(sink) = &self.event_sink {
            sink(event);
        }
    }
}

struct LoopRecordHandle {
    handle: AgentRunRecordHandle,
    initial_message_count: usize,
}

/// Result of one private assistant turn.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum AssistantTurnResult {
    /// Continue the agent loop.
    Continue,
    /// A terminal tool submitted successfully.
    TerminalToolSubmitted {
        /// Terminal tool result.
        outcome: ToolResult,
    },
}

struct LoopToolDispatchOutcome {
    tool_results: Vec<ContentBlock>,
    submission_outcome: Option<ToolResult>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ToolUseRequest {
    tool_use_id: ToolUseId,
    name: String,
    input: JsonObject,
}

fn build_loop_provider_request(state: &AgentLoopState) -> LlmRequest {
    let mut system_prompt = None;
    let messages: Vec<Message> = state
        .conversation_messages
        .iter()
        .filter_map(|message| match message {
            AgentLoopMessage::SystemPrompt(prompt) => {
                system_prompt = Some(prompt.clone());
                None
            }
            AgentLoopMessage::UserMessage(message)
            | AgentLoopMessage::AssistantMessage(message) => Some(message.clone()),
        })
        .collect();
    let mut builder = LlmRequest::builder(state.model_key.clone())
        .messages(build_provider_messages(&messages))
        .max_tokens(state.max_completion_tokens)
        .tools(state.tool_registry.specs());
    if let Some(prompt) = system_prompt {
        builder = builder.system_prompt(prompt);
    }
    builder.build()
}

fn tool_uses_from_message(message: &Message) -> Vec<ToolUseRequest> {
    message
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::ToolUse {
                tool_use_id,
                name,
                input,
            } => Some(ToolUseRequest {
                tool_use_id: tool_use_id.clone(),
                name: name.clone(),
                input: input.clone(),
            }),
            _ => None,
        })
        .collect()
}

fn loop_messages_to_llm_messages(messages: &[AgentLoopMessage]) -> Vec<Message> {
    messages
        .iter()
        .filter_map(|message| match message {
            AgentLoopMessage::SystemPrompt(_) => None,
            AgentLoopMessage::UserMessage(message)
            | AgentLoopMessage::AssistantMessage(message) => Some(message.clone()),
        })
        .collect()
}

fn split_record_initial_messages(messages: &[AgentLoopMessage]) -> (String, Vec<Message>) {
    let mut system_prompt = String::new();
    let llm_messages = messages
        .iter()
        .filter_map(|message| match message {
            AgentLoopMessage::SystemPrompt(prompt) => {
                if system_prompt.is_empty() {
                    system_prompt = prompt.clone();
                }
                None
            }
            AgentLoopMessage::UserMessage(message)
            | AgentLoopMessage::AssistantMessage(message) => Some(message.clone()),
        })
        .collect();
    (system_prompt, llm_messages)
}

fn node_finish_status(kind: &AgentLoopOutcomeKind) -> NodeFinishStatus {
    match kind {
        AgentLoopOutcomeKind::TerminalToolSubmitted { .. } => NodeFinishStatus::Completed,
        AgentLoopOutcomeKind::LoopFailed { .. } => NodeFinishStatus::Failed,
    }
}

fn record_write_failed(
    mut outcome: AgentLoopOutcome,
    error: impl std::fmt::Display,
) -> AgentLoopOutcome {
    outcome.kind = AgentLoopOutcomeKind::LoopFailed {
        error_summary: format!("agent-run record write failed: {error}"),
    };
    outcome
}

fn result_block(tool_use_id: &ToolUseId, result: &ToolResult) -> ContentBlock {
    ContentBlock::ToolResult {
        tool_use_id: tool_use_id.clone(),
        content: result.output.clone(),
        is_error: result.is_error,
        metadata: result.metadata.clone(),
        is_terminal: result.is_terminal,
    }
}

fn rejection_result(message: &str) -> ToolResult {
    ToolResult {
        output: message.to_owned(),
        is_error: true,
        metadata: JsonObject::new(),
        is_terminal: false,
    }
}

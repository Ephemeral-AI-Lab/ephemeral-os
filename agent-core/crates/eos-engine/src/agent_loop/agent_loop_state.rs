//! Mutable state for one agent loop.

use std::sync::Arc;

use eos_agent_ports::{
    AgentExecutionMetadataService, AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind,
    StartAgentLoopRequest,
};
use eos_llm_client::{ContentBlock, Message, MessageRole};
use eos_tool_ports::{
    CommandSessionToolService, SubagentToolService, SystemNotification, ToolRegistry, ToolResult,
    WorkflowToolService,
};
use eos_types::AgentRunId;

use crate::background::{BackgroundManagers, BackgroundTeardownService};
use crate::notifications::NotificationService;

use super::{AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory};
use crate::EngineError;

/// Engine-private mutable loop state.
#[allow(dead_code)] // Phase 2 API shell; read by the production turn executor in Phase 5.
pub(crate) struct AgentLoopState {
    /// Agent-run id.
    pub(crate) agent_run_id: AgentRunId,
    /// Loop transcript.
    pub(crate) conversation_messages: Vec<AgentLoopMessage>,
    /// Resolved model key.
    pub(crate) model_key: String,
    /// Completion token cap.
    pub(crate) max_completion_tokens: u32,
    /// Tool-call limit.
    pub(crate) tool_call_limit: u32,
    /// Concrete registry for this loop.
    pub(crate) tool_registry: ToolRegistry,
    /// Metadata rendering service.
    pub(crate) metadata_service: Arc<dyn AgentExecutionMetadataService>,
    /// Total provider token count when known.
    pub(crate) total_token_count: Option<i64>,
    /// Completed assistant turns.
    pub(crate) completed_turns: u32,
    /// Run-local notification queue drained at loop turn boundaries.
    pub(crate) notifier: NotificationService,
    /// Run-local background teardown service.
    background_teardown: Option<BackgroundTeardownService>,
}

impl std::fmt::Debug for AgentLoopState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AgentLoopState")
            .field("agent_run_id", &self.agent_run_id)
            .field("conversation_messages", &self.conversation_messages.len())
            .field("model_key", &self.model_key)
            .field("max_completion_tokens", &self.max_completion_tokens)
            .field("tool_call_limit", &self.tool_call_limit)
            .field("tool_registry_len", &self.tool_registry.len())
            .field("total_token_count", &self.total_token_count)
            .field("completed_turns", &self.completed_turns)
            .finish_non_exhaustive()
    }
}

impl AgentLoopState {
    pub(crate) fn from_request(
        request: StartAgentLoopRequest,
        tool_registry_factory: &dyn AgentLoopToolRegistryFactory,
        metadata_service: Arc<dyn AgentExecutionMetadataService>,
        run_services: AgentLoopRunServices,
    ) -> Result<Self, EngineError> {
        let tool_registry =
            tool_registry_factory.build_tool_registry(AgentLoopToolRegistryBuildInput {
                agent_run_id: request.agent_run_id.clone(),
                subagent_sessions: run_services.subagent_sessions,
                workflow_sessions: run_services.workflow_sessions,
                command_sessions: run_services.command_sessions,
            })?;
        Ok(Self {
            agent_run_id: request.agent_run_id,
            conversation_messages: request.initial_messages,
            model_key: request.model_key,
            max_completion_tokens: request.max_completion_tokens,
            tool_call_limit: request.tool_call_limit,
            tool_registry,
            metadata_service,
            total_token_count: None,
            completed_turns: 0,
            notifier: run_services.notifier,
            background_teardown: run_services.background_teardown,
        })
    }

    pub(crate) fn advance_turn(&mut self) {
        self.completed_turns = self.completed_turns.saturating_add(1);
    }

    pub(crate) fn terminal_tool_submitted(self, outcome: ToolResult) -> AgentLoopOutcome {
        AgentLoopOutcome {
            kind: AgentLoopOutcomeKind::TerminalToolSubmitted { outcome },
            final_conversation_messages: self.conversation_messages,
            total_token_count: self.total_token_count,
        }
    }

    pub(crate) fn loop_failed(self, error: EngineError) -> AgentLoopOutcome {
        self.loop_failed_summary(error.to_string())
    }

    pub(crate) fn loop_failed_summary(self, error_summary: String) -> AgentLoopOutcome {
        AgentLoopOutcome {
            kind: AgentLoopOutcomeKind::LoopFailed { error_summary },
            final_conversation_messages: self.conversation_messages,
            total_token_count: self.total_token_count,
        }
    }

    pub(crate) fn turn_limit_reached(&self) -> bool {
        self.completed_turns >= self.tool_call_limit.max(1)
    }

    pub(crate) async fn drain_notifications(&mut self) -> Vec<SystemNotification> {
        let notifications = self.notifier.drain().await;
        if !notifications.is_empty() {
            self.conversation_messages
                .push(AgentLoopMessage::UserMessage(notification_message(
                    &notifications,
                )));
        }
        notifications
    }

    pub(crate) async fn teardown_background(&self, reason: &str) {
        if let Some(teardown) = &self.background_teardown {
            teardown.teardown(reason).await;
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct AgentLoopRunServices {
    subagent_sessions: SubagentToolService,
    workflow_sessions: WorkflowToolService,
    command_sessions: CommandSessionToolService,
    notifier: NotificationService,
    background_teardown: Option<BackgroundTeardownService>,
}

impl AgentLoopRunServices {
    pub(crate) fn inert() -> Self {
        Self {
            subagent_sessions: SubagentToolService::new(
                |_agent_run_id| async {},
                |_agent_run_id, _reason| async { false },
                || async { 0 },
                |_reason| async {},
            ),
            workflow_sessions: WorkflowToolService::new(|_workflow| async {}),
            command_sessions: CommandSessionToolService::new(
                |_command_session_id, _sandbox_id| async {},
            ),
            notifier: NotificationService::new(),
            background_teardown: None,
        }
    }

    pub(crate) fn from_background(
        background: &BackgroundManagers,
        notifier: NotificationService,
    ) -> Self {
        Self {
            subagent_sessions: background.subagent_tool_service(),
            workflow_sessions: background.workflow_tool_service(),
            command_sessions: background.command_session_tool_service(),
            notifier,
            background_teardown: Some(background.teardown_service()),
        }
    }
}

fn notification_message(notifications: &[SystemNotification]) -> Message {
    Message {
        role: MessageRole::User,
        content: notifications
            .iter()
            .map(|notification| ContentBlock::SystemNotification {
                text: notification.message.clone(),
            })
            .collect(),
    }
}

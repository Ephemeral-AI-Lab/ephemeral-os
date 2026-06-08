//! Query-loop context and event-source seam.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_runner::AgentRunRecordHandle;
use eos_audit::AuditSink;
use eos_llm_client::LlmRequest;
use eos_tools::{ExecutionMetadata, ToolName, ToolRegistry, ToolResult};
use eos_types::{AgentRunId, TaskId};
use futures::Stream;
use serde::{Deserialize, Serialize};

use crate::{
    AgentRunCancellation, EngineError, EngineRunHandles, ForegroundExecutor, NotificationRule,
    NotificationService, PromptReportRecorder, StreamEvent,
};

/// The engine stream returned by one model turn.
pub type EngineStream = Pin<Box<dyn Stream<Item = Result<StreamEvent, EngineError>> + Send>>;

/// A per-agent stream source. Production adapts an `LlmClient`; tests can replay
/// scripted engine events while still exercising the real loop.
#[async_trait]
pub trait EventSource: Send + Sync {
    /// Open one model turn for `request`.
    ///
    /// # Errors
    /// Returns [`EngineError`] for request construction or stream setup faults.
    async fn stream(&self, request: &LlmRequest) -> Result<EngineStream, EngineError>;
}

/// Why the query loop exited.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum QueryExitReason {
    /// A successful terminal tool was submitted.
    ToolStop,
    /// The hard no-terminal ceiling was reached.
    TerminalNotSubmitted,
}

/// Mutable state for one agent query loop.
#[derive(Clone)]
pub struct QueryContext {
    /// Immutable tool registry for this agent.
    pub tool_registry: Arc<ToolRegistry>,
    /// Working directory.
    pub cwd: PathBuf,
    /// Resolved model key.
    pub model: String,
    /// Request system prompt.
    pub system_prompt: String,
    /// Completion token cap.
    pub max_tokens: u32,
    /// Configured tool-call limit.
    pub tool_call_limit: u32,
    /// Agent profile name.
    pub agent_name: String,
    /// Agent run id.
    pub agent_run_id: AgentRunId,
    /// Owning task id, when known.
    pub task_id: Option<TaskId>,
    /// Counted tool calls.
    pub tool_calls_used: u32,
    /// Counted text-only turns without terminal submission.
    pub text_only_no_terminal_turns: u32,
    /// Tool execution metadata cloned per call.
    pub tool_metadata: ExecutionMetadata,
    /// Terminal tools visible to this agent.
    pub terminal_tools: BTreeSet<ToolName>,
    /// Loop exit reason.
    pub exit_reason: Option<QueryExitReason>,
    /// Terminal tool outcome, when one was produced.
    pub submission_outcome: Option<ToolResult>,
    /// Event-source seam.
    pub event_source: Option<Arc<dyn EventSource>>,
    /// Optional prompt-report recorder.
    pub prompt_report: Option<PromptReportRecorder>,
    /// Optional file-backed message-record handle for this agent run.
    pub message_record: Option<AgentRunRecordHandle>,
    /// Declarative notification rules.
    pub notification_rules: Vec<Arc<dyn NotificationRule>>,
    /// Fire-once notification names already emitted.
    pub notification_fired: BTreeSet<String>,
    /// The run-local notification sink the loop drains at the top of every turn
    /// (anchor §6). Shares its queue with the tool/heartbeat sink — the
    /// instance-identity invariant (anchor §7): if these diverge it compiles and
    /// silently delivers nothing.
    pub notifier: NotificationService,
    /// The run's cooperative cancellation token. The loop polls
    /// [`AgentRunCancellation::is_cancel_requested`] at each turn boundary and
    /// stops starting new work once a cancel has been requested.
    pub cancellation: AgentRunCancellation,
    /// The run's foreground cancelable-effect registry (inline child runs and
    /// registered resources), reached by the cancellation path.
    pub foreground: Arc<ForegroundExecutor>,
    /// Optional agent-core observability sink.
    pub audit: Option<Arc<dyn AuditSink>>,
    /// The explicit run handles the engine-driven advisor dispatch needs to
    /// spawn a child `run_agent`. `None` in tests that never exercise
    /// `ask_advisor`; the gate itself reads only the transcript, never these
    /// handles.
    pub run_handles: Option<EngineRunHandles>,
}

impl std::fmt::Debug for QueryContext {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("QueryContext")
            .field("cwd", &self.cwd)
            .field("model", &self.model)
            .field("max_tokens", &self.max_tokens)
            .field("tool_call_limit", &self.tool_call_limit)
            .field("agent_name", &self.agent_name)
            .field("agent_run_id", &self.agent_run_id)
            .field("task_id", &self.task_id)
            .field("tool_calls_used", &self.tool_calls_used)
            .field(
                "text_only_no_terminal_turns",
                &self.text_only_no_terminal_turns,
            )
            .field("terminal_tools", &self.terminal_tools)
            .field("exit_reason", &self.exit_reason)
            .finish_non_exhaustive()
    }
}

impl QueryContext {
    /// Count one tool call without exposing counter arithmetic to the loop.
    pub(crate) fn record_tool_call(&mut self) {
        self.tool_calls_used = self.tool_calls_used.saturating_add(1);
    }

    /// Count one assistant turn that did not submit a terminal tool.
    pub(crate) fn record_text_only_turn(&mut self) {
        self.text_only_no_terminal_turns = self.text_only_no_terminal_turns.saturating_add(1);
    }

    /// Set the loop exit reason.
    pub(crate) fn set_exit_reason(&mut self, reason: QueryExitReason) {
        self.exit_reason = Some(reason);
    }

    /// Store the successful submission outcome observed by dispatch.
    pub(crate) fn set_submission_outcome(&mut self, outcome: Option<ToolResult>) {
        self.submission_outcome = outcome;
    }

    /// Whether a fire-once notification has already fired.
    pub(crate) fn notification_was_fired(&self, name: &str) -> bool {
        self.notification_fired.contains(name)
    }

    /// Mark a fire-once notification as fired.
    pub(crate) fn mark_notification_fired(&mut self, name: String) {
        self.notification_fired.insert(name);
    }
}

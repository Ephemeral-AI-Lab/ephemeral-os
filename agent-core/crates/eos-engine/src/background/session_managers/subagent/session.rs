use std::sync::Arc;

use eos_tools::ToolResult;
use eos_types::{AgentRunId, JsonObject, SubagentSessionId};
use tokio::task::AbortHandle;

use super::super::{BackgroundSession, BackgroundSessionStatus};
use crate::runtime::AgentRunControl;

/// One tracked subagent run owned by an agent run's background session runtime.
#[derive(Debug, Clone)]
pub(in crate::background) struct SubagentSession {
    id: SubagentSessionId,
    agent_run_control: Arc<AgentRunControl>,
    agent_run_abort: AbortHandle,
    tool_input: JsonObject,
    status: BackgroundSessionStatus,
    result: Option<ToolResult>,
}

pub(super) struct SubagentCancelAction {
    pub(super) agent_run_control: Arc<AgentRunControl>,
    pub(super) agent_run_abort: AbortHandle,
}

impl SubagentSession {
    pub(super) fn running(
        id: SubagentSessionId,
        agent_run_control: Arc<AgentRunControl>,
        agent_run_abort: AbortHandle,
        tool_input: JsonObject,
    ) -> Self {
        Self {
            id,
            agent_run_control,
            agent_run_abort,
            tool_input,
            status: BackgroundSessionStatus::Running,
            result: None,
        }
    }

    pub(super) fn agent_run_id(&self) -> &AgentRunId {
        self.agent_run_control.agent_run_id()
    }

    pub(super) fn tool_input(&self) -> &JsonObject {
        &self.tool_input
    }

    pub(super) const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    pub(super) fn result(&self) -> Option<&ToolResult> {
        self.result.as_ref()
    }

    pub(super) fn cancel(&mut self, reason: &str) -> Option<SubagentCancelAction> {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return None;
        }
        self.status = BackgroundSessionStatus::Cancelled;
        self.result = Some(
            ToolResult::error(format!("Background subagent cancelled: {reason}"))
                .meta("subagent_cancelled", serde_json::json!(true)),
        );
        Some(SubagentCancelAction {
            agent_run_control: self.agent_run_control.clone(),
            agent_run_abort: self.agent_run_abort.clone(),
        })
    }

    pub(super) fn settle(
        &mut self,
        status: BackgroundSessionStatus,
        result: ToolResult,
    ) -> Option<ToolResult> {
        if status.precedence() > self.status.precedence() {
            self.status = status;
            self.result = Some(result);
        }
        self.result.clone()
    }
}

impl BackgroundSession for SubagentSession {
    type Id = SubagentSessionId;

    fn id(&self) -> &Self::Id {
        &self.id
    }
}

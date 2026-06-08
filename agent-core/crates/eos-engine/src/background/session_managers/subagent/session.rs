use eos_tools::ToolResult;
use eos_types::{AgentRunId, SubagentSessionId};

use super::super::{BackgroundSession, BackgroundSessionStatus};

/// One tracked subagent run owned by an agent run's background session runtime.
#[derive(Debug, Clone)]
pub(in crate::background) struct SubagentSession {
    id: SubagentSessionId,
    agent_run_id: AgentRunId,
    agent_name: String,
    status: BackgroundSessionStatus,
    result: Option<ToolResult>,
}

impl SubagentSession {
    pub(super) fn tracked(
        id: SubagentSessionId,
        agent_run_id: AgentRunId,
        agent_name: String,
    ) -> Self {
        Self {
            id,
            agent_run_id,
            agent_name,
            status: BackgroundSessionStatus::Running,
            result: None,
        }
    }

    pub(super) fn agent_run_id(&self) -> &AgentRunId {
        &self.agent_run_id
    }

    pub(super) fn agent_name(&self) -> &str {
        &self.agent_name
    }

    pub(super) const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    pub(super) fn result(&self) -> Option<&ToolResult> {
        self.result.as_ref()
    }

    pub(super) fn cancel(&mut self, reason: &str) -> bool {
        if !matches!(self.status, BackgroundSessionStatus::Running) {
            return false;
        }
        self.status = BackgroundSessionStatus::Cancelled;
        self.result = Some(
            ToolResult::error(format!("Background subagent cancelled: {reason}"))
                .meta("subagent_cancelled", serde_json::json!(true)),
        );
        true
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

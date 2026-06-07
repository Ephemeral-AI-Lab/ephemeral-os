//! [`SubagentLane`] (spec §9.1) — the per-agent-run subagent ledger. Subagents
//! are created by agent-core and run as local Tokio tasks, so this lane owns the
//! local id sequence and the abort backstop. The non-cloneable driver abort now
//! rides inside the first-class [`SubagentHandle`] on the record (no side map).

use std::collections::HashMap;

use eos_tools::ToolResult;
use eos_types::{AgentRunId, JsonObject, SubagentSessionId};
use serde_json::json;
use tokio::task::AbortHandle;

use super::BackgroundTaskStatus;

/// The first-class handle for one tracked subagent run (spec §9.1).
#[derive(Debug, Clone)]
pub struct SubagentHandle {
    /// Agent-facing supervisor id (`subagent_<n>`).
    pub subagent_session_id: SubagentSessionId,
    /// The subagent's own agent-run id (its ephemeral `AgentRunControl`).
    pub sub_agent_run_id: AgentRunId,
    /// Abort handle for the running driver task — a runaway-driver backstop only.
    /// What unwedges the parent terminal is the *settle* (the record leaves
    /// `Running`); `abort()` merely stops a child that ignores cancellation.
    pub driver_abort: AbortHandle,
}

/// One tracked subagent run: handle plus tool input, status, and result.
#[derive(Debug, Clone)]
pub struct SubagentRecord {
    /// The subagent handle (ids + driver abort).
    pub handle: SubagentHandle,
    /// Original tool input.
    pub tool_input: JsonObject,
    /// Current status.
    pub status: BackgroundTaskStatus,
    /// Final result.
    pub result: Option<ToolResult>,
}

impl SubagentRecord {
    /// Cancel this record in-place (shared status/result transition). The driver
    /// abort is a separate side effect owned by the lane.
    fn cancel(&mut self, reason: &str) -> bool {
        if !matches!(self.status, BackgroundTaskStatus::Running) {
            return false;
        }
        self.status = BackgroundTaskStatus::Cancelled;
        self.result = Some(
            ToolResult::error(format!("Background subagent cancelled: {reason}"))
                .meta("subagent_cancelled", json!(true)),
        );
        true
    }
}

/// The per-agent-run subagent ledger.
#[derive(Debug, Default)]
pub struct SubagentLane {
    next_session_seq: u64,
    records: HashMap<SubagentSessionId, SubagentRecord>,
}

impl SubagentLane {
    /// Mint the next stable `subagent_<n>` id (the lane is the sole minter).
    pub(crate) fn mint_id(&mut self) -> SubagentSessionId {
        self.next_session_seq = self.next_session_seq.saturating_add(1);
        match format!("subagent_{}", self.next_session_seq).parse() {
            Ok(id) => id,
            Err(_) => unreachable!("generated subagent ids are non-empty"),
        }
    }

    /// Insert a freshly-launched running record.
    pub(crate) fn insert(&mut self, handle: SubagentHandle, tool_input: JsonObject) {
        self.records.insert(
            handle.subagent_session_id.clone(),
            SubagentRecord {
                handle,
                tool_input,
                status: BackgroundTaskStatus::Running,
                result: None,
            },
        );
    }

    /// Borrow a subagent record.
    #[must_use]
    pub(crate) fn get(&self, subagent_session_id: &SubagentSessionId) -> Option<&SubagentRecord> {
        self.records.get(subagent_session_id)
    }

    /// Settle a record to a terminal status with its result, gated by the
    /// precedence latch: a higher-precedence outcome wins, so a finish racing a
    /// cancel resolves to `Completed`.
    pub(crate) fn settle(
        &mut self,
        subagent_session_id: &SubagentSessionId,
        status: BackgroundTaskStatus,
        result: ToolResult,
    ) {
        if let Some(record) = self.records.get_mut(subagent_session_id) {
            if status.precedence() > record.status.precedence() {
                record.status = status;
                record.result = Some(result);
            }
        }
    }

    /// Cancel one tracked subagent (settle `Cancelled` + abort its driver).
    /// Returns `false` for an unknown / already-settled session.
    pub(crate) fn cancel(&mut self, subagent_session_id: &SubagentSessionId, reason: &str) -> bool {
        let Some(record) = self.records.get_mut(subagent_session_id) else {
            return false;
        };
        if record.cancel(reason) {
            record.handle.driver_abort.abort();
            true
        } else {
            false
        }
    }

    /// Cancel every still-running subagent (settle `Cancelled` + abort drivers),
    /// used by parent-exit teardown so a live or phantom subagent never wedges the
    /// agent's terminal.
    pub(crate) fn cancel_all(&mut self, reason: &str) {
        for record in self.records.values_mut() {
            if record.cancel(reason) {
                record.handle.driver_abort.abort();
            }
        }
    }

    /// Count still-running subagents.
    #[must_use]
    pub(crate) fn count_running(&self) -> usize {
        self.records
            .values()
            .filter(|record| matches!(record.status, BackgroundTaskStatus::Running))
            .count()
    }
}

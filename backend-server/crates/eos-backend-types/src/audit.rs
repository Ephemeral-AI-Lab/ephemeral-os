//! Observability and daemon-audit correlation DTOs.
//!
//! These rows are persistence-internal (they are never serialized as public API
//! bodies), so they derive serde only, not `JsonSchema` — and they carry the
//! daemon-supplied [`CallerId`], which has no `JsonSchema` impl. Model-facing
//! ([`ToolUseId`]) and daemon-facing ([`InvocationId`]) identities are stored as
//! distinct fields and are never reused as one another (AC7).

use serde::{Deserialize, Serialize};

use eos_protocol::CallerId;
use eos_types::{AgentRunId, InvocationId, RequestId, SandboxId, TaskId, ToolUseId, UtcDateTime};

/// Origin of an observability event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObsSource {
    /// Emitted by the agent-core engine / tool path (model-facing).
    Engine,
    /// Pulled from the sandbox daemon audit stream (daemon-facing).
    Daemon,
}

impl ObsSource {
    /// The stable TEXT-column form (matches the `snake_case` serde form).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Engine => "engine",
            Self::Daemon => "daemon",
        }
    }

    /// Parse the TEXT-column form, returning `None` for an unknown value.
    #[must_use]
    pub fn from_db(value: &str) -> Option<Self> {
        match value {
            "engine" => Some(Self::Engine),
            "daemon" => Some(Self::Daemon),
            _ => None,
        }
    }
}

/// One persisted observability event (`obs_event`).
///
/// Daemon audit rows with no matching correlation bridge persist with null
/// model-facing ids (`request_id` / `task_id` / `agent_run_id` / `tool_use_id`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObsEvent {
    /// Autoincrement row id; `None` before insert, `Some` once persisted.
    pub id: Option<i64>,
    /// Owning request id, when known.
    pub request_id: Option<RequestId>,
    /// Owning task id, when known.
    pub task_id: Option<TaskId>,
    /// Owning agent-run id, when known.
    pub agent_run_id: Option<AgentRunId>,
    /// Model/tool-call id, when known. Never set from `sandbox_invocation_id`.
    pub tool_use_id: Option<ToolUseId>,
    /// Daemon/sandbox-call id, when known.
    pub sandbox_invocation_id: Option<InvocationId>,
    /// Sandbox the event relates to, when known.
    pub sandbox_id: Option<SandboxId>,
    /// Event origin.
    pub source: ObsSource,
    /// Event classification (free TEXT, e.g. an `unmatched` marker).
    pub kind: String,
    /// Event-specific payload.
    pub payload: serde_json::Value,
    /// When the event was recorded.
    pub created_at: UtcDateTime,
}

/// The persisted bridge joining a model/tool call to its daemon/sandbox
/// invocation (`sandbox_call_correlation`).
///
/// `tool_use_id` is the model identifier; `sandbox_invocation_id` is the daemon
/// identifier; the join key is `(sandbox_id, caller_id, sandbox_invocation_id)`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SandboxCallCorrelation {
    /// Owning request id.
    pub request_id: RequestId,
    /// Owning task id.
    pub task_id: TaskId,
    /// Owning agent-run id.
    pub agent_run_id: AgentRunId,
    /// Model/tool-call id.
    pub tool_use_id: ToolUseId,
    /// Daemon/sandbox-call id.
    pub sandbox_invocation_id: InvocationId,
    /// Daemon-supplied caller identity.
    pub caller_id: CallerId,
    /// Sandbox the call ran in.
    pub sandbox_id: SandboxId,
    /// When the bridge row was recorded (before the daemon request is sent).
    pub created_at: UtcDateTime,
}

/// Per-sandbox audit pull cursor (`audit_cursor`).
///
/// `boot_epoch_id` detects daemon restarts so a sequence regression alone is not
/// trusted (AC8).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AuditCursor {
    /// Sandbox the cursor tracks (primary key).
    pub sandbox_id: SandboxId,
    /// Highest daemon audit sequence consumed for the current epoch.
    pub last_seq: i64,
    /// Daemon boot epoch the cursor is anchored to.
    pub boot_epoch_id: i64,
    /// Sequence below which events were lost (set on epoch change), if any.
    pub lost_before_seq: Option<i64>,
    /// Count of audit events the daemon dropped for this sandbox in the current boot
    /// epoch (the daemon ring counter restarts on reboot, so this is not cumulative
    /// across epochs).
    pub dropped_count: u64,
    /// When the cursor was last advanced.
    pub updated_at: UtcDateTime,
}

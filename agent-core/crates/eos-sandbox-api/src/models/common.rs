use std::collections::BTreeMap;

use eos_types::{InvocationId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use super::identity::SandboxCaller;

/// High-level execution intent for a foreground sandbox tool call.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    /// Read-only operation (no mutations).
    ReadOnly,
    /// Operation permitted to mutate the workspace.
    WriteAllowed,
    /// Workspace lifecycle operation (e.g. isolated enter/exit).
    Lifecycle,
}

impl Intent {
    /// The wire string for this intent (the serde `snake_case` form), used when
    /// building a daemon payload by hand.
    #[must_use]
    pub const fn as_wire(self) -> &'static str {
        match self {
            Self::ReadOnly => "read_only",
            Self::WriteAllowed => "write_allowed",
            Self::Lifecycle => "lifecycle",
        }
    }
}

/// Which workspace a result was produced against. The hand-written daemon
/// response parsers preserve the daemon's `workspace` / `workspace_mode` field
/// when present and fall back to `Ephemeral` for older responses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Workspace {
    /// The shared ephemeral workspace (default).
    #[default]
    Ephemeral,
    /// An agent's private isolated workspace.
    Isolated,
}

/// Base request shape for audit-aware public sandbox operations. Embedded as a
/// flattened field on each verb request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SandboxRequestBase {
    /// Caller identity for audit and routing.
    pub caller: SandboxCaller,
    /// Human-readable operation description; falls back via [`Self::description_or`].
    #[serde(default)]
    pub description: String,
    /// Optional in-flight correlation id, reused by the transport when present.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub invocation_id: Option<InvocationId>,
}

impl SandboxRequestBase {
    /// `description` if non-empty, else `fallback` (mirrors `default_description`).
    #[must_use]
    pub fn description_or(&self, fallback: &str) -> String {
        if self.description.is_empty() {
            fallback.to_owned()
        } else {
            self.description.clone()
        }
    }
}

/// Base result shape for public sandbox operations. Embedded as a flattened
/// field on each verb result.
///
/// `success` has **no** `Default`/construction shortcut on the parse path — the
/// hand-written parsers set it explicitly with a fail-closed `false` default.
/// `workspace` is decoded from daemon mode fields when present.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SandboxResultBase {
    /// Whether the operation succeeded.
    pub success: bool,
    /// Workspace the result was produced against.
    #[serde(default)]
    pub workspace: Workspace,
    /// Operation timings, keys normalized to plain strings.
    #[serde(default)]
    pub timings: BTreeMap<String, f64>,
    /// Structured conflict details, when the operation conflicted.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict: Option<ConflictInfo>,
    /// Free-text conflict reason, when present.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_reason: Option<String>,
    /// Paths the operation changed (empty for read-only verbs).
    #[serde(default)]
    pub changed_paths: Vec<String>,
    /// Untyped daemon error payload, when the operation failed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonObject>,
}

/// Structured guarded-operation conflict details.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ConflictInfo {
    /// Conflict reason code (e.g. `aborted_overlap`, `rejected`).
    pub reason: String,
    /// The conflicting file, when the conflict is path-scoped.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conflict_file: Option<String>,
    /// User-facing conflict message.
    #[serde(default)]
    pub message: String,
}

impl ConflictInfo {
    /// A rejected-operation conflict (no specific file).
    #[must_use]
    pub fn rejected(reason: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            reason: reason.into(),
            conflict_file: None,
            message: message.into(),
        }
    }

    /// An overlapping-write conflict scoped to `path`.
    #[must_use]
    pub fn overlap(path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            reason: "aborted_overlap".to_owned(),
            conflict_file: Some(path.into()),
            message: message.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn caller(caller_id: &str) -> SandboxCaller {
        SandboxCaller {
            caller_id: caller_id.to_owned(),
            run_id: String::new(),
            agent_run_id: String::new(),
            task_id: String::new(),
            request_id: String::new(),
            attempt_id: String::new(),
            workflow_id: String::new(),
            tool_id: None,
        }
    }

    #[test]
    fn description_or_falls_back_when_empty() {
        let base = SandboxRequestBase {
            caller: caller("a"),
            description: String::new(),
            invocation_id: None,
        };
        assert_eq!(base.description_or("write x"), "write x");
        let base = SandboxRequestBase {
            caller: caller("a"),
            description: "custom".to_owned(),
            invocation_id: None,
        };
        assert_eq!(base.description_or("write x"), "custom");
    }
}

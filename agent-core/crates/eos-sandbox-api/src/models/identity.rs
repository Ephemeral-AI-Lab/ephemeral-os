use eos_types::{AgentRunId, AttemptId, JsonObject, RequestId, TaskId, ToolUseId, WorkflowId};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Caller identity threaded onto every audit-aware request.
///
/// `caller_id` is the daemon-facing sandbox identity. Agent/workflow/task
/// metadata stays in this host-side typed API and is projected only where a
/// higher-level audit consumer needs it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SandboxCaller {
    /// Neutral sandbox caller identity. In agent-core it is derived from the
    /// current agent identity, but the daemon contract does not name agents.
    pub caller_id: String,
    /// Run id (required-empty compatibility field).
    #[serde(default)]
    pub run_id: String,
    /// Agent-run id as a raw wire field (required-empty). Use [`Self::agent_run`]
    /// for the validated typed form.
    #[serde(default)]
    pub agent_run_id: String,
    /// Task id as a raw wire field (required-empty). Use [`Self::task`].
    #[serde(default)]
    pub task_id: String,
    /// Request id as a raw wire field (optional-empty). Use [`Self::request`].
    #[serde(default)]
    pub request_id: String,
    /// Attempt id as a raw wire field (optional-empty). Use [`Self::attempt`].
    #[serde(default)]
    pub attempt_id: String,
    /// Workflow id as a raw wire field (optional-empty). Use [`Self::workflow`].
    #[serde(default)]
    pub workflow_id: String,
    /// Tool-use id, stored already-typed; omitted from the wire when unset.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_id: Option<ToolUseId>,
}

impl SandboxCaller {
    /// Daemon-facing nested `caller` block (mirrors Python `audit_fields()`), a
    /// **payload-shape** method, not audit logic.
    ///
    /// The four required ids are always present (even empty); optional ids are
    /// omitted when empty. This is only the nested block — the full envelope
    /// identity (top-level `caller_id` + this block + optional `invocation_id`) is
    /// built by `tool_api::parse::daemon_request_identity_fields`.
    pub(crate) fn identity_block(&self) -> JsonObject {
        let mut block = JsonObject::new();
        block.insert(
            "caller_id".to_owned(),
            Value::String(self.caller_id.clone()),
        );
        block.insert("run_id".to_owned(), Value::String(self.run_id.clone()));
        block.insert(
            "agent_run_id".to_owned(),
            Value::String(self.agent_run_id.clone()),
        );
        block.insert("task_id".to_owned(), Value::String(self.task_id.clone()));
        if !self.request_id.is_empty() {
            block.insert(
                "request_id".to_owned(),
                Value::String(self.request_id.clone()),
            );
        }
        if !self.attempt_id.is_empty() {
            block.insert(
                "attempt_id".to_owned(),
                Value::String(self.attempt_id.clone()),
            );
        }
        if !self.workflow_id.is_empty() {
            block.insert(
                "workflow_id".to_owned(),
                Value::String(self.workflow_id.clone()),
            );
        }
        if let Some(tool_id) = &self.tool_id {
            block.insert("tool_id".to_owned(), Value::String(tool_id.to_string()));
        }
        block
    }

    /// The typed agent-run id, or `None` when the raw field is empty.
    #[must_use]
    pub fn agent_run(&self) -> Option<AgentRunId> {
        self.agent_run_id.parse().ok()
    }

    /// The typed task id, or `None` when the raw field is empty.
    #[must_use]
    pub fn task(&self) -> Option<TaskId> {
        self.task_id.parse().ok()
    }

    /// The typed request id, or `None` when the raw field is empty.
    #[must_use]
    pub fn request(&self) -> Option<RequestId> {
        self.request_id.parse().ok()
    }

    /// The typed attempt id, or `None` when the raw field is empty.
    #[must_use]
    pub fn attempt(&self) -> Option<AttemptId> {
        self.attempt_id.parse().ok()
    }

    /// The typed workflow id, or `None` when the raw field is empty.
    #[must_use]
    pub fn workflow(&self) -> Option<WorkflowId> {
        self.workflow_id.parse().ok()
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

    // AC-sandbox-api-02: a serialized caller never emits `tool_name`; an unset
    // `tool_id` is omitted and a set one round-trips.
    #[test]
    fn caller_omits_tool_name_and_optional_tool_id() {
        let value = serde_json::to_value(caller("agent-1")).expect("serialize caller");
        let object = value.as_object().expect("caller is an object");
        assert!(
            !object.contains_key("tool_name"),
            "tool_name was removed (GC-01)"
        );
        assert!(
            !object.contains_key("tool_id"),
            "unset tool_id is omitted from the wire"
        );

        let mut with_tool = caller("agent-1");
        with_tool.tool_id = Some("tool-9".parse().expect("non-empty tool id"));
        let value = serde_json::to_value(&with_tool).expect("serialize caller");
        assert_eq!(value["tool_id"], serde_json::json!("tool-9"));
        let back: SandboxCaller = serde_json::from_value(value).expect("roundtrip caller");
        assert_eq!(back, with_tool);
    }

    // AC-sandbox-api-04 (identity-block portion): the nested `caller` block
    // always carries the four required ids (even empty) and omits empty optional
    // ids. The fixture uses caller_id == agent_run_id (production shape) to catch
    // accidental newtype coupling while keeping the fields distinct.
    #[test]
    fn identity_block_required_empty_and_optional_omitted() {
        let mut c = caller("agent-run-7");
        c.agent_run_id = "agent-run-7".to_owned(); // equal to caller_id, distinct field
        let block = c.identity_block();

        for required in ["caller_id", "run_id", "agent_run_id", "task_id"] {
            assert!(block.contains_key(required), "required key {required}");
        }
        assert_eq!(block["caller_id"], serde_json::json!("agent-run-7"));
        assert_eq!(block["agent_run_id"], serde_json::json!("agent-run-7"));
        assert_eq!(block["run_id"], serde_json::json!(""));
        assert_eq!(block["task_id"], serde_json::json!(""));

        for optional in ["request_id", "attempt_id", "workflow_id", "tool_id"] {
            assert!(
                !block.contains_key(optional),
                "empty optional key {optional} must be omitted"
            );
        }

        // Populated optionals appear; tool_id uses its inner string.
        c.request_id = "req-1".to_owned();
        c.tool_id = Some("tool-2".parse().expect("non-empty tool id"));
        let block = c.identity_block();
        assert_eq!(block["request_id"], serde_json::json!("req-1"));
        assert_eq!(block["tool_id"], serde_json::json!("tool-2"));
        assert!(!block.contains_key("attempt_id"));
    }

    #[test]
    fn typed_accessors_validate_non_empty() {
        let mut c = caller("agent-1");
        assert_eq!(c.agent_run(), None);
        assert_eq!(c.task(), None);
        c.agent_run_id = "ar-1".to_owned();
        c.task_id = "task-1".to_owned();
        assert_eq!(c.agent_run().expect("typed").as_str(), "ar-1");
        assert_eq!(c.task().expect("typed").as_str(), "task-1");
    }
}

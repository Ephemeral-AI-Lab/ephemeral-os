use eos_types::{InvocationId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::common::Intent;

/// One tool invocation routed through a workspace pipeline.
///
/// `invocation_id` is the typed [`InvocationId`]; [`Self::from_payload`] parses
/// it at the boundary and is fallible (a spec-sanctioned tightening of the
/// Python path, which tolerated an empty id string).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ToolCallRequest {
    /// Correlation id for this invocation.
    pub invocation_id: InvocationId,
    /// Calling agent id.
    pub agent_id: String,
    /// Tool verb (e.g. `read_file`).
    pub verb: String,
    /// Execution intent.
    pub intent: Intent,
    /// Untyped tool arguments.
    pub args: JsonObject,
    /// Whether the invocation runs in the background.
    #[serde(default)]
    pub background: bool,
}

impl ToolCallRequest {
    /// Build the daemon payload (mirrors `to_payload`).
    #[must_use]
    pub fn to_payload(&self) -> JsonObject {
        let mut payload = JsonObject::new();
        payload.insert(
            "invocation_id".to_owned(),
            Value::String(self.invocation_id.to_string()),
        );
        payload.insert("agent_id".to_owned(), Value::String(self.agent_id.clone()));
        payload.insert("verb".to_owned(), Value::String(self.verb.clone()));
        payload.insert(
            "intent".to_owned(),
            Value::String(self.intent.as_wire().to_owned()),
        );
        payload.insert("args".to_owned(), Value::Object(self.args.clone()));
        payload.insert("background".to_owned(), Value::Bool(self.background));
        payload
    }

    /// Parse a daemon payload (mirrors `from_payload`). Fails when `args` is
    /// present but not an object, or when `invocation_id` is missing/empty.
    pub fn from_payload(payload: &JsonObject) -> Result<Self, crate::error::SandboxApiError> {
        let args = match payload.get("args") {
            None | Some(Value::Null) => JsonObject::new(),
            Some(Value::Object(map)) => map.clone(),
            Some(_) => {
                return Err(crate::error::SandboxApiError::decode(
                    "tool-call payload args must be an object",
                ));
            }
        };
        let invocation_raw = payload
            .get("invocation_id")
            .and_then(Value::as_str)
            .unwrap_or("");
        let invocation_id = invocation_raw.parse::<InvocationId>().map_err(|_| {
            crate::error::SandboxApiError::decode("tool-call payload missing invocation_id")
        })?;
        let intent = match payload.get("intent").and_then(Value::as_str) {
            None | Some("") => Intent::ReadOnly,
            Some("read_only") => Intent::ReadOnly,
            Some("write_allowed") => Intent::WriteAllowed,
            Some("lifecycle") => Intent::Lifecycle,
            Some(other) => {
                return Err(crate::error::SandboxApiError::decode(format!(
                    "unknown tool-call intent: {other}"
                )));
            }
        };
        Ok(Self {
            invocation_id,
            agent_id: payload
                .get("agent_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            verb: payload
                .get("verb")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            intent,
            args,
            background: payload
                .get("background")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        })
    }
}

#[cfg(test)]
mod tests {
    use eos_types::JsonObject;
    use serde_json::Value;

    use super::*;
    use crate::models::Intent;

    #[test]
    fn tool_call_request_payload_roundtrip() {
        let mut args = JsonObject::new();
        args.insert("path".to_owned(), Value::String("a.txt".to_owned()));
        let request = ToolCallRequest {
            invocation_id: "inv-1".parse().expect("non-empty"),
            agent_id: "agent-1".to_owned(),
            verb: "read_file".to_owned(),
            intent: Intent::WriteAllowed,
            args,
            background: true,
        };
        let payload = request.to_payload();
        assert_eq!(payload["intent"], serde_json::json!("write_allowed"));
        assert_eq!(payload["background"], serde_json::json!(true));
        let back = ToolCallRequest::from_payload(&payload).expect("parse payload");
        assert_eq!(back, request);
    }

    #[test]
    fn tool_call_request_rejects_non_object_args_and_empty_invocation() {
        let mut bad_args = JsonObject::new();
        bad_args.insert(
            "invocation_id".to_owned(),
            Value::String("inv-1".to_owned()),
        );
        bad_args.insert("args".to_owned(), Value::String("not-an-object".to_owned()));
        assert!(ToolCallRequest::from_payload(&bad_args).is_err());

        let empty_inv = JsonObject::new();
        assert!(ToolCallRequest::from_payload(&empty_inv).is_err());
    }
}

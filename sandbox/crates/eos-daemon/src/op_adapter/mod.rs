//! Daemon JSON operation adapters.
//!
//! These modules parse wire `args`, call the owning service/crate, and shape
//! the stable response object. Domain lifecycle policy should live below this
//! adapter layer.

pub(crate) mod checkpoint;
pub(crate) mod command;
pub(crate) mod control;
pub(crate) mod files;
pub(crate) mod isolation;
pub(crate) mod plugin;
pub(crate) mod workspace_run;

use eos_operation::{OperationEnvelope, ResponseMeta};
use serde::Serialize;
use serde_json::Value;

pub(crate) fn to_wire_value(output: impl serde::Serialize) -> Value {
    serde_json::to_value(output).expect("operation output DTO serializes to JSON")
}

pub(crate) fn ok_envelope(output: impl Serialize) -> Value {
    let output = to_wire_value(output);
    if is_operation_envelope(&output) {
        return output;
    }
    to_wire_value(OperationEnvelope::ok(output, ResponseMeta::default()))
}

pub(crate) fn is_operation_envelope(value: &Value) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    let Some("ok" | "running" | "rejected" | "cancelled" | "timed_out" | "error") =
        object.get("status").and_then(Value::as_str)
    else {
        return false;
    };
    object.contains_key("meta") && (object.contains_key("result") || object.contains_key("error"))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::ok_envelope;

    #[test]
    fn ok_envelope_wraps_result_without_flattening_domain_status() {
        let value = ok_envelope(json!({"success": true, "status": "committed"}));

        assert_eq!(value["status"], "ok");
        assert_eq!(value["result"]["status"], "committed");
        assert_eq!(value["result"]["success"], true);
        assert!(value.get("meta").is_some());
    }
}

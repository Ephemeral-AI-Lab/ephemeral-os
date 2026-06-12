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
    to_wire_value(OperationEnvelope::ok(
        to_wire_value(output),
        ResponseMeta::default(),
    ))
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

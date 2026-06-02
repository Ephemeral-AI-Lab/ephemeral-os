//! Neutral engine tool-row constructors (GC-audit-05).
//!
//! These build the `engine.tool.*` audit payloads that `engine/audit/stream.py`
//! produces, but over **borrowed neutral inputs** owned by this crate /
//! `eos-types` — never the engine's `ToolExecution*` stream events. The dispatch
//! over those stream types and the node-id precedence logic
//! (`_node_from_stream`) stay in `eos-engine`, which resolves the fields and
//! calls these constructors. That keeps `eos-audit` from importing `eos-engine`
//! (no `eos-audit → eos-engine → eos-audit` cycle).

use eos_types::{Clock, JsonObject, JsonValue, ToolUseId};
use serde_json::{Number, Value};

use crate::event::{AuditEvent, AuditSource};
use crate::node::AuditNode;
use crate::redaction;

/// Engine event type: a tool began executing.
pub const TOOL_STARTED: &str = "engine.tool.started";
/// Engine event type: a tool finished successfully.
pub const TOOL_COMPLETED: &str = "engine.tool.completed";
/// Engine event type: a tool finished with an error.
pub const TOOL_FAILED: &str = "engine.tool.failed";

/// `Some(s)` → a `JSON` string; `None` → `JSON` null (the key is always present,
/// matching Python's always-present `tool_name`/`tool_use_id` payload keys).
fn opt_str_value(value: Option<&str>) -> JsonValue {
    value.map_or(Value::Null, |s| Value::String(s.to_owned()))
}

/// `usize` byte count → a `JSON` number.
fn size_value(size: usize) -> JsonValue {
    Value::Number(Number::from(size))
}

/// Build the `engine.tool.started` row from a pre-resolved node and tool input.
///
/// `tool_name`/`tool_use_id` are read from `node` (the engine resolves them into
/// the node before calling, per the parity nuance in impl-eos-audit.md §6).
#[must_use]
pub fn tool_started(node: AuditNode, input: &JsonObject, clock: &dyn Clock) -> AuditEvent {
    let input = Value::Object(input.clone());
    let mut payload = JsonObject::new();
    payload.insert(
        "tool_name".to_owned(),
        opt_str_value(node.tool_name.as_deref()),
    );
    payload.insert(
        "tool_use_id".to_owned(),
        opt_str_value(node.tool_use_id.as_ref().map(ToolUseId::as_str)),
    );
    payload.insert("status".to_owned(), Value::String("ok".to_owned()));
    payload.insert("input_shape".to_owned(), redaction::shape(&input));
    payload.insert(
        "input_redacted".to_owned(),
        redaction::redacted_shape(&input),
    );
    payload.insert(
        "input_digest".to_owned(),
        Value::String(redaction::digest(&input)),
    );
    payload.insert(
        "input_bytes".to_owned(),
        size_value(redaction::encoded_size(&input)),
    );
    AuditEvent::new(AuditSource::Engine, TOOL_STARTED, node, payload, clock)
}

/// Build the `engine.tool.completed`/`engine.tool.failed` row.
///
/// The event type is `TOOL_FAILED` when `is_error`, else `TOOL_COMPLETED`.
/// `error_kind` is the explicit `JSON` null (not omitted) on success, matching
/// the Python row. `metadata`'s inner `timings` is remapped to `domain_timings`
/// while the row also carries a separate empty `timings: {}`.
#[must_use]
pub fn tool_completed(
    node: AuditNode,
    output: &str,
    is_error: bool,
    is_terminal: bool,
    metadata: &JsonObject,
    clock: &dyn Clock,
) -> AuditEvent {
    let output = Value::String(output.to_owned());
    let mut payload = JsonObject::new();
    payload.insert(
        "tool_name".to_owned(),
        opt_str_value(node.tool_name.as_deref()),
    );
    payload.insert(
        "tool_use_id".to_owned(),
        opt_str_value(node.tool_use_id.as_ref().map(ToolUseId::as_str)),
    );
    payload.insert(
        "status".to_owned(),
        Value::String(if is_error { "error" } else { "ok" }.to_owned()),
    );
    payload.insert(
        "error_kind".to_owned(),
        if is_error {
            Value::String("tool_result_error".to_owned())
        } else {
            Value::Null
        },
    );
    payload.insert("output_shape".to_owned(), redaction::shape(&output));
    payload.insert(
        "output_digest".to_owned(),
        Value::String(redaction::digest(&output)),
    );
    payload.insert(
        "output_bytes".to_owned(),
        size_value(redaction::encoded_size(&output)),
    );
    payload.insert("is_error".to_owned(), Value::Bool(is_error));
    payload.insert("is_terminal".to_owned(), Value::Bool(is_terminal));
    payload.insert("metadata".to_owned(), audit_metadata_from(metadata));
    payload.insert("timings".to_owned(), Value::Object(JsonObject::new()));
    let event_type = if is_error {
        TOOL_FAILED
    } else {
        TOOL_COMPLETED
    };
    AuditEvent::new(AuditSource::Engine, event_type, node, payload, clock)
}

/// Copy `metadata`, popping `timings` and re-adding an object value as
/// `domain_timings` (a non-object `timings` is dropped). Mirrors Python
/// `_audit_metadata_from_stream_metadata`.
fn audit_metadata_from(metadata: &JsonObject) -> JsonValue {
    let mut out = metadata.clone();
    if let Some(Value::Object(timings)) = out.remove("timings") {
        out.insert("domain_timings".to_owned(), Value::Object(timings));
    }
    Value::Object(out)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use eos_types::{TestClock, UtcDateTime};
    use pretty_assertions::assert_eq;
    use serde_json::json;

    fn fixed_clock() -> TestClock {
        TestClock::new(UtcDateTime::parse_rfc3339("2026-06-02T19:47:00Z").unwrap())
    }

    fn golden_node() -> AuditNode {
        AuditNode::builder()
            .request_id("req-1".parse().unwrap())
            .tool_name("read_file")
            .tool_use_id("tu-1".parse().unwrap())
            .build()
    }

    // AC-audit-05 (golden): a built tool-started + tool-completed event serializes
    // to the byte-exact authored Rust target shape — including schema_version: 1,
    // the single deterministic ts, timings: {}, and the domain_timings remap.
    #[test]
    fn tool_rows_match_golden_jsonl() {
        let clock = fixed_clock();

        let mut input = JsonObject::new();
        input.insert("path".to_owned(), json!("README.md"));
        input.insert("limit".to_owned(), json!(5));
        let started = tool_started(golden_node(), &input, &clock);
        let started_line = serde_json::to_string(&started).unwrap();
        assert_eq!(
            started_line,
            r#"{"schema_version":1,"source":"engine","type":"engine.tool.started","node":{"request_id":"req-1","tool_name":"read_file","tool_use_id":"tu-1"},"payload":{"input_bytes":30,"input_digest":"sha256:eae28db122d085e97660cdaca234aa8fe92b68793608afd3c5391d10ab70a5d3","input_redacted":{"limit":"<redacted>","path":"<redacted>"},"input_shape":{"limit":"int","path":"str"},"status":"ok","tool_name":"read_file","tool_use_id":"tu-1"},"ts":"2026-06-02T19:47:00Z"}"#
        );

        let mut metadata = JsonObject::new();
        metadata.insert("timings".to_owned(), json!({"queued_ms": 1.5}));
        metadata.insert("request_id".to_owned(), json!("req-1"));
        let completed = tool_completed(golden_node(), "ok", false, false, &metadata, &clock);
        let completed_line = serde_json::to_string(&completed).unwrap();
        assert_eq!(
            completed_line,
            r#"{"schema_version":1,"source":"engine","type":"engine.tool.completed","node":{"request_id":"req-1","tool_name":"read_file","tool_use_id":"tu-1"},"payload":{"error_kind":null,"is_error":false,"is_terminal":false,"metadata":{"domain_timings":{"queued_ms":1.5},"request_id":"req-1"},"output_bytes":4,"output_digest":"sha256:c48b5b1a9776c84602de2306d7903a7241158a5077e7a8519af75c33441b8334","output_shape":"str","status":"ok","timings":{},"tool_name":"read_file","tool_use_id":"tu-1"},"ts":"2026-06-02T19:47:00Z"}"#
        );
    }

    // The is_error == true branch selects TOOL_FAILED with status "error" and the
    // "tool_result_error" error_kind (the branch the golden does not exercise).
    #[test]
    fn tool_failed_branch_selection() {
        let event = tool_completed(
            golden_node(),
            "boom",
            true,
            false,
            &JsonObject::new(),
            &fixed_clock(),
        );
        assert_eq!(event.event_type, TOOL_FAILED);
        assert_eq!(event.payload["status"], json!("error"));
        assert_eq!(event.payload["error_kind"], json!("tool_result_error"));
        assert_eq!(event.payload["is_error"], json!(true));
    }

    // A non-object `timings` value is dropped, not remapped to domain_timings.
    #[test]
    fn non_object_timings_dropped() {
        let mut metadata = JsonObject::new();
        metadata.insert("timings".to_owned(), json!(42));
        metadata.insert("kept".to_owned(), json!("yes"));
        let event = tool_completed(golden_node(), "ok", false, false, &metadata, &fixed_clock());
        let meta = event.payload["metadata"].as_object().unwrap();
        assert!(!meta.contains_key("timings"));
        assert!(!meta.contains_key("domain_timings"));
        assert_eq!(meta["kept"], json!("yes"));
    }
}

//! Normalized audit/observability contract for collectors.
//!
//! This module intentionally owns only the normalized row shape used by
//! collectors. It does not own producer policy, sinks, daemon rings, tracing, or
//! report rendering.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

use serde::{Deserialize, Serialize};

/// Normalized contract schema.
pub const SCHEMA: &str = "eos.obs.v1";

/// Canonical event name for completed tool calls.
pub const TOOL_CALL_COMPLETED: &str = "tool_call.completed";
/// Canonical event name for completed agent runs.
pub const AGENT_RUN_COMPLETED: &str = "agent_run.completed";
/// Canonical event name for resource samples.
pub const OS_RESOURCE_SAMPLED: &str = "os_resource.sampled";

/// JSON object used by normalized payload sections.
pub type JsonObject = serde_json::Map<String, serde_json::Value>;

/// The source that produced the native event before collector normalization.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ObsSource {
    /// Rust agent control-plane event.
    AgentCore,
    /// Rust sandbox daemon event.
    Sandbox,
}

/// Correlation ids shared by normalized audit/observability rows.
///
/// Every field is optional because a row should carry only ids the producer or
/// collector actually knows. Non-id labels such as `tool_name` belong in
/// `payload`, not here.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObsIds {
    /// Owning request id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    /// Owning task id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    /// Owning agent-run id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_run_id: Option<String>,
    /// Provider/tool-call id used to join agent-core and sandbox rows.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_use_id: Option<String>,
    /// Owning sandbox id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sandbox_id: Option<String>,
}

/// A normalized audit/observability row consumed by collectors and reports.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObsEnvelope {
    /// Contract schema tag.
    pub schema: String,
    /// Native source that produced the row.
    pub source: ObsSource,
    /// Canonical event type.
    #[serde(rename = "type")]
    pub event_type: String,
    /// Common correlation ids.
    #[serde(default)]
    pub ids: ObsIds,
    /// Event-specific sections such as `tool_call`, `occ`, or `os_resource`.
    #[serde(default)]
    pub payload: JsonObject,
    /// Sandbox ring sequence, when the source has one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seq: Option<i64>,
    /// Sandbox ring lane, when the source has one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lane: Option<String>,
}

impl ObsEnvelope {
    /// Build a normalized row with the default schema.
    #[must_use]
    pub fn new(source: ObsSource, event_type: impl Into<String>) -> Self {
        let event_type = event_type.into();
        Self {
            schema: SCHEMA.to_owned(),
            source,
            event_type: canonical_event_type(&event_type).to_owned(),
            ids: ObsIds::default(),
            payload: JsonObject::new(),
            seq: None,
            lane: None,
        }
    }

    /// Set the row ids.
    #[must_use]
    pub fn with_ids(mut self, ids: ObsIds) -> Self {
        self.ids = ids;
        self
    }

    /// Set the row payload.
    #[must_use]
    pub fn with_payload(mut self, payload: JsonObject) -> Self {
        self.payload = payload;
        self
    }

    /// Set sandbox ring metadata.
    #[must_use]
    pub fn with_ring_metadata(mut self, seq: i64, lane: impl Into<String>) -> Self {
        self.seq = Some(seq);
        self.lane = Some(lane.into());
        self
    }
}

/// Return the canonical event type for a native or legacy event type.
#[must_use]
pub fn canonical_event_type(event_type: &str) -> &str {
    match event_type {
        "tool_call.finished" => TOOL_CALL_COMPLETED,
        other => other,
    }
}

/// Parse one normalized JSONL row.
///
/// The parser accepts any valid JSON object matching [`ObsEnvelope`]. Native
/// sandbox rows should be normalized by the collector before calling this.
///
/// # Errors
///
/// Returns a JSON parse error when the line is not a valid normalized row.
pub fn from_jsonl_line(line: &str) -> Result<ObsEnvelope, serde_json::Error> {
    let mut row: ObsEnvelope = serde_json::from_str(line)?;
    row.event_type = canonical_event_type(&row.event_type).to_owned();
    Ok(row)
}

/// Serialize one normalized row to a JSONL line.
///
/// # Errors
///
/// Returns a JSON serialization error if the payload contains a non-serializable
/// value.
pub fn to_jsonl_line(row: &ObsEnvelope) -> Result<String, serde_json::Error> {
    let mut line = serde_json::to_string(row)?;
    line.push('\n');
    Ok(line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    #[test]
    fn envelope_serializes_minimal_shape() {
        let row = ObsEnvelope::new(ObsSource::Sandbox, "tool_call.finished").with_ids(ObsIds {
            tool_use_id: Some("toolu-1".to_owned()),
            ..ObsIds::default()
        });

        let value = serde_json::to_value(row).expect("serialize row");

        assert_eq!(value["schema"], json!(SCHEMA));
        assert_eq!(value["source"], json!("sandbox"));
        assert_eq!(value["type"], json!(TOOL_CALL_COMPLETED));
        assert_eq!(value["ids"], json!({"tool_use_id": "toolu-1"}));
        assert_eq!(value["payload"], json!({}));
        assert!(value.get("seq").is_none());
        assert!(value.get("lane").is_none());
    }

    #[test]
    fn payload_keeps_native_sections() {
        let mut payload = JsonObject::new();
        payload.insert(
            "tool_call".to_owned(),
            json!({"tool_name": "exec_command", "duration_ms": 12.5}),
        );
        let row = ObsEnvelope::new(ObsSource::AgentCore, TOOL_CALL_COMPLETED)
            .with_payload(payload)
            .with_ring_metadata(42, "normal");

        let value = serde_json::to_value(row).expect("serialize row");

        assert_eq!(
            value["payload"]["tool_call"],
            json!({"tool_name": "exec_command", "duration_ms": 12.5})
        );
        assert_eq!(value["seq"], json!(42));
        assert_eq!(value["lane"], json!("normal"));
    }

    #[test]
    fn jsonl_helpers_round_trip_and_canonicalize_alias() {
        let input = r#"{"schema":"eos.obs.v1","source":"sandbox","type":"tool_call.finished","ids":{},"payload":{}}"#;

        let row = from_jsonl_line(input).expect("parse row");
        let line = to_jsonl_line(&row).expect("serialize row");
        let reparsed: Value = serde_json::from_str(&line).expect("parse json line");

        assert_eq!(row.event_type, TOOL_CALL_COMPLETED);
        assert!(line.ends_with('\n'));
        assert_eq!(reparsed["type"], json!(TOOL_CALL_COMPLETED));
    }
}

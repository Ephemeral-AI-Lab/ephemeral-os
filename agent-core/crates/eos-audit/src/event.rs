//! [`AuditEvent`] + [`AuditSource`] — the structured audit row.
//!
//! The serialized row adds a top-level `schema_version` (new in the Rust port)
//! and stamps `ts` exactly once from an injected [`Clock`] (GC-audit-01),
//! collapsing Python's double `ts` stamping. The `type` key maps to the Rust
//! field `event_type` (`type` is a keyword).

use eos_types::{Clock, JsonObject, UtcDateTime};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::node::AuditNode;

/// The serialized-row schema version. Bumped only on a wire-shape change.
pub const SCHEMA_VERSION: u32 = 1;

/// The behavior-owning package that emitted an event.
///
/// Serializes to the exact Python `Literal` strings: `"workflow"`, `"engine"`,
/// `"sandbox"`, `"live_e2e"`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum AuditSource {
    /// Workflow-layer emitter.
    Workflow,
    /// Engine-layer emitter (query loop, tool dispatch).
    Engine,
    /// Sandbox-layer emitter (includes plugin tools).
    Sandbox,
    /// Live end-to-end harness emitter.
    #[serde(rename = "live_e2e")]
    LiveE2e,
}

/// A structured audit event emitted by a behavior-owning package.
///
/// Construct via [`AuditEvent::new`] so `ts` is set once from the injected
/// clock. Field order is the wire order; `payload` is always serialized (even
/// when empty) and `correlation_id` is omitted when `None`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[non_exhaustive]
pub struct AuditEvent {
    /// Serialized-row schema version (always `1`).
    pub schema_version: u32,
    /// The emitting package.
    pub source: AuditSource,
    /// The event-type string (e.g. `"engine.tool.started"`).
    #[serde(rename = "type")]
    pub event_type: String,
    /// Correlation envelope of known ids.
    pub node: AuditNode,
    /// Event-specific payload object.
    #[serde(default)]
    pub payload: JsonObject,
    /// Optional cross-event correlation id.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub correlation_id: Option<String>,
    /// Emission timestamp, stamped once from the injected clock.
    pub ts: UtcDateTime,
}

impl AuditEvent {
    /// Build an event, stamping `ts` from `clock` (GC-audit-01: single source).
    #[must_use]
    pub fn new(
        source: AuditSource,
        event_type: impl Into<String>,
        node: AuditNode,
        payload: JsonObject,
        clock: &dyn Clock,
    ) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            source,
            event_type: event_type.into(),
            node,
            payload,
            correlation_id: None,
            ts: clock.now(),
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use eos_types::TestClock;
    use serde_json::{json, Value};

    fn fixed_clock() -> TestClock {
        TestClock::new(UtcDateTime::parse_rfc3339("2026-06-02T19:47:00Z").unwrap())
    }

    // AC-audit-01: round-trip serde with skip-if-none and the exact Python keys
    // (`type`, nested `node`, nested `payload`).
    #[test]
    fn node_event_serde_roundtrip() {
        let node = AuditNode::builder()
            .request_id("req-1".parse().unwrap())
            .tool_name("read_file")
            .build();
        let mut payload = JsonObject::new();
        payload.insert("status".to_owned(), Value::String("ok".to_owned()));
        let event = AuditEvent::new(
            AuditSource::Engine,
            "engine.tool.started",
            node,
            payload,
            &fixed_clock(),
        );

        let value = serde_json::to_value(&event).unwrap();
        assert_eq!(value["type"], json!("engine.tool.started"));
        assert_eq!(value["schema_version"], json!(1));
        assert!(value.get("node").unwrap().is_object());
        assert_eq!(value["node"]["request_id"], json!("req-1"));
        // skip-if-none: an unset node id is absent, not null.
        assert!(value["node"].get("workflow_id").is_none());
        // correlation_id is None -> omitted entirely.
        assert!(value.get("correlation_id").is_none());
        // payload stays nested under "payload".
        assert_eq!(value["payload"]["status"], json!("ok"));

        let back: AuditEvent = serde_json::from_value(value).unwrap();
        assert_eq!(back, event);
    }

    // AC-audit-02: AuditSource serializes to workflow|engine|sandbox|live_e2e.
    #[test]
    fn source_serde_strings() {
        assert_eq!(
            serde_json::to_value(AuditSource::Workflow).unwrap(),
            json!("workflow")
        );
        assert_eq!(
            serde_json::to_value(AuditSource::Engine).unwrap(),
            json!("engine")
        );
        assert_eq!(
            serde_json::to_value(AuditSource::Sandbox).unwrap(),
            json!("sandbox")
        );
        assert_eq!(
            serde_json::to_value(AuditSource::LiveE2e).unwrap(),
            json!("live_e2e")
        );
        let back: AuditSource = serde_json::from_value(json!("live_e2e")).unwrap();
        assert_eq!(back, AuditSource::LiveE2e);
    }
}

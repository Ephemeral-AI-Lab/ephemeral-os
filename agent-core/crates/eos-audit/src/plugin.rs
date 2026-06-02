//! Plugin audit family (GC-audit-06): the `plugin.*` events + `PluginSection`.
//!
//! The plugin kind is a **payload value**, never part of the event-type string:
//! exactly three fixed types ([`PLUGIN_TOOL_INVOKED`], [`PLUGIN_TOOL_COMPLETED`],
//! [`PLUGIN_ERROR`]), and `plugin_kind` lives in the payload. Only the inner
//! `payload["plugin"]` object is byte-compatible with the daemon's
//! `build_plugin_event` / `PluginSection.as_dict()`; the surrounding
//! [`AuditEvent`](crate::AuditEvent) envelope (`source`, `node`,
//! `schema_version`, single `ts`) is net-new and a deliberate design choice, not
//! Python parity. `source` is [`AuditSource::Sandbox`] because plugin tools run
//! inside the sandbox; there is no `Plugin` variant and we do not add one.
//!
//! `eos-plugin-catalog` owns wrapping a tool's execute and supplying the
//! [`PluginSection`] (duration, status, `error_kind`); this crate owns the
//! section shape and the [`plugin_event`] constructor.

use eos_types::{Clock, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::{AuditEvent, AuditSource};
use crate::node::AuditNode;

/// Plugin event type: a plugin tool was invoked.
pub const PLUGIN_TOOL_INVOKED: &str = "plugin.tool_invoked";
/// Plugin event type: a plugin tool completed.
pub const PLUGIN_TOOL_COMPLETED: &str = "plugin.tool_completed";
/// Plugin event type: a plugin tool errored.
pub const PLUGIN_ERROR: &str = "plugin.error";

/// Default `plugin_kind` when a manifest declares none (Python's
/// `manifest.kind or "custom"`).
const DEFAULT_PLUGIN_KIND: &str = "custom";

/// Payload section for `plugin.*` events, serialized nested under `"plugin"`.
///
/// `plugin_id` and `plugin_kind` are always emitted (Python's
/// `required=("plugin_id", "plugin_kind")`); the rest are omitted when `None`.
/// [`PluginSection::default`] yields `plugin_kind = "custom"`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[non_exhaustive]
pub struct PluginSection {
    /// Plugin identifier (manifest name); always emitted.
    pub plugin_id: String,
    /// Plugin kind; always emitted, defaulting to `"custom"`.
    pub plugin_kind: String,
    /// Declared plugin version.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub plugin_version: Option<String>,
    /// The plugin tool's name.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub plugin_tool_name: Option<String>,
    /// Request payload byte size.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub request_bytes: Option<u64>,
    /// Response payload byte size.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub response_bytes: Option<u64>,
    /// Execution duration in milliseconds.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub duration_ms: Option<f64>,
    /// Outcome status (`"ok"`/`"error"`).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub status: Option<String>,
    /// Error kind (the failing exception's type name, set by the caller).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error_kind: Option<String>,
    /// Hash of the plugin message.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub message_hash: Option<String>,
    /// Workspace handle id the tool ran against.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub workspace_handle_id: Option<String>,
    /// Agent id that invoked the tool.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub agent_id: Option<String>,
    /// Peak resident memory in bytes.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub peak_resident_bytes: Option<u64>,
}

impl Default for PluginSection {
    fn default() -> Self {
        Self {
            plugin_id: String::new(),
            plugin_kind: DEFAULT_PLUGIN_KIND.to_owned(),
            plugin_version: None,
            plugin_tool_name: None,
            request_bytes: None,
            response_bytes: None,
            duration_ms: None,
            status: None,
            error_kind: None,
            message_hash: None,
            workspace_handle_id: None,
            agent_id: None,
            peak_resident_bytes: None,
        }
    }
}

/// Build a `plugin.*` [`AuditEvent`] wrapping `section` under `payload["plugin"]`.
///
/// `event_type` must be one of [`PLUGIN_TOOL_INVOKED`], [`PLUGIN_TOOL_COMPLETED`],
/// [`PLUGIN_ERROR`]. `source` is [`AuditSource::Sandbox`]. Section fields with no
/// node home (`agent_id`, `workspace_handle_id`) stay inside `payload["plugin"]`
/// and are not promoted into `node`.
#[must_use]
pub fn plugin_event(
    event_type: &str,
    section: &PluginSection,
    node: AuditNode,
    clock: &dyn Clock,
) -> AuditEvent {
    let section_value = serde_json::to_value(section).expect("plugin section serializes to json");
    let mut payload = JsonObject::new();
    payload.insert("plugin".to_owned(), section_value);
    AuditEvent::new(AuditSource::Sandbox, event_type, node, payload, clock)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use eos_types::{TestClock, UtcDateTime};

    fn fixed_clock() -> TestClock {
        TestClock::new(UtcDateTime::parse_rfc3339("2026-06-02T19:47:00Z").unwrap())
    }

    // AC-audit-08: the three plugin event types are exactly the fixed strings,
    // none encodes the kind, and plugin_kind appears only in the payload with the
    // "custom" fallback when a manifest omits it.
    #[test]
    fn kind_is_payload_only() {
        assert_eq!(PLUGIN_TOOL_INVOKED, "plugin.tool_invoked");
        assert_eq!(PLUGIN_TOOL_COMPLETED, "plugin.tool_completed");
        assert_eq!(PLUGIN_ERROR, "plugin.error");

        // Default supplies the "custom" fallback kind.
        let section = PluginSection {
            plugin_id: "p1".to_owned(),
            ..Default::default()
        };
        assert_eq!(section.plugin_kind, "custom");

        let clock = fixed_clock();
        for event_type in [PLUGIN_TOOL_INVOKED, PLUGIN_TOOL_COMPLETED, PLUGIN_ERROR] {
            let event = plugin_event(event_type, &section, AuditNode::default(), &clock);
            // The type is exactly the fixed string — no plugin.<kind>.* shape.
            assert_eq!(event.event_type, event_type);
            assert!(!event.event_type.contains("custom"));
            assert!(!event.event_type.starts_with("plugin.custom"));
            // plugin_kind lives only in payload["plugin"].
            let plugin = event.payload["plugin"].as_object().unwrap();
            assert_eq!(plugin["plugin_kind"], serde_json::json!("custom"));
            assert_eq!(plugin["plugin_id"], serde_json::json!("p1"));
        }
    }

    // The inner payload["plugin"] object omits None fields but always keeps
    // plugin_id and plugin_kind (parity with PluginSection.as_dict()).
    #[test]
    fn section_omits_none_keeps_required() {
        let section = PluginSection {
            plugin_id: "p1".to_owned(),
            plugin_kind: "lsp".to_owned(),
            plugin_tool_name: Some("fmt".to_owned()),
            ..Default::default()
        };
        let value = serde_json::to_value(&section).unwrap();
        let obj = value.as_object().unwrap();
        assert_eq!(obj["plugin_id"], serde_json::json!("p1"));
        assert_eq!(obj["plugin_kind"], serde_json::json!("lsp"));
        assert_eq!(obj["plugin_tool_name"], serde_json::json!("fmt"));
        // Unset optionals are omitted, not null.
        assert!(!obj.contains_key("duration_ms"));
        assert!(!obj.contains_key("status"));
    }
}

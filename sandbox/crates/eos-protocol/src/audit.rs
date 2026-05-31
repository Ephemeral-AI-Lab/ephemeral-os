//! Audit event schema (channel A: the daemon ring-buffer's typed `*Section`
//! dataclasses). This is the movable, pure schema; the impure
//! `safe_emit`/`safe_record_phase` bridges stay daemon-side.
//!
//! Invariant: serialization reproduces Python `_drop_none` — a field whose value
//! is `None` is OMITTED; a field with a non-None default IS emitted. Modeled
//! here as `Option<T>` with `skip_serializing_if = "Option::is_none"` for the
//! droppable fields, and plain fields (with `Default`) for the always-emitted
//! defaults (`workspace_mode`, the three `orphan_*_count`).
//! `// PORT backend/src/sandbox/daemon/audit_schema.py:17-25 — _drop_none`
//! `// PORT backend/src/sandbox/daemon/audit_buffer.py:19-65`

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Ring-buffer pull/snapshot schema tag (lane changes => v2 break).
/// `// PORT backend/src/sandbox/daemon/audit_buffer.py:57`
pub const SCHEMA_VERSION: &str = "sandbox.daemon.audit.pull.v1";

/// Default ring capacity (events). `// PORT audit_buffer.py:64`
pub const DEFAULT_MAX_EVENTS: u64 = 50_000;
/// Default ring capacity (bytes, 8 MiB). `// PORT audit_buffer.py:65`
pub const DEFAULT_MAX_BYTES: u64 = 8 * 1024 * 1024;
/// Pressure threshold for the edge-triggered emitter. `// PORT audit_buffer.py:91,134`
pub const DEFAULT_PRESSURE_THRESHOLD: f64 = 0.8;

/// Audit lane. Storage order is `_LANES`; eviction tries `sample` first and
/// `critical` last (`_EVICTION_ORDER`).
/// `// PORT backend/src/sandbox/daemon/audit_buffer.py:59-62`
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Lane {
    /// Survives sample/normal pressure; evicted last.
    Critical,
    /// Default lane.
    Normal,
    /// Evicted first under pressure.
    Sample,
}

impl Lane {
    /// Storage/iteration order (`_LANES`).
    pub const STORAGE_ORDER: [Lane; 3] = [Lane::Critical, Lane::Normal, Lane::Sample];
    /// Eviction order (`_EVICTION_ORDER`): sample first, critical last.
    pub const EVICTION_ORDER: [Lane; 3] = [Lane::Sample, Lane::Normal, Lane::Critical];
}

/// `daemon` section. `// PORT audit_schema.py:28-39`
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DaemonSection {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub boot_epoch_id: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pressure: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retained_events: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retained_bytes: Option<i64>,
}

/// `layer_stack` section. `// PORT audit_schema.py:42-63`
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LayerStackSection {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_step: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lease_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub owner_request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub manifest_version: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub manifest_root_hash: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub layer_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lease_wait_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lock_wait_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lease_hold_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prepare_snapshot_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub squash_trigger_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub squash_input_layers: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub squash_result_layers: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub squash_failure_kind: Option<String>,
}

/// `overlay_workspace` section. `workspace_mode` defaults `"ephemeral"` (always
/// emitted). `// PORT audit_schema.py:75-94`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayWorkspaceSection {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
    pub workspace_mode: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_handle_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lease_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub manifest_root_hash: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mount_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cleanup_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scratch_removed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cleanup_failure_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub committed_layer_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub publish_layer_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub changed_path_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upperdir_bytes: Option<i64>,
}

impl Default for OverlayWorkspaceSection {
    fn default() -> Self {
        Self {
            operation_id: None,
            workspace_mode: "ephemeral".to_owned(),
            workspace_handle_id: None,
            lease_id: None,
            manifest_root_hash: None,
            mount_ms: None,
            cleanup_ms: None,
            scratch_removed: None,
            cleanup_failure_kind: None,
            committed_layer_id: None,
            publish_layer_ms: None,
            changed_path_count: None,
            upperdir_bytes: None,
        }
    }
}

/// `isolated_workspace` section. `workspace_mode` defaults `"isolated"`; the
/// three `orphan_*_count` default `0` (all always emitted).
/// `// PORT audit_schema.py:106-130`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IsolatedWorkspaceSection {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
    pub workspace_mode: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_handle_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub holder_pid: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub holder_pid_alive: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cgroup_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cgroup_removed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scratch_removed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upperdir_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upperdir_cap_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_current_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_peak_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cpu_usage_usec_delta: Option<i64>,
    pub orphan_holder_count: i64,
    pub orphan_cgroup_count: i64,
    pub orphan_scratch_count: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sampled_at_monotonic_s: Option<f64>,
}

impl Default for IsolatedWorkspaceSection {
    fn default() -> Self {
        Self {
            operation_id: None,
            workspace_mode: "isolated".to_owned(),
            workspace_handle_id: None,
            agent_id: None,
            holder_pid: None,
            holder_pid_alive: None,
            cgroup_id: None,
            cgroup_removed: None,
            scratch_removed: None,
            upperdir_bytes: None,
            upperdir_cap_bytes: None,
            memory_current_bytes: None,
            memory_peak_bytes: None,
            cpu_usage_usec_delta: None,
            orphan_holder_count: 0,
            orphan_cgroup_count: 0,
            orphan_scratch_count: 0,
            sampled_at_monotonic_s: None,
        }
    }
}

/// `occ` section. `// PORT audit_schema.py:142-164`
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OccSection {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_step: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub changeset_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub changed_path_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub transaction_lock_wait_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prepare_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub apply_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub commit_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub committed_layer_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub publish_layer_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub committed_layer_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflict_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflict_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflict_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_manifest_version: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_manifest_version: Option<i64>,
}

/// `plugin` section. `plugin_id`/`plugin_kind` are required (always present).
/// `// PORT audit_schema.py:174-193`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginSection {
    pub plugin_id: String,
    pub plugin_kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub plugin_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub plugin_tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub response_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message_hash: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_handle_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub peak_resident_bytes: Option<i64>,
}

/// `background_tool` section. `background_task_id` required.
/// `// PORT audit_schema.py:203-220`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BackgroundToolSection {
    pub background_task_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uptime_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cancel_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery_latency_ms: Option<f64>,
}

/// `tool_call` section. `tool_use_id`/`tool_name` required.
/// `// PORT audit_schema.py:232-250`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCallSection {
    pub tool_use_id: String,
    pub tool_name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_handle_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit_status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_in: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_out: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase_totals_rollup: Option<std::collections::BTreeMap<String, f64>>,
}

/// `os_resource` section. `sampled_at_monotonic_s` required.
/// `// PORT audit_schema.py:262-277`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OsResourceSection {
    pub sampled_at_monotonic_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rss_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cpu_user_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cpu_system_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cpu_throttled_us: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub io_read_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub io_write_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub io_read_ops: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub io_write_ops: Option<i64>,
}

/// Wrap a section into the channel-A event envelope:
/// `{"type": <event_type>, "payload": {<section_key>: <section>}}`.
/// `// PORT backend/src/sandbox/daemon/audit_schema.py — build_*_event`
pub fn build_event(event_type: &str, section_key: &str, section: Value) -> Value {
    serde_json::json!({
        "type": event_type,
        "payload": { section_key: section },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isolated_default_drops_none_keeps_defaults() {
        // Doc §1.1 ground truth: only the four non-None defaults survive.
        let v = serde_json::to_value(IsolatedWorkspaceSection::default()).unwrap();
        assert_eq!(
            v,
            serde_json::json!({
                "orphan_cgroup_count": 0,
                "orphan_holder_count": 0,
                "orphan_scratch_count": 0,
                "workspace_mode": "isolated"
            })
        );
    }

    #[test]
    fn daemon_section_drops_none() {
        let s = DaemonSection {
            pid: Some(42),
            boot_epoch_id: Some(123),
            ..Default::default()
        };
        assert_eq!(
            serde_json::to_value(&s).unwrap(),
            serde_json::json!({"boot_epoch_id": 123, "pid": 42})
        );
    }

    #[test]
    fn event_payloads_match_fixture_embedded_events() {
        // From audit_pull_two_events.json: a daemon.started with {pid:1} and a
        // tool_call.started with {tool_use_id, tool_name}.
        let daemon_evt = build_event(
            "daemon.started",
            "daemon",
            serde_json::to_value(DaemonSection {
                pid: Some(1),
                ..Default::default()
            })
            .unwrap(),
        );
        assert_eq!(
            daemon_evt,
            serde_json::json!({"type":"daemon.started","payload":{"daemon":{"pid":1}}})
        );

        let tool_evt = build_event(
            "tool_call.started",
            "tool_call",
            serde_json::to_value(ToolCallSection {
                tool_use_id: "t".to_owned(),
                tool_name: "read_file".to_owned(),
                agent_id: None,
                workspace_mode: None,
                workspace_handle_id: None,
                phase: None,
                duration_ms: None,
                total_ms: None,
                exit_status: None,
                bytes_in: None,
                bytes_out: None,
                phase_totals_rollup: None,
            })
            .unwrap(),
        );
        assert_eq!(
            tool_evt,
            serde_json::json!({
                "type":"tool_call.started",
                "payload":{"tool_call":{"tool_use_id":"t","tool_name":"read_file"}}
            })
        );
    }

    #[test]
    fn lane_wire_and_orders() {
        assert_eq!(
            serde_json::to_value(Lane::Critical).unwrap(),
            Value::String("critical".to_owned())
        );
        assert_eq!(
            Lane::STORAGE_ORDER,
            [Lane::Critical, Lane::Normal, Lane::Sample]
        );
        assert_eq!(
            Lane::EVICTION_ORDER,
            [Lane::Sample, Lane::Normal, Lane::Critical]
        );
    }
}

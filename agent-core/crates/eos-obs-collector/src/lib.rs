//! Reader-side normalization for Rust audit/observability consumers.
//!
//! Producers keep their local mechanics: agent-core writes normalized JSONL and
//! the sandbox daemon exposes its bounded native ring. This crate is the small
//! collector boundary that turns both inputs into [`eos_obs_contract::ObsEnvelope`]
//! rows for future runner gates.

#![forbid(unsafe_code)]

mod gates;

use eos_obs_contract::{from_jsonl_line, JsonObject, ObsEnvelope, ObsIds, ObsSource};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use gates::{
    evaluate_runner_gate_batches, evaluate_runner_gates, RunnerCorrectnessEvidence,
    RunnerGateBatchInput, RunnerGateFailure, RunnerGateFailureKind, RunnerGateInput,
    RunnerGateMetrics, RunnerGateReport, RunnerGateSettings,
};

/// A sandbox pull response normalized for runner consumption.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SandboxPullBatch {
    /// Normalized rows from the response's `events` array.
    pub rows: Vec<ObsEnvelope>,
    /// Cursor and bounded-ring loss metadata reported with the pull.
    pub loss: SandboxAuditLoss,
}

/// Counted loss and cursor metadata from the daemon audit ring.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxAuditLoss {
    /// Cursor returned by `api.audit.pull` after this pull.
    pub cursor_after_seq: Option<i64>,
    /// First retained sequence when older events were evicted.
    pub lost_before_seq: Option<i64>,
    /// Total events dropped by the bounded ring.
    pub dropped_event_count: Option<i64>,
}

impl SandboxAuditLoss {
    /// Return true when the bounded audit surface reported any counted loss.
    #[must_use]
    pub fn has_counted_loss(&self) -> bool {
        self.lost_before_seq.is_some_and(|seq| seq > 0)
            || self.dropped_event_count.is_some_and(|count| count > 0)
    }

    /// Merge multiple sandbox loss records into one runner-facing summary.
    #[must_use]
    pub fn merge<'a>(losses: impl IntoIterator<Item = &'a Self>) -> Self {
        losses
            .into_iter()
            .fold(Self::default(), |mut merged, loss| {
                merged.cursor_after_seq = max_known(merged.cursor_after_seq, loss.cursor_after_seq);
                merged.lost_before_seq = max_known(merged.lost_before_seq, loss.lost_before_seq);
                merged.dropped_event_count =
                    sum_known(merged.dropped_event_count, loss.dropped_event_count);
                merged
            })
    }
}

/// Errors reported while normalizing collector inputs.
#[derive(Debug, thiserror::Error)]
pub enum ObsCollectorError {
    /// A normalized agent-core JSONL row could not be parsed.
    #[error("agent-core obs JSONL row is invalid")]
    AgentCoreJsonl(#[source] serde_json::Error),
    /// The sandbox pull response did not have the expected schema.
    #[error("sandbox audit response schema mismatch")]
    SandboxSchema,
    /// The sandbox pull response has no `events` array.
    #[error("sandbox audit response is missing events array")]
    MissingEvents,
    /// A sandbox ring event is missing a string `type`.
    #[error("sandbox audit event is missing string type")]
    MissingEventType,
    /// A sandbox ring event has a non-object `payload`.
    #[error("sandbox audit event payload must be an object")]
    NonObjectPayload,
}

/// Parse one agent-core normalized JSONL row.
///
/// # Errors
///
/// Returns [`ObsCollectorError::AgentCoreJsonl`] when the line is not a valid
/// [`ObsEnvelope`] JSON object.
pub fn normalize_agent_core_jsonl_line(line: &str) -> Result<ObsEnvelope, ObsCollectorError> {
    from_jsonl_line(line).map_err(ObsCollectorError::AgentCoreJsonl)
}

/// Normalize a complete `api.audit.pull` response.
///
/// # Errors
///
/// Returns an error when the response schema or event array is invalid, or when
/// any contained event cannot be normalized.
pub fn normalize_sandbox_pull_response(
    response: &Value,
) -> Result<SandboxPullBatch, ObsCollectorError> {
    if response.get("schema").and_then(Value::as_str) != Some(eos_protocol::audit::SCHEMA_VERSION) {
        return Err(ObsCollectorError::SandboxSchema);
    }
    let events = response
        .get("events")
        .and_then(Value::as_array)
        .ok_or(ObsCollectorError::MissingEvents)?;
    let rows = events
        .iter()
        .map(normalize_sandbox_event)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(SandboxPullBatch {
        rows,
        loss: sandbox_loss(response),
    })
}

/// Normalize one daemon audit-ring event.
///
/// # Errors
///
/// Returns an error when `type` is absent or `payload` is not an object.
pub fn normalize_sandbox_event(event: &Value) -> Result<ObsEnvelope, ObsCollectorError> {
    let event_type = event
        .get("type")
        .and_then(Value::as_str)
        .ok_or(ObsCollectorError::MissingEventType)?;
    let payload = match event.get("payload") {
        None => JsonObject::new(),
        Some(Value::Object(payload)) => payload.clone(),
        Some(_) => return Err(ObsCollectorError::NonObjectPayload),
    };

    let mut row = ObsEnvelope::new(ObsSource::Sandbox, event_type)
        .with_ids(sandbox_ids(event, &payload))
        .with_payload(payload);
    if let (Some(seq), Some(lane)) = (
        event.get("seq").and_then(Value::as_i64),
        event.get("lane").and_then(Value::as_str),
    ) {
        row = row.with_ring_metadata(seq, lane);
    }
    Ok(row)
}

fn sandbox_loss(response: &Value) -> SandboxAuditLoss {
    SandboxAuditLoss {
        cursor_after_seq: response
            .get("cursor")
            .and_then(|cursor| cursor.get("after_seq"))
            .and_then(Value::as_i64),
        lost_before_seq: response
            .get("cursor")
            .and_then(|cursor| cursor.get("lost_before_seq"))
            .and_then(Value::as_i64)
            .or_else(|| {
                response
                    .get("buffer")
                    .and_then(|buffer| buffer.get("lost_before_seq"))
                    .and_then(Value::as_i64)
            }),
        dropped_event_count: response
            .get("buffer")
            .and_then(|buffer| buffer.get("dropped_event_count"))
            .and_then(Value::as_i64),
    }
}

fn sandbox_ids(event: &Value, payload: &JsonObject) -> ObsIds {
    ObsIds {
        request_id: string_at(event, &["request_id"])
            .or_else(|| string_at_object(payload, &["request_id"]))
            .or_else(|| string_at_object(payload, &["tool_call", "request_id"])),
        task_id: string_at(event, &["task_id"])
            .or_else(|| string_at_object(payload, &["task_id"]))
            .or_else(|| string_at_object(payload, &["tool_call", "task_id"])),
        agent_run_id: string_at(event, &["agent_run_id"])
            .or_else(|| string_at_object(payload, &["agent_run_id"]))
            .or_else(|| string_at_object(payload, &["tool_call", "agent_run_id"])),
        tool_use_id: string_at(event, &["tool_use_id"])
            .or_else(|| string_at_object(payload, &["tool_use_id"]))
            .or_else(|| string_at_object(payload, &["tool_call", "tool_use_id"]))
            .or_else(|| string_at_object(payload, &["os_resource", "tool_use_id"])),
        sandbox_id: string_at(event, &["sandbox_id"])
            .or_else(|| string_at_object(payload, &["sandbox_id"]))
            .or_else(|| string_at_object(payload, &["tool_call", "sandbox_id"])),
    }
}

fn string_at(value: &Value, path: &[&str]) -> Option<String> {
    let mut current = value;
    for key in path {
        current = current.get(*key)?;
    }
    current.as_str().map(str::to_owned)
}

fn string_at_object(object: &JsonObject, path: &[&str]) -> Option<String> {
    let (first, rest) = path.split_first()?;
    string_at(object.get(*first)?, rest)
}

fn max_known(left: Option<i64>, right: Option<i64>) -> Option<i64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left.max(right)),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

fn sum_known(left: Option<i64>, right: Option<i64>) -> Option<i64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left + right),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eos_obs_contract::{ObsSource, TOOL_CALL_COMPLETED};
    use serde_json::json;

    #[test]
    fn agent_core_jsonl_row_parses_with_contract_helper() {
        let line = r#"{"schema":"eos.obs.v1","source":"agent_core","type":"agent_run.completed","ids":{"agent_run_id":"ar-1"},"payload":{"agent_run":{"status":"ok"}}}"#;

        let row = normalize_agent_core_jsonl_line(line).expect("parse agent-core row");

        assert_eq!(row.source, ObsSource::AgentCore);
        assert_eq!(row.ids.agent_run_id.as_deref(), Some("ar-1"));
        assert_eq!(row.payload["agent_run"]["status"], json!("ok"));
    }

    #[test]
    fn sandbox_pull_normalizes_events_aliases_ids_and_loss() {
        let response = json!({
            "schema": eos_protocol::audit::SCHEMA_VERSION,
            "cursor": {"after_seq": 42, "lost_before_seq": 10},
            "buffer": {"dropped_event_count": 3, "lost_before_seq": 10},
            "events": [{
                "seq": 41,
                "lane": "normal",
                "type": "tool_call.finished",
                "payload": {
                    "tool_call": {
                        "tool_use_id": "toolu-1",
                        "tool_name": "exec_command",
                        "duration_ms": 42.0
                    }
                }
            }]
        });

        let batch = normalize_sandbox_pull_response(&response).expect("normalize pull response");

        assert_eq!(
            batch.loss,
            SandboxAuditLoss {
                cursor_after_seq: Some(42),
                lost_before_seq: Some(10),
                dropped_event_count: Some(3),
            }
        );
        let row = &batch.rows[0];
        assert_eq!(row.source, ObsSource::Sandbox);
        assert_eq!(row.event_type, TOOL_CALL_COMPLETED);
        assert_eq!(row.seq, Some(41));
        assert_eq!(row.lane.as_deref(), Some("normal"));
        assert_eq!(row.ids.tool_use_id.as_deref(), Some("toolu-1"));
        assert_eq!(row.payload["tool_call"]["tool_name"], json!("exec_command"));
    }

    #[test]
    fn sandbox_resource_row_extracts_tool_use_id() {
        let event = json!({
            "seq": 7,
            "lane": "sample",
            "type": "os_resource.sampled",
            "payload": {
                "os_resource": {
                    "tool_use_id": "toolu-2",
                    "sampled_at_monotonic_s": 1.5,
                    "cpu_user_s": 0.2
                }
            }
        });

        let row = normalize_sandbox_event(&event).expect("normalize resource row");

        assert_eq!(row.ids.tool_use_id.as_deref(), Some("toolu-2"));
        assert_eq!(row.payload["os_resource"]["cpu_user_s"], json!(0.2));
    }

    #[test]
    fn sandbox_pull_rejects_wrong_schema() {
        let response = json!({"schema":"wrong","events":[]});

        match normalize_sandbox_pull_response(&response) {
            Err(ObsCollectorError::SandboxSchema) => {}
            other => panic!("expected schema error, got {other:?}"),
        }
    }

    #[test]
    fn sandbox_loss_merge_summarizes_multiple_pulls() {
        let first = SandboxAuditLoss {
            cursor_after_seq: Some(10),
            lost_before_seq: None,
            dropped_event_count: Some(2),
        };
        let second = SandboxAuditLoss {
            cursor_after_seq: Some(14),
            lost_before_seq: Some(7),
            dropped_event_count: Some(3),
        };

        let merged = SandboxAuditLoss::merge([&first, &second]);

        assert_eq!(
            merged,
            SandboxAuditLoss {
                cursor_after_seq: Some(14),
                lost_before_seq: Some(7),
                dropped_event_count: Some(5),
            }
        );
        assert!(merged.has_counted_loss());
    }
}

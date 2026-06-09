use eos_agent_core::{from_jsonl_line, JsonObject, ObsEnvelope, ObsIds, ObsSource};
use serde::{Deserialize, Serialize};
use serde_json::Value;

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

/// Errors reported while normalizing backend observability inputs.
#[derive(Debug, thiserror::Error)]
pub enum ObsNormalizationError {
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
/// Returns [`ObsNormalizationError::AgentCoreJsonl`] when the line is not a valid
/// [`ObsEnvelope`] JSON object.
pub fn normalize_agent_core_jsonl_line(line: &str) -> Result<ObsEnvelope, ObsNormalizationError> {
    from_jsonl_line(line).map_err(ObsNormalizationError::AgentCoreJsonl)
}

/// Normalize a complete `api.audit.pull` response.
///
/// # Errors
///
/// Returns an error when the response schema or event array is invalid, or when
/// any contained event cannot be normalized.
pub fn normalize_sandbox_pull_response(
    response: &Value,
) -> Result<SandboxPullBatch, ObsNormalizationError> {
    if response.get("schema").and_then(Value::as_str) != Some(eos_protocol::audit::SCHEMA_VERSION) {
        return Err(ObsNormalizationError::SandboxSchema);
    }
    let events = response
        .get("events")
        .and_then(Value::as_array)
        .ok_or(ObsNormalizationError::MissingEvents)?;
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
pub fn normalize_sandbox_event(event: &Value) -> Result<ObsEnvelope, ObsNormalizationError> {
    let event_type = event
        .get("type")
        .and_then(Value::as_str)
        .ok_or(ObsNormalizationError::MissingEventType)?;
    let payload = match event.get("payload") {
        None => JsonObject::new(),
        Some(Value::Object(payload)) => payload.clone(),
        Some(_) => return Err(ObsNormalizationError::NonObjectPayload),
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
#[path = "../tests/normalization/mod.rs"]
mod tests;

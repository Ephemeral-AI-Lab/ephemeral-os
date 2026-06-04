//! Shared request argument and JSON conversion helpers.

use eos_layerstack::WorkspaceBinding;
use serde_json::{json, Value};

use crate::error::DaemonError;

pub(crate) fn require_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(DaemonError::InvalidEnvelope(format!("{key} is required")));
    }
    Ok(value)
}

pub(crate) fn binding_to_value(binding: &WorkspaceBinding) -> Result<Value, DaemonError> {
    serde_json::to_value(binding).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))
}

pub(crate) fn timings_to_value_map(
    timings: &std::collections::BTreeMap<String, f64>,
) -> serde_json::Map<String, Value> {
    timings
        .iter()
        .map(|(key, value)| (key.clone(), json!(value)))
        .collect()
}

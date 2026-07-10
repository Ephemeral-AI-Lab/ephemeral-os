//! Pure encoding/validation for the sandbox-scoped daemon readiness handshake.
//! Kept free of Docker and socket concerns so it can be unit tested in isolation.

use serde_json::Value;

/// Treat a daemon response as ready only when it is newline-terminated, valid
/// JSON, reports `status: "ready"`, and echoes the expected sandbox id. A bare
/// TCP connect through Docker's port proxy is not a reliable readiness signal.
pub(crate) fn validate_readiness_response(
    response: &[u8],
    expected_sandbox_id: &str,
) -> Result<(), String> {
    if response.is_empty() {
        return Err("daemon returned an empty response".to_owned());
    }
    if response.last() != Some(&b'\n') {
        return Err("daemon response was not newline-terminated".to_owned());
    }
    let value: Value =
        serde_json::from_slice(response).map_err(|error| format!("decode: {error}"))?;
    if value.get("status").and_then(Value::as_str) != Some("ready") {
        return Err(format!("daemon did not report ready: {value}"));
    }
    let sandbox_id = value.get("sandbox_id").and_then(Value::as_str);
    if sandbox_id != Some(expected_sandbox_id) {
        return Err(format!(
            "daemon sandbox id mismatch: expected {expected_sandbox_id}, got {sandbox_id:?}"
        ));
    }
    Ok(())
}

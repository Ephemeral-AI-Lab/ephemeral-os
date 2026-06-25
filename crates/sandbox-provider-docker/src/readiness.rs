//! Pure encoding/validation for the sandbox-scoped daemon readiness handshake.
//! Kept free of Docker and socket concerns so it can be unit tested in isolation.

use sandbox_protocol::DAEMON_AUTH_FIELD;
use serde_json::Value;

/// Private daemon readiness op served by `sandbox-daemon`. Not part of the public
/// runtime catalog.
pub(crate) const READINESS_OP: &str = "sandbox_daemon_ready";
const READINESS_REQUEST_ID: &str = "docker-readiness";

/// Build the newline-terminated, authenticated readiness request line for the
/// given sandbox. The request is sandbox-scoped so the daemon can confirm it
/// agrees with the expected sandbox id.
pub(crate) fn readiness_request_line(sandbox_id: &str, auth_token: &str) -> Vec<u8> {
    let mut request = serde_json::json!({
        "op": READINESS_OP,
        "request_id": READINESS_REQUEST_ID,
        "scope": { "kind": "sandbox", "sandbox_id": sandbox_id },
        "args": {},
    });
    request[DAEMON_AUTH_FIELD] = Value::String(auth_token.to_owned());
    let mut line = serde_json::to_vec(&request).unwrap_or_default();
    line.push(b'\n');
    line
}

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

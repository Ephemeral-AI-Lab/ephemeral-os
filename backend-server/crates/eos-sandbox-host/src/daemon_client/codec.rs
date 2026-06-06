use std::collections::BTreeMap;
use std::time::Duration;

use eos_sandbox_port::SandboxPortError;
use eos_types::JsonObject;
use serde_json::Value;

use crate::error::SandboxHostError;
use crate::provider::{ExecOpts, RawExecResult};

use super::{EMPTY_RESPONSE_MESSAGE, THIN_CLIENT_IO_FAILED};

pub(crate) fn map_host_error_to_api_error(err: SandboxHostError) -> SandboxPortError {
    match err {
        SandboxHostError::DaemonDispatch { kind, message, .. } => {
            SandboxPortError::transport(Some(kind), message)
        }
        SandboxHostError::ExecFailed { exit_code, message } => SandboxPortError::transport(
            Some("RuntimeExecFailed".to_owned()),
            format!("exit {exit_code}: {message}"),
        ),
        SandboxHostError::DaemonNotReady { .. } => {
            SandboxPortError::transport(Some("RuntimeNotReady".to_owned()), "daemon not ready")
        }
        SandboxHostError::BadResponse { stdout } => SandboxPortError::transport(
            Some("BadRuntimeResponse".to_owned()),
            format!("daemon returned invalid response: {stdout}"),
        ),
        other => SandboxPortError::transport(None, other.to_string()),
    }
}

pub(super) fn new_invocation_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

pub(super) fn exec_opts(cwd: &str, timeout_s: u32) -> ExecOpts {
    ExecOpts {
        cwd: Some(cwd.to_owned()),
        timeout: Some(Duration::from_secs(u64::from(timeout_s))),
    }
}

pub(super) fn without_none(args: JsonObject) -> JsonObject {
    args.into_iter().filter(|(_, v)| !v.is_null()).collect()
}

/// Python `str(x or default)` truthiness: returns `Some(string)` for a truthy
/// value, `None` for a falsy one (null / false / "" / 0 / empty container).
pub(super) fn truthy_to_string(value: &Value) -> Option<String> {
    match value {
        Value::Null | Value::Bool(false) => None,
        Value::Bool(true) => Some("True".to_owned()),
        Value::String(s) if s.is_empty() => None,
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => {
            if n.as_f64() == Some(0.0) {
                None
            } else {
                Some(n.to_string())
            }
        }
        Value::Array(a) if a.is_empty() => None,
        Value::Object(o) if o.is_empty() => None,
        other => Some(other.to_string()),
    }
}

pub(super) fn serialize_envelope(op: &str, invocation_id: &str, args: &JsonObject) -> String {
    let mut envelope = JsonObject::new();
    envelope.insert("op".to_owned(), Value::String(op.to_owned()));
    envelope.insert(
        "invocation_id".to_owned(),
        Value::String(invocation_id.to_owned()),
    );
    envelope.insert("args".to_owned(), Value::Object(args.clone()));
    serde_json::to_string(&Value::Object(envelope)).expect("envelope serializes")
}

pub(super) fn detail(pairs: &[(&str, &str)]) -> JsonObject {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), Value::String((*v).to_owned())))
        .collect()
}

pub(super) fn stderr_or_stdout(result: &RawExecResult) -> String {
    if result.stderr.is_empty() {
        result.stdout.clone()
    } else {
        result.stderr.clone()
    }
}

pub(super) fn exec_failed(result: &RawExecResult) -> SandboxHostError {
    SandboxHostError::ExecFailed {
        exit_code: result.exit_code,
        message: stderr_or_stdout(result),
    }
}

pub(super) fn is_empty_response(result: &RawExecResult) -> bool {
    result.exit_code == THIN_CLIENT_IO_FAILED && result.stderr == EMPTY_RESPONSE_MESSAGE
}

pub(super) fn can_retry_empty_response(op: &str) -> bool {
    !matches!(
        op,
        "api.edit_file"
            | "api.v1.edit_file"
            | "api.write_file"
            | "api.v1.write_file"
            | "api.v1.exec_command"
            | "api.v1.write_stdin"
    ) && !op.starts_with("plugin.")
}

pub(super) fn decode_response(stdout: &str) -> Result<JsonObject, SandboxHostError> {
    let value: Value =
        serde_json::from_str(stdout.trim()).map_err(|_| SandboxHostError::BadResponse {
            stdout: stdout.to_owned(),
        })?;
    match value {
        Value::Object(map) => Ok(map),
        _ => Err(SandboxHostError::BadResponse {
            stdout: stdout.to_owned(),
        }),
    }
}

fn is_handler_level_error_result(response: &JsonObject) -> bool {
    response.get("success") == Some(&Value::Bool(false))
        && matches!(response.get("status"), Some(Value::String(s)) if !s.trim().is_empty())
}

pub(crate) fn decode_and_classify(result: &RawExecResult) -> Result<JsonObject, SandboxHostError> {
    let response = match decode_response(&result.stdout) {
        Ok(response) => response,
        Err(bad) => {
            // ExecFailed wins over BadResponse when the exec itself failed.
            if result.exit_code != 0 {
                return Err(exec_failed(result));
            }
            return Err(bad);
        }
    };
    if let Some(error) = response.get("error") {
        if !error.is_null() && !is_handler_level_error_result(&response) {
            return Err(dispatch_error_from_value(error));
        }
    }
    if result.exit_code != 0 {
        return Err(exec_failed(result));
    }
    Ok(response)
}

fn dispatch_error_from_value(error: &Value) -> SandboxHostError {
    match error {
        Value::Object(map) => SandboxHostError::DaemonDispatch {
            kind: map
                .get("kind")
                .and_then(truthy_to_string)
                .unwrap_or_else(|| "RuntimeError".to_owned()),
            message: map
                .get("message")
                .and_then(truthy_to_string)
                .unwrap_or_default(),
            details: match map.get("details") {
                Some(Value::Object(d)) => d.clone(),
                _ => JsonObject::new(),
            },
        },
        other => SandboxHostError::DaemonDispatch {
            kind: "RuntimeError".to_owned(),
            message: plain_string(other),
            details: JsonObject::new(),
        },
    }
}

pub(super) fn readiness_error_from_value(error: &Value, op: &str) -> SandboxHostError {
    let (kind, message, mut details) = match error {
        Value::Object(map) => (
            map.get("kind")
                .and_then(truthy_to_string)
                .unwrap_or_else(|| "RuntimeReadinessFailed".to_owned()),
            map.get("message")
                .and_then(truthy_to_string)
                .unwrap_or_default(),
            match map.get("details") {
                Some(Value::Object(d)) => d.clone(),
                _ => JsonObject::new(),
            },
        ),
        other => (
            "RuntimeReadinessFailed".to_owned(),
            plain_string(other),
            JsonObject::new(),
        ),
    };
    details.insert("original_op".to_owned(), Value::String(op.to_owned()));
    SandboxHostError::DaemonDispatch {
        kind,
        message,
        details,
    }
}

pub(crate) fn plain_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// The bootstrap fall-through: for the two workspace-base ops, treat the daemon
/// as ready despite a `control_plane` probe `down` with `WorkspaceBindingError`,
/// provided every other probe is `ok`.
pub(super) fn is_bootstrap_ready_response(op: &str, response: &JsonObject) -> bool {
    if op != "api.ensure_workspace_base" && op != "api.build_workspace_base" {
        return false;
    }
    let probes = match response.get("probes") {
        Some(Value::Array(probes)) => probes,
        _ => return false,
    };
    // by_name: last writer wins (matches the Python dict build).
    let mut by_name: BTreeMap<&str, &JsonObject> = BTreeMap::new();
    for probe in probes {
        if let Value::Object(map) = probe {
            if let Some(name) = map.get("name").and_then(Value::as_str) {
                by_name.insert(name, map);
            }
        }
    }
    let control_plane = match by_name.get("control_plane") {
        Some(cp) => *cp,
        None => return false,
    };
    let details = match control_plane.get("details") {
        Some(Value::Object(details)) => details,
        _ => return false,
    };
    if control_plane.get("status").and_then(Value::as_str) != Some("down") {
        return false;
    }
    if details.get("error_type").and_then(Value::as_str) != Some("WorkspaceBindingError") {
        return false;
    }
    by_name
        .iter()
        .filter(|(name, _)| **name != "control_plane")
        .all(|(_, probe)| probe.get("status").and_then(Value::as_str) == Some("ok"))
}

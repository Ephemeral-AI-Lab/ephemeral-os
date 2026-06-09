//! Pure daemon-envelope helpers: the outbound request-identity payload builder,
//! the hand-written `parse_*_result` decoders, timing normalization, field
//! coercion, and the recoverable-conflict classifier.
//!
//! Ports `sandbox/api/tool/_daemon_response_parsing.py` and
//! `sandbox/api/tool/_conflict_detection.py`. Result decode is **hand-written**,
//! never a blanket `serde_json::from_value` of the envelope into the result
//! struct: several fields need defaults / filtering / derivation that raw serde
//! would not apply. Everything here is `pub(crate)`.

use std::collections::BTreeMap;

use eos_types::JsonObject;
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{
    CommandOutput, ConflictInfo, EditFileResult, ExecCommandResult, KnownCommandStatus,
    ReadFileResult, SandboxRequestBase, SandboxResultBase, Workspace, WriteFileResult,
};

// ---------------------------------------------------------------------------
// Outbound identity payload
// ---------------------------------------------------------------------------

/// Build the daemon-envelope identity: a top-level `caller_id` and, only when
/// present, a top-level `invocation_id`.
pub(crate) fn daemon_request_identity_fields(base: &SandboxRequestBase) -> JsonObject {
    let mut payload = JsonObject::new();
    payload.insert(
        "caller_id".to_owned(),
        Value::String(base.caller_id.clone()),
    );
    if let Some(invocation_id) = &base.invocation_id {
        payload.insert(
            "invocation_id".to_owned(),
            Value::String(invocation_id.to_string()),
        );
    }
    payload
}

// ---------------------------------------------------------------------------
// Error message + conflict classification
// ---------------------------------------------------------------------------

const DAEMON_INTERNAL_ERROR_PREFIX: &str = "internal_error: ";

/// Strip the daemon `internal_error:` prefix from a raw error message (mirrors
/// `user_visible_error_message`).
pub(crate) fn user_visible_error_message(message: &str) -> &str {
    message
        .strip_prefix(DAEMON_INTERNAL_ERROR_PREFIX)
        .unwrap_or(message)
}

const EDIT_CONFLICT_CODES: [&str; 3] = [
    "aborted_overlap",
    "anchor_not_found",
    "anchor_occurrence_count_mismatch",
];
// Message substrings, already lowercase (ported from conflict_markers.py). Both
// the api side (here) and the audit side (relocated to eos-tool) must keep
// these in sync — see the conflict_markers.py docstring.
const EDIT_CONFLICT_MARKERS: [&str; 3] = [
    "anchor not found",
    "anchor occurrence count mismatch",
    "aborted_overlap",
];
fn matches_conflict(err: &SandboxPortError, codes: &[&str], markers: &[&str]) -> bool {
    if let Some(code) = err.code() {
        let normalized = code.trim().to_lowercase();
        if codes.contains(&normalized.as_str()) {
            return true;
        }
    }
    let lowered = user_visible_error_message(err.message()).to_lowercase();
    markers.iter().any(|marker| lowered.contains(marker))
}

/// Whether a transport error is a recoverable edit conflict (`edit_file` maps it
/// to a successful `Ok(result)` instead of `Err`).
pub(crate) fn is_edit_conflict(err: &SandboxPortError) -> bool {
    matches_conflict(err, &EDIT_CONFLICT_CODES, &EDIT_CONFLICT_MARKERS)
}

// ---------------------------------------------------------------------------
// Field coercion helpers
// ---------------------------------------------------------------------------

/// Rust `str(value)` for the values that can appear in a daemon collection.
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// Rust `str(value or "")` — the falsy-collapse used by the path/kind filters.
/// Note: a literal numeric `0` collapses to `""` in Rust; that path-element
/// edge never occurs for daemon path lists and is treated the same here.
fn py_truthy_str(value: &Value) -> String {
    match value {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_owned(),
        Value::Number(n) => {
            if n.as_f64() == Some(0.0) {
                String::new()
            } else {
                n.to_string()
            }
        }
        other => other.to_string(),
    }
}

/// `bool(response.get(key, False))`, fail-closed.
fn get_bool(map: &JsonObject, key: &str) -> bool {
    map.get(key).and_then(Value::as_bool).unwrap_or(false)
}

/// `str(response.get(key, default))` for the common string-or-default case. A
/// JSON null or absent key yields `default` (Rust's `str(None)` quirk on an
/// explicit null is not reproduced — daemon never sends null here).
fn get_string(map: &JsonObject, key: &str, default: &str) -> String {
    match map.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(value) if !value.is_null() => py_str(value),
        _ => default.to_owned(),
    }
}

/// `str(value) if value is not None else None` — present (non-null) keeps the
/// value (even empty), null/absent yields `None`.
fn optional_string(map: &JsonObject, key: &str) -> Option<String> {
    match map.get(key) {
        None | Some(Value::Null) => None,
        Some(value) => Some(py_str(value)),
    }
}

/// `str(response.get(key) or "")` — a falsy value (absent, null, `false`, `0`,
/// empty string) collapses to `""`. Distinct from [`get_string`], whose default
/// applies only on an absent/null key.
fn truthy_or_empty(map: &JsonObject, key: &str) -> String {
    map.get(key).map(py_truthy_str).unwrap_or_default()
}

/// `str(value) if value else None` — a truthy (non-empty) value yields `Some`.
fn truthy_string(map: &JsonObject, key: &str) -> Option<String> {
    match map.get(key) {
        None => None,
        Some(value) if py_truthy_str(value).is_empty() => None,
        Some(value) => Some(py_str(value)),
    }
}

/// `strict_int_from_daemon_field`: absent/null yields `default`, a JSON bool is
/// rejected (no bool-as-int), an integer is returned, anything else is rejected.
fn strict_int(map: &JsonObject, key: &str, default: i64) -> Result<i64, SandboxPortError> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(Value::Bool(value)) => Err(SandboxPortError::decode(format!(
            "expected integer value, got bool ({value})"
        ))),
        Some(Value::Number(number)) => number.as_i64().ok_or_else(|| {
            SandboxPortError::decode(format!(
                "expected integer value, got non-integer number ({number})"
            ))
        }),
        Some(other) => Err(SandboxPortError::decode(format!(
            "expected integer value, got {}",
            json_type_name(other)
        ))),
    }
}

fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// `parse_path_tuple_field`: keep `str(path)` for array entries whose
/// `str(path or "").strip()` is non-empty (blank/whitespace-only entries drop;
/// whitespace-padded values are preserved unstripped). Non-arrays yield empty.
fn parse_path_tuple(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter(|item| !py_truthy_str(item).trim().is_empty())
            .map(py_str)
            .collect(),
        _ => Vec::new(),
    }
}

/// Unfiltered `[str(path) for path in raw]` used by the exec parser only (it
/// does **not** drop blank entries, unlike the guarded parser).
fn parse_path_list_unfiltered(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items.iter().map(py_str).collect(),
        _ => Vec::new(),
    }
}

/// `parse_changed_path_kinds_field`: drop pairs whose key or value is blank.
fn parse_changed_path_kinds(value: Option<&Value>) -> BTreeMap<String, String> {
    match value {
        Some(Value::Object(map)) => map
            .iter()
            .filter(|(key, value)| {
                !key.trim().is_empty() && !py_truthy_str(value).trim().is_empty()
            })
            .map(|(key, value)| (key.clone(), py_str(value)))
            .collect(),
        _ => BTreeMap::new(),
    }
}

/// Unfiltered `{str(k): str(v) for k, v in raw.items()}` used by the exec parser.
fn parse_changed_path_kinds_unfiltered(value: Option<&Value>) -> BTreeMap<String, String> {
    match value {
        Some(Value::Object(map)) => map
            .iter()
            .map(|(key, value)| (key.clone(), py_str(value)))
            .collect(),
        _ => BTreeMap::new(),
    }
}

/// `parse_conflict_info_field`: a non-object yields `None`.
fn parse_conflict_info(value: Option<&Value>) -> Option<ConflictInfo> {
    let map = value?.as_object()?;
    let conflict_file = match map.get("conflict_file") {
        Some(Value::String(s)) => Some(s.clone()),
        Some(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    };
    Some(ConflictInfo {
        reason: get_string(map, "reason", ""),
        conflict_file,
        message: get_string(map, "message", ""),
    })
}

/// `dict(error) if isinstance(error, dict) else None`.
fn error_object(value: Option<&Value>) -> Option<JsonObject> {
    match value {
        Some(Value::Object(map)) => Some(map.clone()),
        _ => None,
    }
}

/// `normalize_timing_map`: object keys are kept verbatim and values coerced to
/// `f64`. Keys are already plain strings over JSON — `TimingKey` is a
/// `str`-subclass enum, so its members serialize as their value, never the
/// `TimingKey.*` repr; the prefix branch in Rust's `_timing_key_text` is dead
/// at this boundary and is deliberately not ported (it would require coupling to
/// the daemon-internal enum). Non-numeric values are skipped defensively.
fn parse_timing_map(value: Option<&Value>) -> BTreeMap<String, f64> {
    match value {
        Some(Value::Object(map)) => map
            .iter()
            .filter_map(|(key, value)| value.as_f64().map(|seconds| (key.clone(), seconds)))
            .collect(),
        _ => BTreeMap::new(),
    }
}

fn parse_workspace(response: &JsonObject) -> Workspace {
    response
        .get("workspace")
        .or_else(|| response.get("workspace_mode"))
        .and_then(Value::as_str)
        .map_or(Workspace::Ephemeral, |workspace| match workspace {
            "isolated" | "isolated_workspace" => Workspace::Isolated,
            _ => Workspace::Ephemeral,
        })
}

// ---------------------------------------------------------------------------
// Per-verb result parsers
// ---------------------------------------------------------------------------

/// The result base for read-only verbs: only `success` and `timings` come from
/// the envelope; conflict/changed-path/error fields stay at their empty defaults
/// and workspace mode is preserved when the daemon reports it.
fn simple_result_base(response: &JsonObject) -> SandboxResultBase {
    SandboxResultBase {
        success: get_bool(response, "success"),
        workspace: parse_workspace(response),
        timings: parse_timing_map(response.get("timings")),
        conflict: None,
        conflict_reason: None,
        changed_paths: Vec::new(),
        error: None,
    }
}

pub(crate) fn parse_read_file_result(
    response: &JsonObject,
) -> Result<ReadFileResult, SandboxPortError> {
    Ok(ReadFileResult {
        base: simple_result_base(response),
        content: get_string(response, "content", ""),
        exists: get_bool(response, "exists"),
        encoding: get_string(response, "encoding", "utf-8"),
    })
}

/// The common guarded-mutation fields shared by write/edit/shell results.
struct GuardedCommon {
    base: SandboxResultBase,
    changed_path_kinds: BTreeMap<String, String>,
    mutation_source: String,
    status: String,
}

fn parse_guarded_common(response: &JsonObject) -> GuardedCommon {
    GuardedCommon {
        base: SandboxResultBase {
            success: get_bool(response, "success"),
            workspace: parse_workspace(response),
            timings: parse_timing_map(response.get("timings")),
            conflict: parse_conflict_info(response.get("conflict")),
            conflict_reason: optional_string(response, "conflict_reason"),
            changed_paths: parse_path_tuple(response.get("changed_paths")),
            error: error_object(response.get("error")),
        },
        changed_path_kinds: parse_changed_path_kinds(response.get("changed_path_kinds")),
        // Guarded `mutation_source` collapses falsy values (`str(x or "")`),
        // unlike `status` whose default applies only on an absent key.
        mutation_source: truthy_or_empty(response, "mutation_source"),
        status: get_string(response, "status", ""),
    }
}

pub(crate) fn parse_write_file_result(
    response: &JsonObject,
) -> Result<WriteFileResult, SandboxPortError> {
    let common = parse_guarded_common(response);
    Ok(WriteFileResult {
        base: common.base,
        changed_path_kinds: common.changed_path_kinds,
        mutation_source: common.mutation_source,
        status: common.status,
    })
}

pub(crate) fn parse_edit_file_result(
    response: &JsonObject,
) -> Result<EditFileResult, SandboxPortError> {
    let common = parse_guarded_common(response);
    Ok(EditFileResult {
        base: common.base,
        changed_path_kinds: common.changed_path_kinds,
        mutation_source: common.mutation_source,
        status: common.status,
        applied_edits: strict_int(response, "applied_edits", 0)? as u32,
    })
}

pub(crate) fn parse_exec_command_result(
    response: &JsonObject,
) -> Result<ExecCommandResult, SandboxPortError> {
    // `success = status not in {"error","timed_out"}`, using a falsy status as
    // "" (which IS a success); the `status` field falls back to "error".
    let raw_status = response
        .get("status")
        .and_then(Value::as_str)
        .filter(|status| !status.is_empty());
    let success = !KnownCommandStatus::is_error_raw(raw_status.unwrap_or(""));
    let status = raw_status.unwrap_or("error").to_owned();

    let output_map = response.get("output").and_then(Value::as_object);
    let output = output_map.map_or_else(CommandOutput::default, |map| CommandOutput {
        stdout: get_string(map, "stdout", ""),
        stderr: get_string(map, "stderr", ""),
    });

    // `int(exit_code) if isinstance(exit_code, int) else None` — Rust's
    // isinstance(bool, int) is true, so a JSON bool maps to 0/1.
    let exit_code = match response.get("exit_code") {
        Some(Value::Number(number)) => number.as_i64().map(|value| value as i32),
        Some(Value::Bool(value)) => Some(i32::from(*value)),
        _ => None,
    };

    Ok(ExecCommandResult {
        base: SandboxResultBase {
            success,
            workspace: parse_workspace(response),
            timings: parse_timing_map(response.get("timings")),
            conflict: None,
            conflict_reason: optional_string(response, "conflict_reason"),
            changed_paths: parse_path_list_unfiltered(response.get("changed_paths")),
            error: error_object(response.get("error")),
        },
        status,
        exit_code,
        output,
        command_session_id: truthy_string(response, "command_session_id")
            .and_then(|raw| raw.parse().ok()),
        changed_path_kinds: parse_changed_path_kinds_unfiltered(response.get("changed_path_kinds")),
        mutation_source: optional_string(response, "mutation_source").unwrap_or_default(),
    })
}

#[cfg(test)]
#[path = "../../tests/tool_dispatch/parse.rs"]
mod tests;

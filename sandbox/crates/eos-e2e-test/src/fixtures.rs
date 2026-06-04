//! Workspace seeding + load generators on top of a [`NodeLease`].
//!
//! Thin convenience wrappers around the verb ops so tests read declaratively;
//! every wrapper still goes through `lease.call`/`call_ok` (the wire).

use anyhow::Result;
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::pool::NodeLease;

/// `write_file` (overwrite). Asserts success.
///
/// # Errors
/// Returns an error on transport failure or a non-success response.
pub fn write_file(lease: &NodeLease, path: &str, content: &str) -> Result<Value> {
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({"path": path, "content": content, "overwrite": true}),
    )
}

/// `read_file` (no success assertion — callers inspect `exists`).
///
/// # Errors
/// Returns an error on transport failure.
pub fn read_file(lease: &NodeLease, path: &str) -> Result<Value> {
    lease.call(ops::API_V1_READ_FILE, json!({"path": path}))
}

/// `edit_file` with one search/replace edit.
///
/// # Errors
/// Returns an error on transport failure.
pub fn edit_file(
    lease: &NodeLease,
    path: &str,
    old: &str,
    new: &str,
    replace_all: bool,
) -> Result<Value> {
    lease.call(
        ops::API_V1_EDIT_FILE,
        json!({
            "path": path,
            "edits": [{"old_text": old, "new_text": new, "replace_all": replace_all}],
        }),
    )
}

/// `exec_command` (one-shot; default yield window).
///
/// # Errors
/// Returns an error on transport failure.
pub fn exec(lease: &NodeLease, cmd: &str) -> Result<Value> {
    lease.call(ops::API_V1_EXEC_COMMAND, json!({"cmd": cmd}))
}

/// `glob` over the merged overlay view.
///
/// # Errors
/// Returns an error on transport failure.
pub fn glob(lease: &NodeLease, pattern: &str) -> Result<Value> {
    lease.call(ops::API_V1_GLOB, json!({"pattern": pattern}))
}

/// `api.layer_metrics` for this lease's root.
///
/// # Errors
/// Returns an error on transport failure.
pub fn layer_metrics(lease: &NodeLease) -> Result<Value> {
    lease.call_ok(ops::API_LAYER_METRICS, json!({}))
}

/// Publish `count` successive versions of the same path to grow layer depth
/// (drives the auto-squash threshold). Returns the final response.
///
/// # Errors
/// Returns an error on transport failure or a non-success write.
pub fn grow_layers(lease: &NodeLease, path: &str, count: usize) -> Result<Value> {
    let mut last = Value::Null;
    for i in 0..count {
        last = write_file(lease, path, &format!("v{i}\n"))?;
    }
    Ok(last)
}

/// A deterministic blob of `bytes` length (no RNG).
#[must_use]
pub fn blob(bytes: usize) -> String {
    "x".repeat(bytes)
}

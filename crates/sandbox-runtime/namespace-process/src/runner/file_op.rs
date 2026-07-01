//! Namespace file-op runner protocol and entry point. A session file operation
//! shares the ns-runner harness with `exec_command`: `setns` into the session's
//! holder namespaces, run a body, return a result. The body here reads or writes
//! one regular file at `workspace_root/rel` through the mounted overlay, with
//! fd-relative no-follow path walking so nothing escapes the workspace tree.
//!
//! File-op outcomes (including not-found and not-regular) are carried in the
//! result payload with exit code 0; only a crash or a missing envelope is a
//! transport failure. Result bytes are drained concurrently by the launcher and
//! capped, so a large `ReadFile` cannot deadlock the child.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::runner::protocol::{NamespaceRunnerRequest, RunResult};

/// The requested file operation, carried in the runner request `args`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum FileRunnerOp {
    ReadWindow {
        rel: String,
        offset: u64,
        limit: usize,
        output_cap: usize,
    },
    ReadFile {
        rel: String,
        max_bytes: usize,
    },
    Write {
        rel: String,
        content: String,
    },
}

/// File-type of a non-regular path the runner refused to read or write.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FileRunnerEntryKind {
    Directory,
    Symlink,
    Other,
}

/// A successful file-op result. `existed` reflects pre-operation regular-file
/// existence, so absent reads are a result (not an error).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FileRunnerResult {
    ReadWindow {
        existed: bool,
        content: String,
        start_line: u64,
        num_lines: usize,
        total_lines: u64,
        bytes_read: usize,
        total_bytes: u64,
        next_offset: Option<u64>,
        truncated: bool,
    },
    ReadFile {
        existed: bool,
        bytes_b64: String,
        total_bytes: u64,
    },
    Write {
        existed: bool,
        bytes_written: usize,
    },
}

/// A file-op error. Transport failures (`setns`, bad request) are reported as
/// `Io`; the operation layer maps each variant to a response kind.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "error", rename_all = "snake_case")]
pub enum FileRunnerError {
    NotRegular { kind: FileRunnerEntryKind },
    NotUtf8,
    FileTooLarge { size: u64, limit: usize },
    OutputTooLarge { limit: usize },
    Io { path: String, message: String },
}

/// Run a file operation inside the session namespaces and encode the outcome as
/// a [`RunResult`]. File-op outcomes use exit code 0; the launcher inspects the
/// payload, not the exit code.
#[must_use]
pub fn run_file_op(request: &NamespaceRunnerRequest) -> RunResult {
    match crate::runner::setns::run_file_op_setns(request) {
        Ok(result) => envelope("result", to_value(&result)),
        Err(error) => envelope("error", to_value(&error)),
    }
}

/// Decode a runner result payload into the file-op outcome, or `None` when the
/// runner produced no valid envelope (a crash or transport failure).
#[must_use]
pub fn decode_file_op_payload(
    payload: &Value,
) -> Option<Result<FileRunnerResult, FileRunnerError>> {
    if let Some(result) = payload.get("result") {
        return serde_json::from_value::<FileRunnerResult>(result.clone())
            .ok()
            .map(Ok);
    }
    if let Some(error) = payload.get("error") {
        return serde_json::from_value::<FileRunnerError>(error.clone())
            .ok()
            .map(Err);
    }
    None
}

#[cfg(target_os = "linux")]
pub(crate) fn decode_op(request: &NamespaceRunnerRequest) -> Result<FileRunnerOp, FileRunnerError> {
    serde_json::from_value::<FileRunnerOp>(request.args.clone()).map_err(|error| {
        FileRunnerError::Io {
            path: String::new(),
            message: format!("invalid file-op request: {error}"),
        }
    })
}

fn envelope(field: &str, value: Value) -> RunResult {
    RunResult {
        exit_code: 0,
        payload: json!({ "status": "ok", field: value }),
    }
}

fn to_value<T: Serialize>(value: &T) -> Value {
    serde_json::to_value(value).unwrap_or(Value::Null)
}

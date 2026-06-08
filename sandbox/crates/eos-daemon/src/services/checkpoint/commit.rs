//! Workspace checkpoint commit services.
//!
//! `commit_to_git` is a thin seam over [`eos_checkpoint_host`]: this module owns
//! only the request-envelope parsing and the response/error re-mapping; the
//! pathspec policy, worktree preparation, and git pipeline live in the host
//! crate so the daemon does not fuse that glue into the control plane.

use std::path::PathBuf;
use std::time::Instant;

use eos_checkpoint_host::{CommitOutcome, CommitRequest};
use eos_layerstack::LayerStack;
use serde_json::{json, Value};

use crate::error::DaemonError;
use crate::request_args::{require_string, timings_to_value_map};

pub(crate) fn commit_to_workspace(args: &Value) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let mut stack = LayerStack::open(root)?;
    let (manifest, commit_timings) = stack.commit_to_workspace(&workspace_root)?;
    let mut timings = timings_to_value_map(&commit_timings);
    timings.insert(
        "api.commit_to_workspace.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    Ok(json!({
        "success": true,
        "manifest_version": manifest.version,
        "timings": Value::Object(timings),
    }))
}

pub(crate) fn commit_to_git(args: &Value) -> Result<Value, DaemonError> {
    let layer_stack_root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let message = require_string(args, "message")?;
    let raw_paths = raw_commit_paths(args)?;
    let outcome = eos_checkpoint_host::commit_to_git(&CommitRequest {
        layer_stack_root: &layer_stack_root,
        workspace_root: &workspace_root,
        message: &message,
        raw_paths,
    })?;
    Ok(commit_response(&outcome))
}

/// Lift the raw `paths` pathspecs from the envelope. Normalization (trimming,
/// binding resolution, `.git` rejection) is the host crate's responsibility;
/// this only enforces the wire shape (string, array-of-strings, or absent).
fn raw_commit_paths(args: &Value) -> Result<Vec<String>, DaemonError> {
    let Some(value) = args.get("paths") else {
        return Ok(Vec::new());
    };
    match value {
        Value::Null => Ok(Vec::new()),
        Value::String(path) => Ok(vec![path.clone()]),
        Value::Array(items) => items
            .iter()
            .map(|item| {
                item.as_str().map(str::to_owned).ok_or_else(|| {
                    DaemonError::InvalidEnvelope("paths must be strings".to_owned())
                })
            })
            .collect(),
        _ => Err(DaemonError::InvalidEnvelope(
            "paths must be a string or array of strings".to_owned(),
        )),
    }
}

fn commit_response(outcome: &CommitOutcome) -> Value {
    json!({
        "success": true,
        "committed": outcome.committed,
        "commit_sha": outcome.commit_sha,
        "manifest_version": outcome.manifest_version,
        "manifest_root_hash": outcome.manifest_root_hash,
        "paths": outcome.paths,
        "worktree_mode": outcome.worktree_mode,
        "timings": Value::Object(timings_to_value_map(&outcome.timings)),
    })
}

#[cfg(test)]
#[path = "../../../tests/checkpoint/commit.rs"]
mod tests;

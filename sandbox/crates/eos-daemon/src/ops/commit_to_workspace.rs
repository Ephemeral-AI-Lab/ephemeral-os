//! Materialize LayerStack state into the bound workspace.

use std::path::PathBuf;
use std::time::Instant;

use eos_layerstack::LayerStack;
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::request_args::{require_string, timings_to_value_map};

pub(crate) fn op_commit_to_workspace(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
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

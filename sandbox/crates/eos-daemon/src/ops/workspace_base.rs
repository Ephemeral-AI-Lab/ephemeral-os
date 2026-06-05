//! Workspace base, binding, commit, and LayerStack metric ops.

use std::path::{Path, PathBuf};
use std::time::Instant;

use eos_layerstack::{
    build_workspace_base, ensure_workspace_base, read_workspace_binding, require_workspace_binding,
    LayerStack,
};
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::occ_writer::occ_service_cache_snapshot;
use crate::request_args::{binding_to_value, require_string, timings_to_value_map};

/// `api.layer_metrics` — summarize layer-stack storage + lease state for a root.
pub(crate) fn op_layer_metrics(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let stack = LayerStack::open(root.clone())?;
    let manifest = stack.read_active_manifest()?;
    let binding = read_workspace_binding(&root)?;
    Ok(json!({
        "success": true,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth(),
        "active_leases": stack.active_lease_count(),
        "leased_layers": stack.leased_layers().len(),
        "layer_dirs": count_dirs(&root.join("layers"))?,
        "referenced_layers": manifest.layers.len(),
        "orphan_layer_count": 0,
        "missing_layer_count": 0,
        "orphan_layer_ids": [],
        "missing_layer_ids": [],
        "staging_dirs": count_dirs(&root.join("staging"))?,
        "storage_bytes": storage_bytes(&root)?,
        "workspace_bound": binding.is_some(),
        "workspace_root": binding.as_ref().map_or("", |binding| binding.workspace_root.as_str()),
        "base_root_hash": binding.as_ref().map_or("", |binding| binding.base_root_hash.as_str()),
        "occ_runtime_service_cache": occ_service_cache_snapshot(),
    }))
}

pub(crate) fn op_build_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let reset = args.get("reset").and_then(Value::as_bool).unwrap_or(false);
    if reset {
        crate::plugin::stop_services_for_layer_stack_root(&root.to_string_lossy())?;
    }
    let built = build_workspace_base(&root, &workspace_root, reset)?;
    let mut timings = timings_to_value_map(&built.timings);
    timings.insert(
        "api.workspace_base.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let binding = binding_to_value(&built.binding)?;
    Ok(json!({
        "success": true,
        "created": true,
        "binding": binding,
        "timings": Value::Object(timings),
    }))
}

pub(crate) fn op_ensure_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let (binding, created) = ensure_workspace_base(&root, &workspace_root)?;
    let binding = binding_to_value(&binding)?;
    let timings = json!({
        "api.workspace_base.total_s": total_start.elapsed().as_secs_f64(),
    });
    Ok(json!({
        "success": true,
        "created": created,
        "binding": binding,
        "timings": timings,
    }))
}

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

pub(crate) fn op_workspace_binding(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let binding = require_workspace_binding(&root)?;
    let binding = binding_to_value(&binding)?;
    Ok(json!({
        "success": true,
        "binding": binding,
    }))
}

fn count_dirs(path: &Path) -> Result<usize, DaemonError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut count = 0;
    for entry in std::fs::read_dir(path)? {
        if entry?.file_type()?.is_dir() {
            count += 1;
        }
    }
    Ok(count)
}

fn storage_bytes(path: &Path) -> Result<u64, DaemonError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0;
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let meta = entry.metadata()?;
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total += meta.len();
            }
        }
    }
    Ok(total)
}

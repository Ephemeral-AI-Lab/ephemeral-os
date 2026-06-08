//! Workspace checkpoint base, binding, and LayerStack metric services.

use std::path::PathBuf;
use std::time::Instant;

use eos_layerstack::{
    build_workspace_base as build_layer_stack_workspace_base,
    ensure_workspace_base as ensure_layer_stack_workspace_base, read_workspace_binding,
    require_workspace_binding, LayerStack,
};
use serde_json::{json, Value};

use crate::error::DaemonError;
use crate::request_args::{binding_to_value, require_string, timings_to_value_map};
use crate::services::occ::occ_service_cache_snapshot;

pub(crate) fn layer_metrics(args: &Value) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let stack = LayerStack::open(root.clone())?;
    let manifest = stack.read_active_manifest()?;
    let metrics = stack.storage_metrics()?;
    let binding = read_workspace_binding(&root)?;
    Ok(json!({
        "success": true,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth(),
        "active_leases": stack.active_lease_count(),
        "leased_layers": stack.leased_layers().len(),
        "layer_dirs": metrics.layer_dirs,
        "referenced_layers": manifest.layers.len(),
        "orphan_layer_count": 0,
        "missing_layer_count": 0,
        "orphan_layer_ids": [],
        "missing_layer_ids": [],
        "staging_dirs": metrics.staging_dirs,
        "storage_bytes": metrics.storage_bytes,
        "workspace_bound": binding.is_some(),
        "workspace_root": binding.as_ref().map_or("", |binding| binding.workspace_root.as_str()),
        "base_root_hash": binding.as_ref().map_or("", |binding| binding.base_root_hash.as_str()),
        "occ_runtime_service_cache": occ_service_cache_snapshot(),
    }))
}

pub(crate) fn build_workspace_base(args: &Value) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let reset = args.get("reset").and_then(Value::as_bool).unwrap_or(false);
    if reset {
        crate::services::plugins::stop_services_for_layer_stack_root(&root.to_string_lossy())?;
    }
    let built = build_layer_stack_workspace_base(&root, &workspace_root, reset)?;
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

pub(crate) fn ensure_workspace_base(args: &Value) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let (binding, created) = ensure_layer_stack_workspace_base(&root, &workspace_root)?;
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

pub(crate) fn workspace_binding(args: &Value) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let binding = require_workspace_binding(&root)?;
    let binding = binding_to_value(&binding)?;
    Ok(json!({
        "success": true,
        "binding": binding,
    }))
}

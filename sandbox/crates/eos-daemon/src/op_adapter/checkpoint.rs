//! Workspace checkpoint adapters: LayerStack base/binding/metrics plus the
//! `eos_operation::checkpoint` commit module.

use std::time::Instant;

use eos_layerstack::{
    build_workspace_base as build_layer_stack_workspace_base,
    ensure_workspace_base as ensure_layer_stack_workspace_base, read_workspace_binding,
    require_workspace_binding, LayerStack,
};
use eos_operation::checkpoint::contract::{
    BindingInput, BindingOutput, BuildBaseInput, CommitInput, CommitOutput, CommitToWorkspaceInput,
    CommitToWorkspaceOutput, EnsureBaseInput, LayerMetricsInput, LayerMetricsOutput,
    WorkspaceBaseOutput,
};
use eos_layerstack::WorkspaceBinding;
use eos_operation::checkpoint::{CommitOutcome, CommitRequest};
use serde_json::Value;

use crate::error::DaemonError;
use crate::DispatchContext;
use eos_layerstack::service::cache_snapshot;

use super::to_wire_value;

pub(crate) fn layer_metrics(
    input: LayerMetricsInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let root = input.layer_stack_root;
    let stack = LayerStack::open(root.clone())?;
    let manifest = stack.read_active_manifest()?;
    let metrics = stack.storage_metrics()?;
    let binding = read_workspace_binding(&root)?;
    Ok(to_wire_value(LayerMetricsOutput {
        success: true,
        manifest_version: manifest.version,
        manifest_depth: manifest.depth(),
        active_leases: stack.active_lease_count(),
        leased_layers: stack.leased_layers().len(),
        layer_dirs: metrics.layer_dirs,
        referenced_layers: manifest.layers.len(),
        orphan_layer_count: 0,
        missing_layer_count: 0,
        orphan_layer_ids: Vec::new(),
        missing_layer_ids: Vec::new(),
        staging_dirs: metrics.staging_dirs,
        storage_bytes: metrics.storage_bytes,
        workspace_bound: binding.is_some(),
        workspace_root: binding
            .as_ref()
            .map_or_else(String::new, |binding| binding.workspace_root.clone()),
        base_root_hash: binding
            .as_ref()
            .map_or_else(String::new, |binding| binding.base_root_hash.clone()),
        occ_runtime_service_cache: cache_snapshot(),
    }))
}

pub(crate) fn build_workspace_base(
    input: BuildBaseInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = input.layer_stack_root;
    let workspace_root = input.workspace_root;
    if input.reset {
        context
            .require_services()?
            .plugin
            .stop_services_for_layer_stack_root(&root.to_string_lossy())?;
    }
    let built = build_layer_stack_workspace_base(&root, &workspace_root, input.reset)?;
    let mut timings = built.timings;
    timings.insert(
        "api.workspace_base.total_s".to_owned(),
        total_start.elapsed().as_secs_f64(),
    );
    let binding = binding_to_value(&built.binding)?;
    Ok(to_wire_value(WorkspaceBaseOutput {
        success: true,
        created: true,
        binding,
        timings,
    }))
}

pub(crate) fn ensure_workspace_base(
    input: EnsureBaseInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = input.layer_stack_root;
    let workspace_root = input.workspace_root;
    let (binding, created) = ensure_layer_stack_workspace_base(&root, &workspace_root)?;
    let binding = binding_to_value(&binding)?;
    let timings = std::collections::BTreeMap::from([(
        "api.workspace_base.total_s".to_owned(),
        total_start.elapsed().as_secs_f64(),
    )]);
    Ok(to_wire_value(WorkspaceBaseOutput {
        success: true,
        created,
        binding,
        timings,
    }))
}

pub(crate) fn workspace_binding(
    input: BindingInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let root = input.layer_stack_root;
    let binding = require_workspace_binding(&root)?;
    let binding = binding_to_value(&binding)?;
    Ok(to_wire_value(BindingOutput {
        success: true,
        binding,
    }))
}

pub(crate) fn commit_to_workspace(
    input: CommitToWorkspaceInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = input.layer_stack_root;
    let workspace_root = input.workspace_root;
    let mut stack = LayerStack::open(root)?;
    let (manifest, mut timings) = stack.commit_to_workspace(&workspace_root)?;
    timings.insert(
        "api.commit_to_workspace.total_s".to_owned(),
        total_start.elapsed().as_secs_f64(),
    );
    Ok(to_wire_value(CommitToWorkspaceOutput {
        success: true,
        manifest_version: manifest.version,
        timings,
    }))
}

pub(crate) fn commit_to_git(
    input: CommitInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let outcome = eos_operation::checkpoint::commit_to_git(&CommitRequest {
        layer_stack_root: &input.layer_stack_root,
        workspace_root: &input.workspace_root,
        message: &input.message,
        raw_paths: input.paths,
    })?;
    Ok(commit_response(&outcome))
}

fn commit_response(outcome: &CommitOutcome) -> Value {
    to_wire_value(CommitOutput {
        success: true,
        committed: outcome.committed,
        commit_sha: outcome.commit_sha.clone(),
        manifest_version: outcome.manifest_version,
        manifest_root_hash: outcome.manifest_root_hash.clone(),
        paths: outcome.paths.clone(),
        worktree_mode: outcome.worktree_mode.to_owned(),
        timings: outcome.timings.clone(),
    })
}

fn binding_to_value(binding: &WorkspaceBinding) -> Result<Value, DaemonError> {
    serde_json::to_value(binding).map_err(|err| DaemonError::InvalidRequest(err.to_string()))
}

#[cfg(test)]
#[path = "../../tests/unit/checkpoint/commit.rs"]
mod tests;

//! Workspace file and read-only search op handlers.

#[cfg(target_os = "linux")]
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use eos_layerstack::{require_workspace_binding, LayerStack};
#[cfg(target_os = "linux")]
use eos_layerstack::{MergedView, WorkspaceBinding};
use eos_protocol::{
    apply_search_replace,
    models::{SearchReplaceEdit, MAX_READ_BYTES},
    Intent, LayerChange, LayerPath, SearchReplaceError,
};
#[cfg(target_os = "linux")]
use eos_protocol::{LayerRef, Manifest};
#[cfg(target_os = "linux")]
use eos_runner::{Fd, NsFds};
use eos_runner::{RunMode, RunRequest, RunResult, ToolCall, WorkspaceRoot};
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::occ_writer::{apply_occ_changeset, hash_current, manifest_version_u64};
use crate::overlay_runner::{overlay_run_dirs, run_ns_runner_child, RunDirCleanup};
use crate::request_args::{require_raw_string, require_string};
use crate::response_timings::{
    guarded_changeset_response, guarded_conflict_response, merge_runner_timings,
    published_file_count, resource_timings, usize_to_f64_saturating, usize_to_i64_saturating,
};

/// `api.v1.read_file` — direct `LayerStack` read path.
pub(crate) fn op_read_file(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_read_file(args, &handle, total_start);
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let raw_path = require_string(args, "path")?;
    let binding = require_workspace_binding(&root)?;
    let layer_path = if raw_path.starts_with('/') {
        binding.layer_path_from_absolute(&raw_path)?
    } else {
        binding.layer_path_from_relative(&raw_path)?
    };
    let stack = LayerStack::open(root)?;
    let read_start = Instant::now();
    let (bytes, exists) = stack.read_bytes(&layer_path)?;
    let content = if exists {
        let bytes = bytes.unwrap_or_default();
        if bytes.len() > MAX_READ_BYTES {
            return Err(DaemonError::InvalidEnvelope(format!(
                "file too large: {} > {} bytes",
                bytes.len(),
                MAX_READ_BYTES
            )));
        }
        String::from_utf8_lossy(&bytes).into_owned()
    } else {
        String::new()
    };
    let manifest = stack.read_active_manifest()?;
    Ok(json!({
        "success": true,
        "workspace": "ephemeral",
        "content": content,
        "exists": exists,
        "encoding": "utf-8",
        "timings": {
            "resource.command_exec.changed_path_count": 0.0,
            "resource.layer_stack.manifest_depth": usize_to_f64_saturating(manifest.depth()),
            "resource.layer_stack.manifest_path_count": usize_to_f64_saturating(manifest.depth()),
            "resource.command_exec.run_dir_tree_exists": 0.0,
            "resource.command_exec.run_dir_tree_bytes": 0.0,
            "resource.command_exec.run_dir_tree_file_count": 0.0,
            "resource.command_exec.run_dir_tree_dir_count": 0.0,
            "resource.command_exec.run_dir_tree_entry_count": 0.0,
            "resource.command_exec.run_dir_tree_truncated": 0.0,
            "resource.command_exec.workspace_tree_exists": 0.0,
            "resource.command_exec.workspace_tree_bytes": 0.0,
            "resource.command_exec.workspace_tree_file_count": 0.0,
            "resource.command_exec.workspace_tree_dir_count": 0.0,
            "resource.command_exec.workspace_tree_entry_count": 0.0,
            "resource.command_exec.workspace_tree_truncated": 0.0,
            "resource.command_exec.upperdir_tree_exists": 0.0,
            "resource.command_exec.upperdir_tree_bytes": 0.0,
            "resource.command_exec.upperdir_tree_file_count": 0.0,
            "resource.command_exec.upperdir_tree_dir_count": 0.0,
            "resource.command_exec.upperdir_tree_entry_count": 0.0,
            "resource.command_exec.upperdir_tree_truncated": 0.0,
            "api.read.layer_stack_read_s": read_start.elapsed().as_secs_f64(),
            "api.read.total_s": total_start.elapsed().as_secs_f64(),
        },
    }))
}

/// `api.v1.write_file` — direct `LayerStack` write publish path.
pub(crate) fn op_write_file(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_write_file(args, &handle, total_start);
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let layer_path = bound_layer_path(&root, args)?;
    let content = require_raw_string(args, "content")?.into_bytes();
    let stack = LayerStack::open(root.clone())?;

    if !args
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let (_current, exists) = stack.read_text(&layer_path)?;
        if exists {
            let manifest = stack.read_active_manifest()?;
            return Ok(guarded_conflict_response(
                "write",
                &layer_path,
                "rejected",
                "create_only_existing",
                "file already exists",
                resource_timings(&manifest, 0),
                total_start,
            ));
        }
    }
    let manifest = stack.read_active_manifest()?;
    let (base_bytes, base_exists) = stack.read_bytes(&layer_path)?;
    let base_hash = hash_current(base_bytes.as_deref(), base_exists);

    drop(stack);
    let occ_start = Instant::now();
    let path = LayerPath::parse(&layer_path).map_err(eos_layerstack::LayerStackError::from)?;
    let result = apply_occ_changeset(
        &root,
        Some(manifest_version_u64(manifest.version)?),
        &[LayerChange::Write {
            path: path.clone(),
            content,
        }],
        &[(path, base_hash)],
    )?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, published_file_count(&result));
    timings.insert(
        "api.write.occ_apply_s".to_owned(),
        json!(occ_start.elapsed().as_secs_f64()),
    );
    Ok(guarded_changeset_response(
        "write",
        &result,
        timings,
        total_start,
        None,
    ))
}

/// `api.v1.edit_file` — direct `LayerStack` edit publish path.
pub(crate) fn op_edit_file(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_edit_file(args, &handle, total_start);
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let layer_path = bound_layer_path(&root, args)?;
    let edits = parse_edits(args)?;
    let stack = LayerStack::open(root.clone())?;
    let (base_bytes, exists) = stack.read_bytes(&layer_path)?;
    let base_hash = hash_current(base_bytes.as_deref(), exists);
    let mut content = if exists {
        String::from_utf8(base_bytes.unwrap_or_default()).map_err(|err| {
            eos_layerstack::LayerStackError::Storage(format!("file is not utf-8 text: {err}"))
        })?
    } else {
        String::new()
    };

    if !exists {
        let manifest = stack.read_active_manifest()?;
        return Ok(guarded_conflict_response(
            "edit",
            &layer_path,
            "aborted_version",
            "aborted_version",
            "file does not exist",
            resource_timings(&manifest, 0),
            total_start,
        ));
    }

    for edit in &edits {
        match apply_search_replace(&content, &edit.old_text, &edit.new_text, edit.replace_all) {
            Ok(next) => content = next,
            Err(err) => {
                let manifest = stack.read_active_manifest()?;
                return Ok(guarded_conflict_response(
                    "edit",
                    &layer_path,
                    "aborted_overlap",
                    "aborted_overlap",
                    search_replace_message(&err),
                    resource_timings(&manifest, 0),
                    total_start,
                ));
            }
        }
    }

    let manifest = stack.read_active_manifest()?;
    drop(stack);
    let occ_start = Instant::now();
    let path = LayerPath::parse(&layer_path).map_err(eos_layerstack::LayerStackError::from)?;
    let result = apply_occ_changeset(
        &root,
        Some(manifest_version_u64(manifest.version)?),
        &[LayerChange::Write {
            path: path.clone(),
            content: content.into_bytes(),
        }],
        &[(path, base_hash)],
    )?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, published_file_count(&result));
    timings.insert(
        "api.edit.occ_apply_s".to_owned(),
        json!(occ_start.elapsed().as_secs_f64()),
    );
    Ok(guarded_changeset_response(
        "edit",
        &result,
        timings,
        total_start,
        Some(usize_to_i64_saturating(edits.len())),
    ))
}

#[cfg(target_os = "linux")]
fn isolated_read_file(
    args: &Value,
    handle: &crate::isolated::CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    let read_start = Instant::now();
    let (bytes, exists) = isolated_read_current(handle, &layer_path)?;
    let content = if exists {
        let bytes = bytes.unwrap_or_default();
        if bytes.len() > MAX_READ_BYTES {
            return Err(DaemonError::InvalidEnvelope(format!(
                "file too large: {} > {} bytes",
                bytes.len(),
                MAX_READ_BYTES
            )));
        }
        String::from_utf8_lossy(&bytes).into_owned()
    } else {
        String::new()
    };
    let mut timings = isolated_timings("read", total_start, 0);
    timings.insert(
        "api.read.layer_stack_read_s".to_owned(),
        json!(read_start.elapsed().as_secs_f64()),
    );
    record_isolated_tool_call(handle, "read_file", "ok", &[], total_start);
    Ok(json!({
        "success": true,
        "workspace": "isolated",
        "workspace_mode": "isolated",
        "content": content,
        "exists": exists,
        "encoding": "utf-8",
        "timings": Value::Object(timings),
    }))
}

#[cfg(target_os = "linux")]
fn isolated_write_file(
    args: &Value,
    handle: &crate::isolated::CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    if !args
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let (_bytes, exists) = isolated_read_current(handle, &layer_path)?;
        if exists {
            return Ok(isolated_conflict_response(
                "write",
                &layer_path,
                "create_only_existing",
                "file already exists",
                total_start,
            ));
        }
    }
    let content = require_raw_string(args, "content")?.into_bytes();
    isolated_write_upper(handle, &layer_path, &content)?;
    let changed_paths = vec![layer_path.as_str().to_owned()];
    record_isolated_tool_call(
        handle,
        "write_file",
        "committed",
        &changed_paths,
        total_start,
    );
    Ok(isolated_write_response(
        "write",
        &layer_path,
        &changed_paths,
        total_start,
        None,
    ))
}

#[cfg(target_os = "linux")]
fn isolated_edit_file(
    args: &Value,
    handle: &crate::isolated::CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    let edits = parse_edits(args)?;
    let (base_bytes, exists) = isolated_read_current(handle, &layer_path)?;
    if !exists {
        return Ok(isolated_conflict_response(
            "edit",
            &layer_path,
            "aborted_version",
            "file does not exist",
            total_start,
        ));
    }
    let mut content = String::from_utf8(base_bytes.unwrap_or_default()).map_err(|err| {
        eos_layerstack::LayerStackError::Storage(format!("file is not utf-8 text: {err}"))
    })?;
    for edit in &edits {
        match apply_search_replace(&content, &edit.old_text, &edit.new_text, edit.replace_all) {
            Ok(next) => content = next,
            Err(err) => {
                return Ok(isolated_conflict_response(
                    "edit",
                    &layer_path,
                    "aborted_overlap",
                    search_replace_message(&err),
                    total_start,
                ));
            }
        }
    }
    isolated_write_upper(handle, &layer_path, content.as_bytes())?;
    let changed_paths = vec![layer_path.as_str().to_owned()];
    record_isolated_tool_call(
        handle,
        "edit_file",
        "committed",
        &changed_paths,
        total_start,
    );
    Ok(isolated_write_response(
        "edit",
        &layer_path,
        &changed_paths,
        total_start,
        Some(usize_to_i64_saturating(edits.len())),
    ))
}

/// `api.v1.glob` — read-only overlay namespace search.
pub(crate) fn op_glob(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return run_isolated_read_tool(args, "glob", &handle, Instant::now());
    }
    run_overlay_read_tool(args, "glob")
}

/// `api.v1.grep` — read-only overlay namespace content search.
pub(crate) fn op_grep(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return run_isolated_read_tool(args, "grep", &handle, Instant::now());
    }
    run_overlay_read_tool(args, "grep")
}

fn run_overlay_read_tool(args: &Value, verb: &str) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or(verb)
        .to_owned();
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let binding = require_workspace_binding(&root)?;

    let mut stack = LayerStack::open(root.clone())?;
    let acquire_start = Instant::now();
    let lease = stack.acquire_snapshot(&format!("overlay:{agent_id}:{invocation_id}"))?;
    let lease_acquire_s = acquire_start.elapsed().as_secs_f64();
    let run_result: Result<RunResult, DaemonError> = (|| {
        let dirs = overlay_run_dirs("sandbox-overlay", &invocation_id)?;
        let _cleanup = RunDirCleanup(dirs.run_dir.clone());
        let request = RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: invocation_id.clone(),
                agent_id,
                verb: verb.to_owned(),
                intent: Intent::ReadOnly,
                args: args.clone(),
                background: false,
            },
            workspace_root: WorkspaceRoot(PathBuf::from(&binding.workspace_root)),
            layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
            upperdir: Some(dirs.upperdir),
            workdir: Some(dirs.workdir),
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: args.get("timeout_seconds").and_then(Value::as_f64),
        };
        run_ns_runner_child(&request, None)
    })();
    let _ = stack.release_lease(&lease.lease_id);

    let runner = run_result?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, 0);
    merge_runner_timings(&mut timings, &runner);
    timings.insert(
        "layer_stack.acquire_snapshot.total_s".to_owned(),
        json!(lease_acquire_s),
    );
    let mut response = runner.tool_result;
    timings
        .entry("command_exec.capture_upperdir_s".to_owned())
        .or_insert_with(|| json!(0.0));
    timings.insert(
        "command_exec.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    response["timings"] = Value::Object(timings);
    Ok(response)
}

#[cfg(target_os = "linux")]
fn run_isolated_read_tool(
    args: &Value,
    verb: &str,
    handle: &crate::isolated::CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or(verb)
        .to_owned();
    let ns_fds = isolated_ns_fds(&handle.ns_fds);
    let request = RunRequest {
        mode: if ns_fds.is_some() {
            RunMode::SetNs
        } else {
            RunMode::FreshNs
        },
        tool_call: ToolCall {
            invocation_id,
            agent_id: handle.agent_id.clone(),
            verb: verb.to_owned(),
            intent: Intent::ReadOnly,
            args: args.clone(),
            background: false,
        },
        workspace_root: WorkspaceRoot(handle.workspace_root.clone()),
        layer_paths: handle.layer_paths.clone(),
        upperdir: Some(handle.upperdir.clone()),
        workdir: Some(handle.workdir.clone()),
        ns_fds,
        cgroup_path: handle.cgroup_path.clone(),
        timeout_seconds: args.get("timeout_seconds").and_then(Value::as_f64),
    };
    let runner = run_ns_runner_child(&request, None)?;
    let mut timings = resource_timings(&isolated_manifest(handle), 0);
    merge_runner_timings(&mut timings, &runner);
    timings.insert(
        "command_exec.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let mut response = runner.tool_result;
    response["workspace"] = json!("isolated");
    response["workspace_mode"] = json!("isolated");
    response["timings"] = Value::Object(timings);
    record_isolated_tool_call(handle, verb, "ok", &[], total_start);
    Ok(response)
}

fn bound_layer_path(root: &Path, args: &Value) -> Result<String, DaemonError> {
    let raw_path = require_string(args, "path")?;
    let binding = require_workspace_binding(root)?;
    if raw_path.starts_with('/') {
        binding
            .layer_path_from_absolute(&raw_path)
            .map_err(DaemonError::from)
    } else {
        binding
            .layer_path_from_relative(&raw_path)
            .map_err(DaemonError::from)
    }
}

#[cfg(target_os = "linux")]
fn isolated_layer_path(
    handle: &crate::isolated::CommandHandle,
    args: &Value,
) -> Result<LayerPath, DaemonError> {
    let raw_path = require_string(args, "path")?;
    let binding = WorkspaceBinding {
        workspace_root: handle.workspace_root.to_string_lossy().into_owned(),
        layer_stack_root: handle.layer_stack_root.to_string_lossy().into_owned(),
        active_manifest_version: handle.manifest_version,
        active_root_hash: handle.manifest_root_hash.clone(),
        base_manifest_version: handle.manifest_version,
        base_root_hash: handle.manifest_root_hash.clone(),
    };
    let path = if raw_path.starts_with('/') {
        binding.layer_path_from_absolute(&raw_path)?
    } else {
        binding.layer_path_from_relative(&raw_path)?
    };
    LayerPath::parse(&path)
        .map_err(eos_layerstack::LayerStackError::from)
        .map_err(DaemonError::from)
}

#[cfg(target_os = "linux")]
fn isolated_upper_path(handle: &crate::isolated::CommandHandle, layer_path: &LayerPath) -> PathBuf {
    handle.upperdir.join(layer_path.as_str())
}

#[cfg(target_os = "linux")]
fn isolated_read_current(
    handle: &crate::isolated::CommandHandle,
    layer_path: &LayerPath,
) -> Result<(Option<Vec<u8>>, bool), DaemonError> {
    let upper_path = isolated_upper_path(handle, layer_path);
    match std::fs::symlink_metadata(&upper_path) {
        Ok(metadata) if metadata.is_file() => {
            return Ok((Some(std::fs::read(upper_path)?), true));
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            return Ok((
                Some(
                    std::fs::read_link(upper_path)?
                        .to_string_lossy()
                        .as_bytes()
                        .to_vec(),
                ),
                true,
            ));
        }
        Ok(_) => return Ok((None, false)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    MergedView::new(handle.layer_stack_root.clone())
        .read_bytes(layer_path.as_str(), &isolated_manifest(handle))
        .map_err(DaemonError::from)
}

#[cfg(target_os = "linux")]
fn isolated_write_upper(
    handle: &crate::isolated::CommandHandle,
    layer_path: &LayerPath,
    content: &[u8],
) -> Result<(), DaemonError> {
    let path = isolated_upper_path(handle, layer_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, content)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn isolated_manifest(handle: &crate::isolated::CommandHandle) -> Manifest {
    Manifest {
        version: handle.manifest_version,
        schema_version: 1,
        layers: handle
            .layer_paths
            .iter()
            .enumerate()
            .map(|(index, path)| LayerRef {
                layer_id: format!("isolated-{index}"),
                path: isolated_manifest_layer_path(handle, path),
            })
            .collect(),
    }
}

#[cfg(target_os = "linux")]
fn isolated_manifest_layer_path(handle: &crate::isolated::CommandHandle, path: &Path) -> String {
    path.strip_prefix(&handle.layer_stack_root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

#[cfg(target_os = "linux")]
fn isolated_timings(
    verb: &str,
    total_start: Instant,
    changed_path_count: usize,
) -> serde_json::Map<String, Value> {
    let mut timings = serde_json::Map::new();
    timings.insert(
        "resource.command_exec.changed_path_count".to_owned(),
        json!(usize_to_f64_saturating(changed_path_count)),
    );
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    timings
}

#[cfg(target_os = "linux")]
fn isolated_write_response(
    verb: &str,
    layer_path: &LayerPath,
    changed_paths: &[String],
    total_start: Instant,
    applied_edits: Option<i64>,
) -> Value {
    let mut changed_path_kinds = serde_json::Map::new();
    changed_path_kinds.insert(layer_path.as_str().to_owned(), json!("write"));
    let mut response = json!({
        "success": true,
        "workspace": "isolated",
        "workspace_mode": "isolated",
        "changed_paths": changed_paths,
        "changed_path_kinds": Value::Object(changed_path_kinds),
        "mutation_source": "isolated_workspace",
        "status": "committed",
        "conflict": null,
        "conflict_reason": null,
        "error": null,
        "timings": Value::Object(isolated_timings(verb, total_start, 1)),
    });
    if let Some(count) = applied_edits {
        response["applied_edits"] = json!(count);
    }
    response
}

#[cfg(target_os = "linux")]
fn isolated_conflict_response(
    verb: &str,
    layer_path: &LayerPath,
    reason: &str,
    message: &str,
    total_start: Instant,
) -> Value {
    json!({
        "success": false,
        "workspace": "isolated",
        "workspace_mode": "isolated",
        "changed_paths": [],
        "changed_path_kinds": {},
        "mutation_source": "isolated_workspace",
        "status": reason,
        "conflict": {
            "reason": reason,
            "conflict_file": layer_path.as_str(),
            "message": message,
        },
        "conflict_reason": message,
        "error": null,
        "timings": Value::Object(isolated_timings(verb, total_start, 0)),
    })
}

#[cfg(target_os = "linux")]
fn record_isolated_tool_call(
    handle: &crate::isolated::CommandHandle,
    tool_name: &str,
    status: &str,
    changed_paths: &[String],
    total_start: Instant,
) {
    let duration_s = total_start.elapsed().as_secs_f64();
    crate::isolated::record_tool_call(
        &handle.agent_id,
        json!({
            "tool_name": tool_name,
            "workspace_handle_id": handle.workspace_handle_id,
            "argv0": tool_name,
            "exit_code": 0,
            "status": status,
            "changed_paths": changed_paths,
            "published": false,
            "duration_s": duration_s,
            "total_ms": duration_s * 1000.0,
            "phases_ms": {
                "exec": duration_s * 1000.0,
            },
        }),
    );
}

#[cfg(target_os = "linux")]
fn isolated_ns_fds(map: &HashMap<String, i32>) -> Option<NsFds> {
    if map.is_empty() {
        return None;
    }
    Some(NsFds {
        user: map.get("user").copied().map(Fd),
        mnt: map.get("mnt").copied().map(Fd),
        pid: map.get("pid").copied().map(Fd),
        net: map.get("net").copied().map(Fd),
    })
}

fn parse_edits(args: &Value) -> Result<Vec<SearchReplaceEdit>, DaemonError> {
    let edits = args
        .get("edits")
        .and_then(Value::as_array)
        .ok_or_else(|| DaemonError::InvalidEnvelope("edits must be a list".to_owned()))?;
    let mut parsed = Vec::with_capacity(edits.len());
    for raw in edits {
        let edit: SearchReplaceEdit = serde_json::from_value(raw.clone())
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
        if edit.old_text.is_empty() {
            return Err(DaemonError::InvalidEnvelope(
                "edit anchor old_text must be non-empty".to_owned(),
            ));
        }
        parsed.push(edit);
    }
    Ok(parsed)
}

const fn search_replace_message(err: &SearchReplaceError) -> &'static str {
    match err {
        SearchReplaceError::EmptyAnchor => "edit anchor old_text must be non-empty",
        SearchReplaceError::NotFound => "anchor not found",
        SearchReplaceError::CountMismatch => "anchor occurrence count mismatch",
        _ => "edit failed",
    }
}

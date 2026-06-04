//! Workspace file and read-only search op handlers.

use std::path::{Path, PathBuf};
use std::time::Instant;

use eos_layerstack::{require_workspace_binding, LayerStack};
use eos_protocol::{
    apply_search_replace,
    models::{SearchReplaceEdit, MAX_FILE_BYTES, MAX_READ_BYTES},
    Intent, LayerChange, LayerPath, SearchReplaceError,
};
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

#[cfg(target_os = "linux")]
mod isolated_workspace;

/// `api.v1.read_file` — direct `LayerStack` read path.
pub(crate) fn op_read_file(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_workspace::read_file(args, &handle, total_start);
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
        return isolated_workspace::write_file(args, &handle, total_start);
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let layer_path = bound_layer_path(&root, args)?;
    let content = require_raw_string(args, "content")?.into_bytes();
    if content.len() > MAX_FILE_BYTES {
        return Err(DaemonError::InvalidEnvelope(format!(
            "file too large: {} > {} bytes",
            content.len(),
            MAX_FILE_BYTES
        )));
    }
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
        return isolated_workspace::edit_file(args, &handle, total_start);
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

/// `api.v1.glob` — read-only overlay namespace search.
pub(crate) fn op_glob(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_workspace::read_tool(args, "glob", &handle, Instant::now());
    }
    run_overlay_read_tool(args, "glob")
}

/// `api.v1.grep` — read-only overlay namespace content search.
pub(crate) fn op_grep(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::isolated::command_handle_for_args(args) {
        return isolated_workspace::read_tool(args, "grep", &handle, Instant::now());
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
        let _cleanup = RunDirCleanup::new(dirs.run_dir.clone());
        let request = RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: invocation_id.clone(),
                agent_id,
                verb: verb.into(),
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

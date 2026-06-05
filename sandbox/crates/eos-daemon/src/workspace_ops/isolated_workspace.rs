use std::{
    path::{Path, PathBuf},
    time::Instant,
};

use eos_layerstack::{MergedView, WorkspaceBinding};
use eos_protocol::{
    apply_search_replace,
    models::{MAX_FILE_BYTES, MAX_READ_BYTES},
    LayerPath, LayerRef, Manifest,
};
use serde_json::{json, Value};

use super::{parse_edits, search_replace_message};
use crate::{
    error::DaemonError,
    isolated::{record_tool_call, CommandHandle},
    request_args::{require_raw_string, require_string},
    response_timings::{resource_timings, usize_to_f64_saturating, usize_to_i64_saturating},
};

pub(super) fn read_file(
    args: &Value,
    handle: &CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    let read_start = Instant::now();
    let (bytes, exists) = read_current(handle, &layer_path)?;
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

pub(super) fn write_file(
    args: &Value,
    handle: &CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    if !args
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let (_bytes, exists) = read_current(handle, &layer_path)?;
        if exists {
            return Ok(conflict_response(
                "write",
                &layer_path,
                "create_only_existing",
                "file already exists",
                total_start,
            ));
        }
    }
    let content = require_raw_string(args, "content")?.into_bytes();
    if content.len() > MAX_FILE_BYTES {
        return Err(DaemonError::InvalidEnvelope(format!(
            "file too large: {} > {} bytes",
            content.len(),
            MAX_FILE_BYTES
        )));
    }
    write_upper(handle, &layer_path, &content)?;
    let changed_paths = vec![layer_path.as_str().to_owned()];
    record_isolated_tool_call(
        handle,
        "write_file",
        "committed",
        &changed_paths,
        total_start,
    );
    Ok(write_response(
        "write",
        &layer_path,
        &changed_paths,
        total_start,
        None,
    ))
}

pub(super) fn edit_file(
    args: &Value,
    handle: &CommandHandle,
    total_start: Instant,
) -> Result<Value, DaemonError> {
    let layer_path = isolated_layer_path(handle, args)?;
    let edits = parse_edits(args)?;
    let (base_bytes, exists) = read_current(handle, &layer_path)?;
    if !exists {
        return Ok(conflict_response(
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
                return Ok(conflict_response(
                    "edit",
                    &layer_path,
                    "aborted_overlap",
                    search_replace_message(&err),
                    total_start,
                ));
            }
        }
    }
    write_upper(handle, &layer_path, content.as_bytes())?;
    let changed_paths = vec![layer_path.as_str().to_owned()];
    record_isolated_tool_call(
        handle,
        "edit_file",
        "committed",
        &changed_paths,
        total_start,
    );
    Ok(write_response(
        "edit",
        &layer_path,
        &changed_paths,
        total_start,
        Some(usize_to_i64_saturating(edits.len())),
    ))
}

fn isolated_layer_path(handle: &CommandHandle, args: &Value) -> Result<LayerPath, DaemonError> {
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

fn upper_path(handle: &CommandHandle, layer_path: &LayerPath) -> PathBuf {
    handle.upperdir.join(layer_path.as_str())
}

fn read_current(
    handle: &CommandHandle,
    layer_path: &LayerPath,
) -> Result<(Option<Vec<u8>>, bool), DaemonError> {
    let upper_path = upper_path(handle, layer_path);
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

fn write_upper(
    handle: &CommandHandle,
    layer_path: &LayerPath,
    content: &[u8],
) -> Result<(), DaemonError> {
    let path = upper_path(handle, layer_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, content)?;
    Ok(())
}

fn isolated_manifest(handle: &CommandHandle) -> Manifest {
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

fn isolated_manifest_layer_path(handle: &CommandHandle, path: &Path) -> String {
    path.strip_prefix(&handle.layer_stack_root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

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

fn write_response(
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

fn conflict_response(
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

fn record_isolated_tool_call(
    handle: &CommandHandle,
    tool_name: &str,
    status: &str,
    changed_paths: &[String],
    total_start: Instant,
) {
    let duration_s = total_start.elapsed().as_secs_f64();
    record_tool_call(
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

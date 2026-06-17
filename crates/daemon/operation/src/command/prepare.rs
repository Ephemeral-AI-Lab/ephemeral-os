use std::path::{Path, PathBuf};

use linux_namespace_subprocess::protocol::{
    NsFds, RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot,
};
use serde_json::{json, Value};
use workspace::IsolatedWorkspaceBinding;
use workspace::OverlayDirs;

use super::outcome::WorkspaceApiError;
use super::trace::CommandTraceEvent;

pub(crate) struct PreparedCommand {
    pub(crate) run_request: Value,
    pub(crate) request_path: PathBuf,
    pub(crate) output_path: PathBuf,
    pub(crate) final_path: PathBuf,
    pub(crate) transcript_path: PathBuf,
    pub(crate) trace_events: Vec<CommandTraceEvent>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CommandPrepareError {
    pub(crate) error: Box<WorkspaceApiError>,
    pub(crate) trace_events: Vec<CommandTraceEvent>,
}

pub(crate) struct PrepareInputs<'a> {
    pub(crate) caller_id: &'a str,
    pub(crate) command_id: &'a str,
    pub(crate) invocation_id: &'a str,
    pub(crate) cmd: &'a str,
    pub(crate) cwd: Option<&'a Path>,
    pub(crate) remountable: bool,
    pub(crate) timeout_seconds: Option<f64>,
    pub(crate) command_dir: PathBuf,
    pub(crate) workspace_label: &'a str,
}

pub(crate) fn prepare_ephemeral(
    inputs: PrepareInputs<'_>,
    workspace_root: &Path,
    layer_paths: &[PathBuf],
    dirs: &OverlayDirs,
    scratch_run_dir: &Path,
) -> Result<PreparedCommand, CommandPrepareError> {
    let tool_call = tool_call(&inputs);
    let run_request = RunRequest {
        mode: RunMode::FreshNs,
        tool_call,
        workspace_root: WorkspaceRoot(workspace_root.to_path_buf()),
        layer_paths: layer_paths.to_vec(),
        upperdir: Some(dirs.upperdir.clone()),
        workdir: Some(dirs.workdir.clone()),
        ns_fds: None,
        cgroup_path: None,
        timeout_seconds: inputs.timeout_seconds,
    };
    finish_prepare(
        inputs,
        run_request,
        scratch_run_dir.join("command-runner-request.json"),
        scratch_run_dir.join("command-runner-result.json"),
    )
}

pub(crate) fn prepare_isolated(
    inputs: PrepareInputs<'_>,
    binding: &IsolatedWorkspaceBinding,
) -> Result<PreparedCommand, CommandPrepareError> {
    let ns_fds = ns_fds_from_map(&binding.ns_fds);
    let tool_call = tool_call(&inputs);
    let run_request = RunRequest {
        mode: if ns_fds.is_some() {
            RunMode::SetNs
        } else {
            RunMode::FreshNs
        },
        tool_call,
        workspace_root: WorkspaceRoot(binding.workspace_root.clone()),
        layer_paths: binding.layer_paths.clone(),
        upperdir: Some(binding.upperdir.clone()),
        workdir: Some(binding.workdir.clone()),
        ns_fds,
        cgroup_path: binding.cgroup_path.clone(),
        timeout_seconds: inputs.timeout_seconds,
    };
    let request_path = inputs.command_dir.join("runner-request.json");
    let output_path = inputs.command_dir.join("runner-result.json");
    finish_prepare(inputs, run_request, request_path, output_path)
}

fn tool_call(inputs: &PrepareInputs<'_>) -> ToolCall {
    let cwd = inputs
        .cwd
        .map(|path| path.to_string_lossy().into_owned())
        .unwrap_or_else(|| ".".to_owned());
    ToolCall {
        invocation_id: inputs.invocation_id.to_owned(),
        caller_id: inputs.caller_id.to_owned(),
        verb: RunnerVerb::ExecCommand,
        args: json!({
            "command": inputs.cmd,
            "cwd": cwd,
            "remountable": inputs.remountable,
        }),
        background: false,
    }
}

fn finish_prepare(
    inputs: PrepareInputs<'_>,
    run_request: RunRequest,
    request_path: PathBuf,
    output_path: PathBuf,
) -> Result<PreparedCommand, CommandPrepareError> {
    std::fs::create_dir_all(&inputs.command_dir)
        .map_err(|error| prepare_artifact_error("artifact_dir", &inputs.command_dir, error))?;
    let metadata_path = inputs.command_dir.join("metadata.json");
    let metadata_bytes = serde_json::to_vec_pretty(&json!({
        "command_id": inputs.command_id,
        "caller_id": inputs.caller_id,
        "invocation_id": inputs.invocation_id,
        "workspace": inputs.workspace_label,
        "command": inputs.cmd,
        "status": "running",
    }))
    .map_err(|error| prepare_artifact_error("metadata", &metadata_path, error))?;
    std::fs::write(&metadata_path, &metadata_bytes)
        .map_err(|error| prepare_artifact_error("metadata", &metadata_path, error))?;
    let run_request = serde_json::to_value(&run_request)
        .map_err(|error| prepare_artifact_error("runner_request", &request_path, error))?;
    Ok(PreparedCommand {
        run_request,
        request_path,
        output_path,
        final_path: inputs.command_dir.join("final.json"),
        transcript_path: inputs.command_dir.join("transcript.log"),
        trace_events: vec![
            CommandTraceEvent::new(
                "prepared",
                json!({
                    "command_id": inputs.command_id,
                    "workspace": inputs.workspace_label,
                    "artifact_dir": inputs.command_dir.display().to_string(),
                }),
            ),
            CommandTraceEvent::artifact_written("metadata", &metadata_path, metadata_bytes.len()),
        ],
    })
}

fn ns_fds_from_map(map: &std::collections::HashMap<String, i32>) -> Option<NsFds> {
    if map.is_empty() {
        return None;
    }
    let fd = |name: &str| {
        map.get(name)
            .copied()
            .map(linux_namespace_subprocess::protocol::Fd)
    };
    Some(NsFds {
        user: fd("user"),
        mnt: fd("mnt"),
        pid: fd("pid"),
        net: fd("net"),
    })
}

fn prepare_artifact_error(
    artifact: &'static str,
    path: &Path,
    error: impl std::fmt::Display,
) -> CommandPrepareError {
    CommandPrepareError {
        error: Box::new(WorkspaceApiError::new(
            "command_prepare_failed",
            error.to_string(),
        )),
        trace_events: vec![CommandTraceEvent::artifact_failed(artifact, path, error)],
    }
}

#[cfg(test)]
#[path = "../../tests/command/prepare.rs"]
mod tests;

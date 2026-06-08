//! Isolated command-workspace lifecycle as free functions.
//!
//! The daemon's isolated workspace run owns the namespace/lease handle and calls
//! these directly: [`prepare_isolated_command`] builds the set-ns runner request,
//! and [`finalize_isolated_command`] captures the upperdir for AUDIT ONLY. There
//! is NO publish path here — isolated writes are never merged into the shared
//! LayerStack (see the crate-level no-publish guarantee); the upperdir is torn
//! down with the namespace on exit. Cancel skips capture entirely.

use std::collections::HashMap;
use std::path::PathBuf;

use eos_workspace::{
    usize_to_f64_saturating, ChangedPathKinds, FinalizeCommandRequest, PrepareCommandRequest,
    PreparedCommandWorkspace, WorkspaceApiError, WorkspaceCommandOutcome, WorkspaceMode,
    WorkspaceTimings,
};
use eos_protocol::LayerChange;
use serde_json::{json, Value};

/// Daemon-supplied facts needed to prepare an isolated command workspace.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IsolatedCommandPrepareContext {
    pub workspace_handle_id: String,
    pub workspace_root: PathBuf,
    pub scratch_dir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub ns_fds: HashMap<String, i32>,
    pub cgroup_path: Option<PathBuf>,
}

/// Daemon-supplied facts needed to finalize an isolated command workspace.
#[derive(Debug, Clone, PartialEq)]
pub struct IsolatedCommandFinalizeContext {
    pub caller_id: String,
    pub workspace_handle_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub upperdir: PathBuf,
    pub base_timings: WorkspaceTimings,
}

/// Build the set-ns/fresh-ns runner request + session scaffolding for one
/// isolated command session.
///
/// # Errors
///
/// Returns [`WorkspaceApiError`] when the session dir or metadata cannot be
/// created.
pub fn prepare_isolated_command(
    context: IsolatedCommandPrepareContext,
    request: PrepareCommandRequest,
) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
    let mode = if context.ns_fds.is_empty() {
        "fresh_ns"
    } else {
        "set_ns"
    };
    let ns_fds = ns_fds_value(&context.ns_fds);
    let PrepareCommandRequest {
        caller_id,
        command_session_id,
        invocation_id,
        cmd,
        timeout_seconds,
    } = request;
    let session_dir = context
        .scratch_dir
        .join("command-sessions")
        .join(&command_session_id);
    std::fs::create_dir_all(&session_dir).map_err(|error| {
        prepare_error(format!(
            "create command session dir {}: {error}",
            session_dir.display()
        ))
    })?;
    std::fs::write(
        session_dir.join("metadata.json"),
        serde_json::to_vec_pretty(&json!({
            "command_session_id": &command_session_id,
            "caller_id": &caller_id,
            "invocation_id": &invocation_id,
            "workspace": "isolated",
            "workspace_handle_id": &context.workspace_handle_id,
            "command": &cmd,
            "status": "running",
        }))
        .map_err(prepare_error)?,
    )
    .map_err(prepare_error)?;
    let run_request = json!({
        "mode": mode,
        "tool_call": {
            "invocation_id": &invocation_id,
            "caller_id": &caller_id,
            "verb": "exec_command",
            "intent": "write_allowed",
            "args": {
                "command": &cmd,
                "cwd": ".",
            },
            "background": false,
        },
        "workspace_root": context.workspace_root,
        "layer_paths": context.layer_paths,
        "upperdir": context.upperdir,
        "workdir": context.workdir,
        "ns_fds": ns_fds,
        "cgroup_path": context.cgroup_path,
        "timeout_seconds": timeout_seconds,
    });

    Ok(PreparedCommandWorkspace {
        run_request,
        request_path: session_dir.join("runner-request.json"),
        output_path: session_dir.join("runner-result.json"),
        final_path: session_dir.join("final.json"),
        session_dir: session_dir.clone(),
        transcript_path: session_dir.join("transcript.log"),
    })
}

/// Capture the isolated command's upperdir for AUDIT ONLY (never published) and
/// shape the command outcome. The returned outcome carries an `audit` block in
/// `metadata` that the daemon run extracts and records before responding; cancel
/// skips this path entirely so a cancelled command captures nothing.
///
/// # Errors
///
/// Returns [`WorkspaceApiError`] when capturing the upperdir fails.
pub fn finalize_isolated_command(
    context: IsolatedCommandFinalizeContext,
    request: FinalizeCommandRequest,
) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
    let capture_start = std::time::Instant::now();
    let changes = eos_overlay::capture_upperdir(&context.upperdir)
        .map_err(|err| finalize_error(format!("capture isolated upperdir: {err}")))?;
    let capture_s = capture_start.elapsed().as_secs_f64();
    let path_kinds = path_changes_to_wire(&changes);
    let changed_paths: Vec<String> = path_kinds.iter().map(|(path, _)| path.clone()).collect();
    let changed_path_kinds = path_kinds.into_iter().collect::<ChangedPathKinds>();
    let mut timings = context.base_timings;
    merge_runner_timings(&mut timings, request.runner_result.as_ref());
    timings.insert(
        "resource.command_exec.changed_path_count".to_owned(),
        json!(usize_to_f64_saturating(changed_paths.len())),
    );
    timings.insert("command_exec.capture_upperdir_s".to_owned(), json!(capture_s));
    timings.insert("command_exec.occ_apply_s".to_owned(), json!(0.0));
    timings.insert(
        "command_exec.total_s".to_owned(),
        json!(request.command_elapsed_s),
    );
    timings.insert(
        "api.exec_command.total_s".to_owned(),
        json!(request.command_elapsed_s),
    );
    timings.insert(
        "api.exec_command.dispatch_total_s".to_owned(),
        json!(request.command_elapsed_s),
    );
    let command_success = request.command_succeeded();
    let exit_code = request.exit_code.unwrap_or(1);
    let duration_ms = request.command_elapsed_s * 1000.0;
    let status = request.status;
    let command_session_id = request.command_session_id;
    let audit_command_session_id = command_session_id.clone().unwrap_or_default();
    let caller_id = context.caller_id;
    let workspace_handle_id = context.workspace_handle_id;
    let manifest_version = context.manifest_version;
    let manifest_root_hash = context.manifest_root_hash;
    Ok(WorkspaceCommandOutcome {
        mode: WorkspaceMode::Isolated,
        success: command_success,
        status: status.clone(),
        exit_code: Some(exit_code),
        stdout: request.stdout,
        stderr: request.stderr,
        command_session_id,
        changed_paths,
        changed_path_kinds,
        mutation_source: "isolated_workspace".to_owned(),
        conflict: None,
        conflict_reason: None,
        timings,
        metadata: json!({
            "isolated_workspace": {
                "caller_id": caller_id,
                "workspace_handle_id": workspace_handle_id.clone(),
                "manifest_version": manifest_version,
                "manifest_root_hash": manifest_root_hash,
                "published": false,
            },
            "warnings": [],
            "audit": {
                "workspace_handle_id": workspace_handle_id,
                "exit_code": exit_code,
                "argv0": "bash",
                "status": status,
                "published": false,
                "command_session_id": audit_command_session_id,
                "duration_s": request.command_elapsed_s,
                "total_ms": duration_ms,
                "phases_ms": {
                    "exec": duration_ms,
                },
            },
        }),
    })
}

/// Split the `audit` block out of a finalized isolated outcome's metadata,
/// folding the captured `changed_paths` into it. Returns the audit payload the
/// daemon records, leaving `outcome.metadata` without the `audit` key.
#[must_use]
pub fn take_isolated_audit(outcome: &mut WorkspaceCommandOutcome) -> Value {
    let audit = outcome
        .metadata
        .get("audit")
        .cloned()
        .unwrap_or_else(|| json!({}));
    if let Some(metadata) = outcome.metadata.as_object_mut() {
        metadata.remove("audit");
    }
    let changed_paths = json!(outcome.changed_paths);
    merge_changed_paths(audit, changed_paths)
}

fn merge_changed_paths(mut audit: Value, changed_paths: Value) -> Value {
    if let Some(object) = audit.as_object_mut() {
        object.insert("changed_paths".to_owned(), changed_paths);
    }
    audit
}

fn prepare_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("isolated_command_prepare_failed", error.to_string())
}

fn finalize_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("isolated_command_finalize_failed", error.to_string())
}

fn path_changes_to_wire(changes: &[LayerChange]) -> Vec<(String, String)> {
    changes
        .iter()
        .map(|change| (change.path().as_str().to_owned(), change.kind().to_owned()))
        .collect()
}

fn merge_runner_timings(timings: &mut WorkspaceTimings, runner_result: Option<&Value>) {
    let Some(runner_timings) = runner_result
        .and_then(|runner| runner.get("tool_result"))
        .and_then(|tool_result| tool_result.get("timings"))
        .and_then(Value::as_object)
    else {
        return;
    };
    for (key, value) in runner_timings {
        timings.entry(key.clone()).or_insert_with(|| value.clone());
    }
    if let Some(value) = timings.get("workspace.mount_s").cloned() {
        timings
            .entry("command_exec.mount_workspace_s".to_owned())
            .or_insert(value);
    }
    if let Some(value) = timings.get("workspace.tool_s").cloned() {
        timings
            .entry("command_exec.run_command_s".to_owned())
            .or_insert(value);
    }
}

fn ns_fds_value(map: &HashMap<String, i32>) -> Value {
    if map.is_empty() {
        Value::Null
    } else {
        json!({
            "user": namespace_fd(map, "user"),
            "mnt": namespace_fd(map, "mnt"),
            "pid": namespace_fd(map, "pid"),
            "net": namespace_fd(map, "net"),
        })
    }
}

fn namespace_fd(map: &HashMap<String, i32>, name: &str) -> Value {
    map.get(name).map_or(Value::Null, |fd| json!(*fd))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    #[test]
    fn prepare_builds_setns_runner_request_without_publish(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let scratch_dir = std::env::temp_dir().join(format!(
            "eos-isolated-command-prepare-{}",
            std::process::id()
        ));
        let workspace_root = PathBuf::from("/configured-workspace");
        let _ = std::fs::remove_dir_all(&scratch_dir);

        let prepared = prepare_isolated_command(
            IsolatedCommandPrepareContext {
                workspace_handle_id: "iws-1".to_owned(),
                workspace_root: workspace_root.clone(),
                scratch_dir: scratch_dir.clone(),
                layer_paths: vec![PathBuf::from("/lower/a")],
                upperdir: scratch_dir.join("upper"),
                workdir: scratch_dir.join("work"),
                ns_fds: HashMap::from([
                    ("user".to_owned(), 10),
                    ("mnt".to_owned(), 11),
                    ("pid".to_owned(), 12),
                    ("net".to_owned(), 13),
                ]),
                cgroup_path: Some(PathBuf::from("/sys/fs/cgroup/eos/iws-1")),
            },
            PrepareCommandRequest {
                caller_id: "caller-1".to_owned(),
                command_session_id: "cmd-1".to_owned(),
                invocation_id: "inv-1".to_owned(),
                cmd: "pwd".to_owned(),
                timeout_seconds: Some(4.0),
            },
        )?;

        assert_eq!(prepared.run_request["mode"], "set_ns");
        assert_eq!(
            prepared.run_request["workspace_root"],
            workspace_root.to_string_lossy().as_ref()
        );
        assert_eq!(prepared.run_request["ns_fds"]["user"], 10);
        assert_eq!(prepared.run_request["tool_call"]["args"]["command"], "pwd");
        assert_eq!(prepared.run_request["layer_paths"][0], "/lower/a");
        assert_eq!(
            prepared.session_dir,
            scratch_dir.join("command-sessions").join("cmd-1")
        );

        let _ = std::fs::remove_dir_all(scratch_dir);
        Ok(())
    }

    #[test]
    fn finalize_captures_audit_only_changes_without_publish(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let root = std::env::temp_dir().join(format!(
            "eos-isolated-command-finalize-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let upperdir = root.join("upper");
        std::fs::create_dir_all(&upperdir)?;
        std::fs::write(upperdir.join("private.txt"), b"private")?;

        let mut outcome = finalize_isolated_command(
            IsolatedCommandFinalizeContext {
                caller_id: "caller-1".to_owned(),
                workspace_handle_id: "iws-1".to_owned(),
                manifest_version: 7,
                manifest_root_hash: "hash".to_owned(),
                upperdir,
                base_timings: BTreeMap::new(),
            },
            FinalizeCommandRequest {
                runner_result: Some(json!({
                    "tool_result": {"timings": {"workspace.mount_s": 0.1, "workspace.tool_s": 0.2}},
                    "exit_code": 0,
                })),
                command_elapsed_s: 1.25,
                status: "ok".to_owned(),
                exit_code: Some(0),
                stdout: "done".to_owned(),
                stderr: String::new(),
                command_session_id: Some("cmd-1".to_owned()),
            },
        )?;

        assert_eq!(outcome.mode, WorkspaceMode::Isolated);
        assert!(outcome.success);
        assert_eq!(outcome.changed_paths, vec!["private.txt"]);
        assert_eq!(outcome.changed_path_kinds["private.txt"], "write");
        assert_eq!(outcome.timings["command_exec.occ_apply_s"], 0.0);
        assert_eq!(outcome.timings["command_exec.mount_workspace_s"], 0.1);
        assert_eq!(outcome.metadata["isolated_workspace"]["published"], false);

        // The audit block is extractable and carries the captured changed paths.
        let audit = take_isolated_audit(&mut outcome);
        assert_eq!(audit["published"], false);
        assert_eq!(audit["changed_paths"][0], "private.txt");
        assert!(outcome.metadata.get("audit").is_none());

        let _ = std::fs::remove_dir_all(root);
        Ok(())
    }
}

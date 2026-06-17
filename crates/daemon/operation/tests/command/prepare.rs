use linux_namespace_subprocess::protocol::{
    Fd, RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot,
};

use super::*;

#[test]
fn workspace_command_prepares_setns_for_host_and_isolated() -> Result<(), Box<dyn std::error::Error>>
{
    let root = prepare_root("setns-only");
    let host_dirs = overlay_dirs(&root.join("host-run"));
    let host_prepared = prepare_ephemeral(
        prepare_inputs(&root, "host-command", "host"),
        &root.join("workspace"),
        &[root.join("layer")],
        &host_dirs,
        &host_dirs.run_dir,
        Some(host_ns_fds()),
    )
    .expect("host workspace command prepares");
    let host_request: RunRequest = serde_json::from_value(host_prepared.run_request)?;
    assert_eq!(host_request.mode, RunMode::SetNs);
    assert_eq!(host_request.ns_fds.expect("host ns_fds").net, None);

    let isolated_binding = isolated_binding(&root, all_ns_fds_map());
    let isolated_prepared = prepare_isolated(
        prepare_inputs(&root, "isolated-command", "isolated"),
        &isolated_binding,
    )
    .expect("isolated workspace command prepares");
    let isolated_request: RunRequest = serde_json::from_value(isolated_prepared.run_request)?;
    assert_eq!(isolated_request.mode, RunMode::SetNs);
    assert_eq!(
        isolated_request.ns_fds.expect("isolated ns_fds").net,
        Some(Fd(13))
    );

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn workspace_command_missing_holder_fds_fails_without_freshns_fallback(
) -> Result<(), Box<dyn std::error::Error>> {
    let root = prepare_root("missing-holder-fds");
    let host_dirs = overlay_dirs(&root.join("host-run"));

    let host_error = prepare_ephemeral(
        prepare_inputs(&root, "host-missing", "host"),
        &root.join("workspace"),
        &[root.join("layer")],
        &host_dirs,
        &host_dirs.run_dir,
        None,
    )
    .expect_err("host command should reject missing holder fds");
    assert!(host_error
        .error
        .to_string()
        .contains("host workspace command requires setns holder fds"));

    let missing_fds_binding = isolated_binding(&root, std::collections::HashMap::new());
    let isolated_error = prepare_isolated(
        prepare_inputs(&root, "isolated-missing", "isolated"),
        &missing_fds_binding,
    )
    .expect_err("isolated command should reject missing holder fds");
    assert!(isolated_error
        .error
        .to_string()
        .contains("isolated workspace command requires setns holder fds"));

    let mut missing_net = all_ns_fds_map();
    missing_net.remove("net");
    let missing_net_binding = isolated_binding(&root, missing_net);
    let missing_net_error = prepare_isolated(
        prepare_inputs(&root, "isolated-missing-net", "isolated"),
        &missing_net_binding,
    )
    .expect_err("isolated command should require net fd");
    assert!(missing_net_error
        .error
        .to_string()
        .contains("missing holder net namespace fd"));

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn finish_prepare_records_prepared_and_metadata_artifact_events() {
    let root =
        std::env::temp_dir().join(format!("operation-command-prepare-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let command_dir = root.join("command");
    let request_path = root.join("runner-request.json");
    let output_path = root.join("runner-result.json");

    let prepared = finish_prepare(
        PrepareInputs {
            caller_id: "caller",
            command_id: "cmd_prepare",
            invocation_id: "invoke",
            cmd: "echo ok",
            cwd: None,
            remountable: false,
            timeout_seconds: Some(5.0),
            command_dir: command_dir.clone(),
            workspace_label: "isolated",
        },
        RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: "invoke".to_owned(),
                caller_id: "caller".to_owned(),
                verb: RunnerVerb::ExecCommand,
                args: serde_json::json!({"command": "echo ok"}),
                background: false,
            },
            workspace_root: WorkspaceRoot(root.join("workspace")),
            layer_paths: Vec::new(),
            upperdir: None,
            workdir: None,
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: Some(5.0),
        },
        request_path,
        output_path,
    )
    .expect("prepare command");

    assert_eq!(prepared.trace_events.len(), 2);
    assert_eq!(prepared.trace_events[0].name, "prepared");
    assert_eq!(
        prepared.trace_events[0].details["command_id"],
        "cmd_prepare"
    );
    assert_eq!(prepared.trace_events[0].details["workspace"], "isolated");
    assert_eq!(prepared.trace_events[1].name, "artifact_written");
    assert_eq!(prepared.trace_events[1].details["artifact"], "metadata");
    assert_eq!(
        prepared.trace_events[1].details["path"],
        command_dir.join("metadata.json").display().to_string()
    );
    assert!(
        prepared.trace_events[1].details["bytes"]
            .as_u64()
            .expect("metadata byte count")
            > 0
    );

    let metadata =
        std::fs::read_to_string(command_dir.join("metadata.json")).expect("metadata written");
    assert!(metadata.contains("\"command_id\": \"cmd_prepare\""));

    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn finish_prepare_reports_metadata_artifact_write_failure() {
    let root = std::env::temp_dir().join(format!(
        "operation-command-prepare-failure-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let command_dir = root.join("command");
    std::fs::create_dir_all(command_dir.join("metadata.json"))
        .expect("create obstructing metadata directory");

    let error = match finish_prepare(
        PrepareInputs {
            caller_id: "caller",
            command_id: "cmd_prepare",
            invocation_id: "invoke",
            cmd: "echo ok",
            cwd: None,
            remountable: false,
            timeout_seconds: Some(5.0),
            command_dir: command_dir.clone(),
            workspace_label: "isolated",
        },
        RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: "invoke".to_owned(),
                caller_id: "caller".to_owned(),
                verb: RunnerVerb::ExecCommand,
                args: serde_json::json!({"command": "echo ok"}),
                background: false,
            },
            workspace_root: WorkspaceRoot(root.join("workspace")),
            layer_paths: Vec::new(),
            upperdir: None,
            workdir: None,
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: Some(5.0),
        },
        root.join("runner-request.json"),
        root.join("runner-result.json"),
    ) {
        Ok(_) => panic!("metadata path is a directory"),
        Err(error) => error,
    };

    assert_eq!(error.error.kind, "command_prepare_failed");
    assert_eq!(error.trace_events.len(), 1);
    let event = error.trace_events.first().expect("artifact failed event");
    assert_eq!(event.name, "artifact_failed");
    assert_eq!(event.details["artifact"], "metadata");
    assert_eq!(
        event.details["path"],
        command_dir.join("metadata.json").display().to_string()
    );
    assert!(event.details["error"]
        .as_str()
        .is_some_and(|error| !error.is_empty()));

    let _ = std::fs::remove_dir_all(root);
}

fn prepare_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!(
        "operation-command-prepare-{label}-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    root
}

fn prepare_inputs(
    root: &std::path::Path,
    command_id: &'static str,
    label: &'static str,
) -> PrepareInputs<'static> {
    PrepareInputs {
        caller_id: "caller",
        command_id,
        invocation_id: "invoke",
        cmd: "echo ok",
        cwd: None,
        remountable: false,
        timeout_seconds: Some(5.0),
        command_dir: root.join(command_id),
        workspace_label: label,
    }
}

fn overlay_dirs(run_dir: &std::path::Path) -> OverlayDirs {
    OverlayDirs {
        run_dir: run_dir.to_path_buf(),
        upperdir: run_dir.join("upper"),
        workdir: run_dir.join("work"),
    }
}

fn host_ns_fds() -> workspace::network_mode::host::WorkspaceNamespaceFds {
    workspace::network_mode::host::WorkspaceNamespaceFds::from_raw_parts(
        Some(10),
        Some(11),
        Some(12),
        None,
    )
}

fn all_ns_fds_map() -> std::collections::HashMap<String, i32> {
    [("user", 10), ("mnt", 11), ("pid", 12), ("net", 13)]
        .into_iter()
        .map(|(name, fd)| (name.to_owned(), fd))
        .collect()
}

fn isolated_binding(
    root: &std::path::Path,
    ns_fds: std::collections::HashMap<String, i32>,
) -> workspace::network_mode::isolated_network::IsolatedWorkspaceBinding {
    workspace::network_mode::isolated_network::IsolatedWorkspaceBinding {
        caller_id: "caller".to_owned(),
        workspace_handle_id: "workspace-handle".to_owned(),
        layer_stack_root: root.join("stack"),
        manifest_version: 1,
        manifest_root_hash: "root".to_owned(),
        workspace_root: root.join("workspace"),
        scratch_dir: root.join("scratch"),
        upperdir: root.join("upper"),
        workdir: root.join("work"),
        layer_paths: vec![root.join("layer")],
        ns_fds,
        cgroup_path: None,
    }
}
